from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ht_lead_radar import ops as ops_module
from ht_lead_radar.aggregate_adapters.models import AdapterRun
from ht_lead_radar.application import _adapter_metric_counts
from ht_lead_radar.ops import (
    AuditLog,
    AuditPolicyError,
    CollectionIntent,
    CollectionPolicy,
    OpsMetricsStore,
    PublicPersonFact,
    PublicPersonFreshnessPolicy,
    SourcePermission,
    SuppressedEntity,
    SuppressionEntry,
    SuppressionRegistry,
    backup_sqlite,
    build_daily_monitoring_report,
    inspect_cron_entries,
)
from ht_lead_radar.runtime import RunStore
from ht_lead_radar.sources import SourceHealth, SourceHealthStore


UTC = timezone.utc
NOW = datetime(2026, 7, 25, 6, tzinfo=UTC)


def test_suppression_is_identical_across_every_entry_point():
    registry = SuppressionRegistry(
        [
            SuppressionEntry(
                entity_type="company",
                canonical_name="未来火箭科技（上海）有限公司",
                aliases=("未来火箭", "Future Rocket"),
                reason="verified opt-out",
            ),
            SuppressionEntry(
                entity_type="person",
                canonical_name="张三",
                reason="verified opt-out",
            ),
        ],
        clock=lambda: NOW,
    )

    for entry_point in (
        "daily_scan",
        "market_scan",
        "candidate_float",
        "deep_research",
        "view",
        "export",
    ):
        decision = registry.check_company(entry_point, " FUTURE-ROCKET ")
        assert decision.suppressed is True
        assert decision.reason == "verified opt-out"
        with pytest.raises(SuppressedEntity):
            registry.enforce(entry_point, company="未来火箭", people=["张三"])


