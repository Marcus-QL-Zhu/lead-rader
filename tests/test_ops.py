from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

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