def test_expired_suppression_no_longer_blocks_and_json_loader(tmp_path):
    path = tmp_path / "suppressions.json"
    path.write_text(
        json.dumps(
            {
                "companies": [
                    {
                        "name": "A公司",
                        "aliases": [],
                        "reason": "temporary",
                        "expires_at": "2026-07-24T00:00:00Z",
                    }
                ],
                "people": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    registry = SuppressionRegistry.from_json(path, clock=lambda: NOW)
    assert registry.check_company("daily_scan", "A公司").suppressed is False


def test_audit_log_records_only_structured_safe_actions_and_exports(tmp_path):
    audit = AuditLog(tmp_path / "audit.sqlite", clock=lambda: NOW)
    for action in ("run", "view", "export", "modify"):
        audit.record(
            actor="openclaw",
            action=action,
            resource_type="lead_report",
            resource_id="report-1",
            run_id="run-1",
            metadata={"result_count": 20, "company_ids": ["c1", "c2"]},
        )

    assert [event.action for event in audit.query(limit=10)] == [
        "modify",
        "export",
        "view",
        "run",
    ]
    # The parent must be deliberately created; output helpers never guess a
    # broad directory.
    (tmp_path / "exports").mkdir()
    export = audit.export_jsonl(
        "events.jsonl", allowed_root=tmp_path / "exports"
    )
    lines = export.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert json.loads(lines[0])["action"] == "run"


@pytest.mark.parametrize(
    "metadata",
    [
        {"api_key": "not-written"},
        {"nested": {"raw_resume": "candidate history"}},
        {"safe_key": "Bearer abcdefghijklmnop"},
        {"candidate_profile": {"name": "someone"}},
        {"provider_token": "short-secret"},
        {"notes": {"document_text": "raw candidate material"}},
    ],
)
def test_audit_log_rejects_secrets_and_resume_material_before_insert(
    tmp_path, metadata
):
    audit = AuditLog(tmp_path / "audit.sqlite")
    with pytest.raises(AuditPolicyError):
        audit.record(
            actor="codex",
            action="run",
            resource_type="analysis",
            resource_id="1",
            metadata=metadata,
        )
    assert audit.query() == []


def test_sqlite_backup_is_integrity_checked_and_restorable(tmp_path):
    source = tmp_path / "source.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE evidence (id INTEGER, title TEXT)")
        connection.execute("INSERT INTO evidence VALUES (1, '融资')")
    backups = tmp_path / "backups"
    backups.mkdir()

    result = backup_sqlite(
        source,
        backups / "daily.sqlite",
        allowed_root=backups,
        clock=lambda: NOW,
    )

    assert result.integrity_check == "ok"
    with sqlite3.connect(result.target) as restored:
        assert restored.execute("SELECT * FROM evidence").fetchall() == [
            (1, "融资")
        ]
    with pytest.raises(FileExistsError):
        backup_sqlite(source, result.target, allowed_root=backups)
    with pytest.raises(ValueError, match="inside"):
        backup_sqlite(source, tmp_path / "escape.sqlite", allowed_root=backups)


def test_cron_inspector_requires_daily_five_am_entry():
    expected = (
        "0 5 * * * /home/admin/.openclaw/workspace/skills/"
        "hardtech-lead-radar/scripts/run_daily_fixed_sources.sh"
    )
    status = inspect_cron_entries(
        ["# comment", expected],
        command_marker="run_daily_fixed_sources.sh",
    )
    assert status["configured"] is True
    wrong = inspect_cron_entries(
        [expected.replace("0 5", "0 6")],
        command_marker="run_daily_fixed_sources.sh",
    )
    assert wrong["configured"] is False
    assert wrong["conflicting_entries"]


def test_daily_monitor_report_fans_in_all_critical_operational_signals(tmp_path):
    runtime_path = tmp_path / "runtime.sqlite"
    runtime = RunStore(runtime_path, clock=lambda: NOW - timedelta(hours=40))
    run = runtime.ensure_run("failed-daily", {"direction": "脑机接口"})
    runtime.set_run_state(
        run.run_id,
        "failed",
        current_stage="score",
        error="score error",
    )
    attempt = runtime.start_checkpoint(
        run.run_id, "score", {}, costly=False, replay=False
    )
    runtime.fail_checkpoint(run.run_id, "score", attempt, "score error")

    health_path = tmp_path / "health.sqlite"
    SourceHealthStore(health_path).upsert(
        SourceHealth(
            source_id="source-a",
            last_success=(NOW - timedelta(days=1)).isoformat(),
            parse_yield=0,
            consecutive_failures=3,
            updated_at=NOW.isoformat(),
        )
    )

    metrics_path = tmp_path / "ops.sqlite"
    metrics = OpsMetricsStore(metrics_path)
    metrics.record_run(
        "failed-daily",
        recorded_at=NOW,
        status="failed",
        result_count=0,
        metaso_points=500,
    )
    for index in range(3):
        metrics.record_source(
            f"run-{index}",
            "source-a",
            recorded_at=NOW - timedelta(days=index),
            ok=True,
            yield_count=0,
        )

    report = build_daily_monitoring_report(
        runtime_db=runtime_path,
        source_health_db=health_path,
        ops_metrics_db=metrics_path,
        cron_entries=[
            "0 6 * * * /opt/radar/scripts/run_daily_fixed_sources.sh"
        ],
        cron_command_marker="run_daily_fixed_sources.sh",
        now=NOW,
    )

    assert report.status == "critical"
    assert report.suggested_exit_code == 2
    codes = {issue.code for issue in report.issues}
    assert {
        "cron_missing_or_wrong_time",
        "latest_run_failed",
        "latest_run_stale",
        "checkpoint_failed",
        "source_consecutive_failures",
        "zero_results",
        "metaso_budget_exhausted",
        "source_consecutive_zero_output",
    }.issubset(codes)
    assert report.to_dict()["metrics"]["metaso_budget"]["used"] == 500


def test_monitor_reads_source_pack_run_schema_and_failure_streaks(tmp_path):
    health_path = tmp_path / "source-packs.sqlite"
    with sqlite3.connect(health_path) as connection:
        connection.executescript(
            """
            CREATE TABLE source_pack_source_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                discovered_count INTEGER NOT NULL DEFAULT 0,
                observation_count INTEGER NOT NULL DEFAULT 0,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                detail_error_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            );
            """
        )
        connection.execute(
            """
            INSERT INTO source_pack_source_runs (
                source_id, topic, started_at, finished_at, status,
                observation_count, evidence_count, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "source-a",
                "脑机接口",
                (NOW - timedelta(days=4)).isoformat(),
                (NOW - timedelta(days=4)).isoformat(),
                "ok",
                2,
                1,
                "",
            ),
        )
        for days in (3, 2, 1):
            connection.execute(
                """
                INSERT INTO source_pack_source_runs (
                    source_id, topic, started_at, finished_at, status, error
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "source-a",
                    "脑机接口",
                    (NOW - timedelta(days=days)).isoformat(),
                    (NOW - timedelta(days=days)).isoformat(),
                    "error",
                    "timeout",
                ),
            )
        connection.execute(
            """
            INSERT INTO source_pack_source_runs (
                source_id, topic, started_at, finished_at, status,
                observation_count, evidence_count, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "source-b",
                "脑机接口",
                NOW.isoformat(),
                NOW.isoformat(),
                "ok",
                0,
                0,
                "",
            ),
        )

    report = build_daily_monitoring_report(source_health_db=health_path, now=NOW)

    codes = {issue.code for issue in report.issues}
    assert "source_health_schema_missing" not in codes
    assert "source_consecutive_failures" in codes
    assert "source_consecutive_empty_discovery" not in codes
    assert report.metrics["sources"]["schema"] == "source_pack_source_runs"
    assert report.metrics["sources"]["count"] == 2
    assert report.metrics["sources"]["unhealthy"][0]["source_id"] == "source-a"
    healthy = next(
        item
        for item in report.metrics["sources"]["adapters"]
        if item["source_id"] == "source-b"
    )
    assert healthy["error_class"] == ""
    assert healthy["detail"] == ""

    with sqlite3.connect(health_path) as connection:
        for days in (1, 2):
            timestamp = (NOW - timedelta(days=days)).isoformat()
            connection.execute(
                """
                INSERT INTO source_pack_source_runs (
                    source_id, topic, started_at, finished_at, status,
                    discovered_count, observation_count, evidence_count, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "source-b",
                    "脑机接口",
                    timestamp,
                    timestamp,
                    "ok",
                    0,
                    0,
                    0,
                    "",
                ),
            )

    repeated_report = build_daily_monitoring_report(
        source_health_db=health_path, now=NOW
    )
    repeated_codes = {issue.code for issue in repeated_report.issues}
    assert "source_consecutive_empty_discovery" in repeated_codes


def test_monitor_merges_legacy_and_source_pack_health_when_both_schemas_exist(
    tmp_path,
):
    health_path = tmp_path / "mixed-health.sqlite"
    SourceHealthStore(health_path).upsert(
        SourceHealth(
            source_id="legacy-only",
            last_success=NOW.isoformat(),
            parse_yield=0,
            consecutive_failures=0,
            updated_at=NOW.isoformat(),
        )
    )
    SourceHealthStore(health_path).upsert(
        SourceHealth(
            source_id="shared-source",
            last_success=(NOW - timedelta(days=5)).isoformat(),
            parse_yield=4,
            consecutive_failures=4,
            updated_at=NOW.isoformat(),
        )
    )
    with sqlite3.connect(health_path) as connection:
        connection.executescript(
            """
            CREATE TABLE source_pack_source_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                discovered_count INTEGER NOT NULL DEFAULT 0,
                observation_count INTEGER NOT NULL DEFAULT 0,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                detail_error_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            );
            """
        )
        for source_id in ("shared-source", "pack-only"):
            for days in (2, 1, 0):
                timestamp = (NOW - timedelta(days=days)).isoformat()
                connection.execute(
                    """
                    INSERT INTO source_pack_source_runs (
                        source_id, topic, started_at, finished_at, status,
                        discovered_count, observation_count, evidence_count, error
                    ) VALUES (?, '硬科技', ?, ?, 'ok', 0, 0, 0, '')
                    """,
                    (source_id, timestamp, timestamp),
                )

    report = build_daily_monitoring_report(source_health_db=health_path, now=NOW)

    sources = report.metrics["sources"]
    assert sources["schema"] == "mixed"
    assert sources["schemas"] == ["source_health", "source_pack_source_runs"]
    assert sources["count"] == 3
    by_id = {item["source_id"]: item for item in sources["adapters"]}
    assert set(by_id) == {"legacy-only", "shared-source", "pack-only"}
    assert by_id["shared-source"]["consecutive_failures"] == 4
    assert by_id["shared-source"]["consecutive_empty_discoveries"] == 3
    codes = {issue.code for issue in report.issues}
    assert "source_consecutive_failures" in codes
    assert "source_consecutive_empty_discovery" in codes


def test_monitor_detects_result_count_regression_without_demanding_twenty(tmp_path):
    metrics = OpsMetricsStore(tmp_path / "ops.sqlite")
    for index, count in enumerate((12, 11, 10, 3)):
        metrics.record_run(
            f"run-{index}",
            recorded_at=NOW - timedelta(days=3 - index),
            status="completed",
            result_count=count,
        )
    report = build_daily_monitoring_report(
        ops_metrics_db=tmp_path / "ops.sqlite",
        now=NOW,
    )
    codes = {issue.code for issue in report.issues}
    assert "result_count_drop" in codes
    assert "too_few_results" not in codes


def test_ops_metrics_migrates_legacy_not_null_yield_without_losing_rows(tmp_path):
    database = tmp_path / "ops.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE ops_source_metrics (
                run_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                ok INTEGER NOT NULL,
                yield_count INTEGER NOT NULL,
                PRIMARY KEY (run_id, source_id)
            );
            INSERT INTO ops_source_metrics VALUES (
                'legacy-run', 'legacy-source', '2026-08-30T00:00:00+00:00', 1, 4
            );
            INSERT INTO ops_source_metrics VALUES (
                'legacy-failed-run', 'legacy-failed-source',
                '2026-08-30T01:00:00+00:00', 0, 0
            );
            """
        )

    metrics = OpsMetricsStore(database)
    metrics.record_source(
        "new-run",
        "36kr-finance",
        recorded_at=NOW,
        ok=False,
        yield_count=None,
        status="partial",
        discovered_count=22,
        incremental_count=7,
        detail_success_count=5,
        detail_error_count=2,
        semantic_attempt_count=4,
        semantic_accepted_count=3,
        semantic_prefiltered_count=1,
        semantic_failure_count=1,
        open_dead_letter_count=2,
        listing_count=20,
        evidence_count=19,
        detail_failure_count=2,
        rule_event_count=4,
        minimax_event_count=16,
        prefiltered_count=1,
        adaptive_used_count=0,
        omissions_detected=0,
        error_class="AdapterTimeout",
    )

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        yield_column = next(
            row
            for row in connection.execute(
                "PRAGMA table_info(ops_source_metrics)"
            ).fetchall()
            if row[1] == "yield_count"
        )
        legacy = connection.execute(
            "SELECT yield_count, status FROM ops_source_metrics WHERE run_id='legacy-run'"
        ).fetchone()
        legacy_failed = connection.execute(
            "SELECT ok, status FROM ops_source_metrics "
            "WHERE run_id='legacy-failed-run'"
        ).fetchone()
        current = connection.execute(
            "SELECT * FROM ops_source_metrics WHERE run_id='new-run'"
        ).fetchone()
    assert yield_column[3] == 0
    assert legacy["yield_count"] == 4
    assert legacy["status"] == "ok"
    assert legacy_failed["ok"] == 0
    assert legacy_failed["status"] == "error"
    assert current["yield_count"] is None
    assert current["status"] == "partial"
    assert current["incremental_count"] == 7
    assert current["detail_success_count"] == 5
    assert current["semantic_attempt_count"] == 4
    assert current["semantic_accepted_count"] == 3
    assert current["semantic_prefiltered_count"] == 1
    assert current["semantic_failure_count"] == 1
    assert current["open_dead_letter_count"] == 2
    assert current["listing_count"] == 20
    assert current["evidence_count"] == 19
    assert current["detail_failure_count"] == 2
    assert current["rule_event_count"] == 4
    assert current["minimax_event_count"] == 16
    assert current["prefiltered_count"] == 1
    assert current["adaptive_used_count"] == 0
    assert current["omissions_detected"] == 0


def test_partial_and_error_adapter_metrics_are_never_reported_healthy(tmp_path):
    metrics = OpsMetricsStore(tmp_path / "ops.sqlite")
    metrics.record_source(
        "run-partial",
        "partial-source",
        recorded_at=NOW,
        ok=True,
        yield_count=2,
        status="partial",
        error_class="PartialFetch",
    )
    metrics.record_source(
        "run-error",
        "error-source",
        recorded_at=NOW,
        ok=False,
        yield_count=None,
        status="error",
        error_class="AdapterError",
    )

    report = build_daily_monitoring_report(
        ops_metrics_db=tmp_path / "ops.sqlite",
        now=NOW,
    )

    latest = {
        item["source_id"]: item for item in report.metrics["adapter_health"]
    }
    assert latest["partial-source"]["status"] == "partial"
    assert latest["partial-source"]["ok"] == 0
    assert latest["partial-source"]["yield_count"] == 2
    assert latest["error-source"]["status"] == "error"
    assert latest["error-source"]["yield_count"] is None
    issues = {
        (issue.code, issue.severity, issue.details.get("source_id"))
        for issue in report.issues
    }
    assert ("adapter_latest_nonhealthy", "warning", "partial-source") in issues
    assert ("adapter_latest_nonhealthy", "critical", "error-source") in issues


def test_resumed_source_update_becomes_latest_without_rowid_reinsertion(
    tmp_path, monkeypatch
):
    update_times = iter(
        (
            NOW - timedelta(minutes=3),
            NOW - timedelta(minutes=2),
            NOW - timedelta(minutes=1),
        )
    )
    monkeypatch.setattr(ops_module, "_utc_now", lambda: next(update_times))
    database = tmp_path / "ops-resume.sqlite"
    metrics = OpsMetricsStore(database)
    metrics.record_source(
        "resumable-run",
        "36kr-finance",
        recorded_at=NOW - timedelta(hours=1),
        ok=True,
        yield_count=4,
        status="ok",
    )
    metrics.record_source(
        "later-inserted-run",
        "36kr-finance",
        recorded_at=NOW,
        ok=True,
        yield_count=5,
        status="ok",
    )
    metrics.record_source(
        "resumable-run",
        "36kr-finance",
        recorded_at=NOW - timedelta(hours=1),
        ok=False,
        yield_count=None,
        status="error",
        error_class="ResumeValidationError",
    )

    report = build_daily_monitoring_report(ops_metrics_db=database, now=NOW)

    latest = report.metrics["adapter_health"]
    assert len(latest) == 1
    assert latest[0]["status"] == "error"
    assert latest[0]["error_class"] == "ResumeValidationError"
    assert any(
        issue.code == "adapter_latest_nonhealthy"
        and issue.severity == "critical"
        for issue in report.issues
    )


def test_monitor_reads_pre_updated_at_ops_schema_before_first_writer_migration(
    tmp_path,
):
    database = tmp_path / "ops-pre-migration.sqlite"
    metrics = OpsMetricsStore(database)
    metrics.record_source(
        "legacy-run",
        "legacy-source",
        recorded_at=NOW,
        ok=False,
        yield_count=None,
        status="error",
        error_class="LegacySourceError",
    )
    with sqlite3.connect(database) as connection:
        connection.execute("DROP INDEX ops_source_metrics_source_updated")
        connection.execute("ALTER TABLE ops_source_metrics DROP COLUMN updated_at")

    report = build_daily_monitoring_report(ops_metrics_db=database, now=NOW)

    assert report.metrics["adapter_health"][0]["source_id"] == "legacy-source"
    assert report.metrics["adapter_health"][0]["status"] == "error"


def test_native_adapter_counters_are_persisted_without_inventing_other_stages(
    tmp_path,
):
    native_run = AdapterRun(
        adapter_id="kr36",
        source_id="36kr-finance",
        started_at=NOW.isoformat(),
        finished_at=NOW.isoformat(),
        status="partial",
        listing_count=20,
        incremental_count=18,
        detail_success_count=17,
        detail_failure_count=1,
        rule_event_count=4,
        minimax_event_count=16,
        evidence_count=19,
        adaptive_used_count=0,
        semantic_failure_count=1,
        prefiltered_count=2,
        omissions_detected=0,
        error="one detail failed",
    ).to_dict()
    database = tmp_path / "ops.sqlite"
    OpsMetricsStore(database).record_source(
        "run-native",
        native_run["source_id"],
        recorded_at=NOW,
        ok=False,
        yield_count=native_run["evidence_count"],
        status=native_run["status"],
        **_adapter_metric_counts(native_run),
    )

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        stored = connection.execute(
            "SELECT * FROM ops_source_metrics WHERE run_id='run-native'"
        ).fetchone()

    assert stored["listing_count"] == 20
    assert stored["minimax_event_count"] == 16
    assert stored["discovered_count"] is None
    assert stored["observation_count"] is None
    assert stored["semantic_attempt_count"] is None
    assert stored["semantic_accepted_count"] is None


def test_negative_optional_source_counter_is_rejected(tmp_path):
    metrics = OpsMetricsStore(tmp_path / "ops.sqlite")
    with pytest.raises(ValueError, match="non-negative"):
        metrics.record_source(
            "run",
            "source",
            recorded_at=NOW,
            ok=False,
            yield_count=None,
            status="partial",
            semantic_failure_count=-1,
        )


def test_public_person_facts_are_marked_expired_not_deleted():
    policy = PublicPersonFreshnessPolicy()
    current_role = policy.assess(
        PublicPersonFact(
            person_id="p1",
            fact_type="current_role",
            observed_at=(NOW - timedelta(days=91)).isoformat(),
        ),
        as_of=NOW,
    )
    history = policy.assess(
        PublicPersonFact(
            person_id="p1",
            fact_type="historical_employment",
            observed_at=(NOW - timedelta(days=3000)).isoformat(),
        ),
        as_of=NOW,
    )
    unknown = policy.assess(
        PublicPersonFact("p2", "current_role", observed_at=None),
        as_of=NOW,
    )

    assert current_role.status == "expired"
    assert current_role.requires_refresh is True
    assert history.status == "historical"
    assert history.requires_refresh is False
    assert unknown.status == "unknown"


def test_collection_policy_never_bypasses_controls_or_keeps_private_contacts():
    policy = CollectionPolicy()
    blocked = policy.evaluate(
        SourcePermission(
            access_basis="public_web",
            publicly_accessible=True,
            bypasses_access_control=True,
        ),
        CollectionIntent(("company_name",)),
    )
    assert blocked.allowed is False
    assert "access_control_bypass_forbidden" in blocked.reasons

    facts_only = policy.enforce(
        SourcePermission(
            access_basis="public_web",
            publicly_accessible=True,
            license_category="unknown",
            full_text_retention_allowed=False,
            max_retention_days=7,
        ),
        CollectionIntent(
            (
                "company_name",
                "professional_role",
                "personal_email",
                "mobile_phone",
            ),
            retain_full_text=True,
            requested_retention_days=30,
        ),
    )
    assert facts_only.allowed is True
    assert facts_only.collect_fields == ("company_name", "professional_role")
    assert set(facts_only.excluded_fields) == {
        "personal_email",
        "mobile_phone",
    }
    assert facts_only.retention_mode == "metadata_and_extracted_facts"
    assert facts_only.retention_days == 0
    assert "full_text_retention_downgraded" in facts_only.reasons


def test_collection_policy_permits_bounded_full_text_only_when_licensed():
    decision = CollectionPolicy().enforce(
        SourcePermission(
            access_basis="licensed_feed",
            publicly_accessible=False,
            authorized=True,
            license_category="licensed",
            full_text_retention_allowed=True,
            max_retention_days=30,
        ),
        CollectionIntent(
            ("title", "url"),
            retain_full_text=True,
            requested_retention_days=90,
        ),
    )
    assert decision.allowed is True
    assert decision.retention_mode == "full_text"
    assert decision.retention_days == 30
    assert "retention_period_capped" in decision.reasons
