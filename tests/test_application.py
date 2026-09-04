import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ht_lead_radar.application import (
    DEFAULTS,
    LeadRadarApplication,
    _adapter_metric_counts,
    apply_defaults,
    default_idempotency_key,
)
from ht_lead_radar.models import CompanyLead, Evidence
from ht_lead_radar.product_clock import product_date
from ht_lead_radar.requests import plan_opportunity_request
from ht_lead_radar.runtime import StageExecutionError
from ht_lead_radar.talent_pool_store import TalentPoolStore


def _payload(tmp_path):
    plan = plan_opportunity_request("最近灵巧手有哪些公司可能招总监以上？")
    return {
        "command": "ask",
        "direction": "灵巧手",
        "request_plan": plan.to_dict(),
        "demo": True,
        "runtime_db": str(tmp_path / "runtime.sqlite"),
        "fact_db": str(tmp_path / "facts.sqlite"),
        "relationship_db": str(tmp_path / "relationships.sqlite"),
        "budget_db": str(tmp_path / "budget.sqlite"),
        "source_state_db": str(tmp_path / "sources.sqlite"),
        "feishu_state_db": str(tmp_path / "feishu.sqlite"),
        "audit_db": str(tmp_path / "audit.sqlite"),
        "output_dir": str(tmp_path / "reports"),
        "metaso_verify_limit": 0,
    }


def test_product_date_uses_shanghai_calendar_day():
    assert product_date(datetime(2026, 9, 4, 16, 30, tzinfo=timezone.utc)) == date(
        2026, 9, 5
    )


def test_demo_application_is_checkpointed_and_report_is_traceable(tmp_path):
    payload = _payload(tmp_path)
    app = LeadRadarApplication(payload["runtime_db"])
    key = default_idempotency_key(payload)

    first = app.run(payload, key)
    second = app.run(payload, key)

    assert first.lead_count == 3
    assert second.runtime.reused_stages
    report = json.loads(open(first.output["json_path"], encoding="utf-8").read())
    assert report["manifest"]["request_plan"]["request"]["raw_text"]
    assert all(item["event_id"] for item in report["leads"][0]["evidence"])


def test_application_never_persists_candidate_profile_in_fact_store(tmp_path):
    plan = plan_opportunity_request("我有一位数据采集总监候选人，哪些公司可能会要他？")
    payload = _payload(tmp_path)
    payload["command"] = "float"
    payload["request_plan"] = plan.to_dict()
    app = LeadRadarApplication(payload["runtime_db"])
    result = app.run(payload, default_idempotency_key(payload))

    database_bytes = (tmp_path / "facts.sqlite").read_bytes()
    assert "candidate_profile".encode() not in database_bytes
    envelope = json.loads(open(result.output["json_path"], encoding="utf-8").read())
    # The manifest preserves the request interpretation, but the Candidate
    # Float result itself explicitly stores no candidate object.
    assert all(
        not item.get("candidate_profile_persisted")
        for item in envelope["float_matches"]
    )


def test_float_candidate_marker_is_absent_from_persistent_outputs(tmp_path):
    marker = "CANDIDATE_SECRET_MARKER_7F31A9"
    plan = plan_opportunity_request(
        f"我有一位数据采集总监候选人，内部备注 {marker}，哪些公司可能会要他？"
    )
    payload = _payload(tmp_path)
    payload["command"] = "float"
    payload["candidate"] = marker
    payload["question"] = f"Float private question: {marker}"
    payload["raw_request"] = f"Float private request: {marker}"
    payload["request_plan"] = plan.to_dict()
    app = LeadRadarApplication(payload["runtime_db"])

    result = app.run(payload, default_idempotency_key(payload))

    marker_bytes = marker.encode("utf-8")
    persistent_paths = [
        tmp_path / "runtime.sqlite",
        tmp_path / "facts.sqlite",
        tmp_path / "relationships.sqlite",
        tmp_path / "feishu.sqlite",
        tmp_path / "feishu-change-set.json",
    ]
    for path in persistent_paths:
        if path.exists():
            assert marker_bytes not in path.read_bytes(), path

    envelope = json.loads(open(result.output["json_path"], encoding="utf-8").read())
    manifest_text = json.dumps(
        envelope["manifest"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert marker not in manifest_text
    assert marker not in json.dumps(envelope, ensure_ascii=False, sort_keys=True)


def test_child_scan_can_skip_feishu_projection_entirely(tmp_path):
    payload = _payload(tmp_path)
    payload["skip_feishu_projection"] = True
    result = LeadRadarApplication(payload["runtime_db"]).run(
        payload,
        default_idempotency_key(payload, refresh=True),
    )

    assert result.output["feishu"]["mode"] == "skipped"
    assert not (tmp_path / "feishu.sqlite").exists()
    assert not (tmp_path / "feishu-change-set.json").exists()


def test_skip_feishu_projection_has_distinct_idempotency_key(tmp_path):
    ordinary = _payload(tmp_path)
    child = dict(ordinary)
    child["skip_feishu_projection"] = True

    assert default_idempotency_key(ordinary) != default_idempotency_key(child)


def test_source_topics_are_part_of_idempotency_and_default_to_direction(tmp_path):
    ordinary = _payload(tmp_path)
    broad = dict(ordinary)
    broad["direction"] = "硬科技组合"
    broad["source_topics"] = "具身智能|半导体"

    assert default_idempotency_key(ordinary) != default_idempotency_key(broad)


def test_daily_cooldown_and_candidate_pool_are_part_of_idempotency(tmp_path):
    ordinary = _payload(tmp_path)
    cooled = dict(ordinary, daily_cooldown=True, candidate_pool_size=60)
    differently_sized = dict(cooled, candidate_pool_size=40)

    assert default_idempotency_key(ordinary) != default_idempotency_key(cooled)
    assert default_idempotency_key(cooled) != default_idempotency_key(differently_sized)


def test_run_date_is_strictly_canonical_before_idempotency_and_collection(tmp_path):
    payload = _payload(tmp_path)
    compact = dict(payload, run_date="20260905")
    canonical = dict(payload, run_date="2026-09-05")

    assert apply_defaults(canonical)["run_date"] == "2026-09-05"
    for invalid in (compact, dict(payload, run_date="not-a-date")):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            default_idempotency_key(invalid)
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            apply_defaults(invalid)


def test_daily_application_applies_delivery_cooldown_before_all_published_outputs(
    tmp_path,
):
    direction = "hardtech"
    today = date.today().isoformat()
    replay_leads = []
    for index in range(1, 31):
        company = f"CN-Robot-{index:02d}"
        evidence = Evidence(
            company=company,
            event_type="merger_acquisition",
            phase="strategy_capital",
            event_date=today,
            title=f"{company} appoints a new China business president",
            snippet="Leadership transition creates a new organization mandate.",
            source_url=f"https://example.cn/capacity/{index}",
            source_name="test-source",
            source_grade="B",
            direction=direction,
        )
        replay_leads.append(
            CompanyLead(
                company=company,
                direction=direction,
                score=0,
                confidence_grade="C",
                timing_stage="pre_ad",
                target_roles=["China Strategy Director"],
                hiring_thesis="test",
                evidence=[evidence],
                outreach_routes=[],
            ).to_dict()
        )
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(
        json.dumps(
            {"manifest": {"as_of": today}, "leads": replay_leads},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    talent_store = TalentPoolStore(tmp_path / "talent.sqlite")
    talent_store.save_bundle(
        {
            "run_date": today,
            "direction": direction,
            "source_run_id": "prior-delivered-report",
            "generation_provider": "test",
            "generation_model": "",
            "generation_error": "",
            "drafts": [],
            "final_report_opportunities": [
                {
                    "company": f"CN-Robot-{index:02d}",
                    "score": 1,
                    "role_hypotheses": ["China Strategy Director"],
                    "evidence_urls": [f"https://example.cn/capacity/{index}"],
                }
                for index in range(1, 11)
            ],
        }
    )
    prior = talent_store.current_bundle(today, direction)
    talent_store.record_delivery(
        prior["_snapshot_id"], channel="feishu_fallback", status="delivered"
    )

    payload = _payload(tmp_path)
    payload.update(
        {
            "direction": direction,
            "demo": False,
            "replay_json": str(replay_path),
            "top": 20,
            "candidate_pool_size": 30,
            "daily_cooldown": True,
            "talent_state_db": str(talent_store.database),
            "ops_metrics_db": str(tmp_path / "ops.sqlite"),
            "skip_feishu_projection": True,
        }
    )

    result = LeadRadarApplication(payload["runtime_db"]).run(
        payload,
        default_idempotency_key(payload, refresh=True),
    )

    assert result.lead_count == 20
    report = json.loads(open(result.output["json_path"], encoding="utf-8").read())
    companies = [item["company"] for item in report["leads"]]
    assert companies == [f"CN-Robot-{index:02d}" for index in range(11, 31)]
    segments = report["daily_opportunity_segments"]
    assert segments["input_company_count"] == 30
    assert segments["selected_company_count"] == 20
    assert segments["suppressed_company_count"] == 10
    assert report["manifest"]["daily_cooldown_applied"] is True
    assert report["manifest"]["policy"]["target_count"] == 20
    assert report["manifest"]["policy"]["candidate_pool_size"] == 30
    assert report["manifest"]["daily_opportunity_segments"] == segments
    with sqlite3.connect(tmp_path / "ops.sqlite") as connection:
        result_count = connection.execute(
            "SELECT result_count FROM ops_run_metrics"
        ).fetchone()[0]
    assert result_count == 20


def test_candidate_pool_never_expands_the_final_report_without_cooldown(tmp_path):
    direction = "hardtech"
    today = date.today().isoformat()
    replay_path = tmp_path / "oversupply.json"
    replay_path.write_text(
        json.dumps(
            {
                "manifest": {"as_of": today},
                "leads": [
                    CompanyLead(
                        company=f"CN-Chip-{index:02d}",
                        direction=direction,
                        score=0,
                        confidence_grade="C",
                        timing_stage="pre_ad",
                        target_roles=["Operations Director"],
                        hiring_thesis="test",
                        evidence=[
                            Evidence(
                                company=f"CN-Chip-{index:02d}",
                                event_type="merger_acquisition",
                                phase="strategy_capital",
                                event_date=today,
                                title="New operating mandate",
                                snippet="The company is expanding production.",
                                source_url=f"https://example.cn/chip/{index}",
                                source_name="test-source",
                                source_grade="B",
                                direction=direction,
                            )
                        ],
                        outreach_routes=[],
                    ).to_dict()
                    for index in range(1, 26)
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    payload = _payload(tmp_path)
    payload.update(
        {
            "direction": direction,
            "demo": False,
            "replay_json": str(replay_path),
            "top": 20,
            "candidate_pool_size": 25,
            "daily_cooldown": False,
            "skip_feishu_projection": True,
        }
    )

    result = LeadRadarApplication(payload["runtime_db"]).run(
        payload,
        default_idempotency_key(payload, refresh=True),
    )

    report = json.loads(Path(result.output["json_path"]).read_text(encoding="utf-8"))
    assert result.lead_count == 20
    assert len(report["leads"]) == 20


def test_source_pack_outer_status_preserves_inner_partial_and_error(
    tmp_path, monkeypatch
):
    from ht_lead_radar import source_pack_collector

    class FakeCollector:
        next_status = "partial"
        next_items = []

        def __init__(self, **_kwargs):
            self.last_run_summary = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def collect(self, *_args, **_kwargs):
            self.last_run_summary = {
                "sources": {
                    "aggregate-source": {
                        "status": self.next_status,
                        "evidence_count": len(self.next_items),
                        "error": (
                            "AdapterError: Authorization: Bearer "
                            "application-secret token=second-secret"
                        ),
                    }
                },
                "dedicated_aggregate": {},
            }
            return list(self.next_items)

        def source_health_summary(self):
            return {"failed_count": int(self.next_status == "error")}

    monkeypatch.setattr(source_pack_collector, "SourcePackCollector", FakeCollector)
    context = SimpleNamespace(effect_once=lambda _key, operation: operation("token"))
    app = LeadRadarApplication(tmp_path / "runtime.sqlite")
    payload = {
        "fixed_sources": str(tmp_path / "missing-fixed.json"),
        "source_packs": str(tmp_path / "source-packs.json"),
        "source_state_db": str(tmp_path / "sources.sqlite"),
        "limit_per_query": 8,
    }
    FakeCollector.next_items = [
        Evidence(
            company="CN-Robot",
            event_type="executive_change",
            phase="strategy_capital",
            event_date=date.today().isoformat(),
            title="New president",
            snippet="Leadership transition",
            source_url="https://example.cn/news/1",
            source_name="aggregate-source",
            direction="hardtech",
        )
    ]
    FakeCollector.next_status = "partial"
    _, summary = app._collect_fixed(
        context, payload, "hardtech", date.today().year, env={}
    )
    assert summary["runs"][0]["status"] == "partial"
    assert "application-secret" not in json.dumps(summary, ensure_ascii=False)
    assert "second-secret" not in json.dumps(summary, ensure_ascii=False)
    assert "[redacted]" in json.dumps(summary, ensure_ascii=False)

    FakeCollector.next_items = []
    FakeCollector.next_status = "error"
    _, summary = app._collect_fixed(
        context, payload, "hardtech", date.today().year, env={}
    )
    assert summary["runs"][0]["status"] == "error"


def test_source_pack_effect_resume_preserves_atomic_partial_diagnostics(
    tmp_path, monkeypatch
):
    from ht_lead_radar import source_pack_collector

    calls = 0

    class FakeCollector:
        def __init__(self, **_kwargs):
            self.last_run_summary = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def collect(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            self.last_run_summary = {
                "sources": {
                    "pack-a": {
                        "status": "partial",
                        "evidence_count": 1,
                        "detail_failure_count": 1,
                        "error": "DetailFetchError",
                    }
                }
            }
            return [
                Evidence(
                    company="缓存公司",
                    event_type="funding",
                    phase="strategy_capital",
                    event_date="2026-08-31",
                    title="完成融资",
                    snippet="完成融资",
                    source_url="https://example.cn/a",
                    source_name="pack-a",
                    source_grade="B",
                    direction="hardtech",
                )
            ]

        def source_health_summary(self):
            return {"status": "warning", "failed_count": 0}

    class CachedContext:
        def __init__(self):
            self.effects = {}

        def effect_once(self, key, operation):
            if key not in self.effects:
                self.effects[key] = operation("token")
            return self.effects[key]

    monkeypatch.setattr(source_pack_collector, "SourcePackCollector", FakeCollector)
    context = CachedContext()
    app = LeadRadarApplication(tmp_path / "runtime.sqlite")
    payload = {
        "fixed_sources": str(tmp_path / "missing-fixed.json"),
        "source_packs": str(tmp_path / "source-packs.json"),
        "source_state_db": str(tmp_path / "sources.sqlite"),
        "limit_per_query": 8,
    }

    first_evidence, first = app._collect_fixed(
        context, payload, "hardtech", 2026, env={}
    )
    second_evidence, second = app._collect_fixed(
        context, payload, "hardtech", 2026, env={}
    )

    assert calls == 1
    assert first == second
    assert [item.company for item in first_evidence] == ["缓存公司"]
    assert [item.company for item in second_evidence] == ["缓存公司"]
    assert second["runs"][0]["status"] == "partial"
    assert second["runs"][0]["health"]["status"] == "warning"


def test_source_pack_initialization_failure_is_a_structured_critical_run(
    tmp_path, monkeypatch
):
    from ht_lead_radar import source_pack_collector

    class BrokenCollector:
        def __init__(self, **_kwargs):
            raise RuntimeError("registry unavailable")

    monkeypatch.setattr(source_pack_collector, "SourcePackCollector", BrokenCollector)
    app = LeadRadarApplication(tmp_path / "runtime.sqlite")
    context = SimpleNamespace(effect_once=lambda _key, operation: operation("token"))

    _, summary = app._collect_fixed(
        context,
        {
            "fixed_sources": str(tmp_path / "missing-fixed.json"),
            "source_packs": str(tmp_path / "packs.json"),
            "source_state_db": str(tmp_path / "sources.sqlite"),
            "limit_per_query": 8,
        },
        "hardtech",
        2026,
        env={},
    )

    run = summary["runs"][0]
    assert run["provider"] == "reusable-source-packs"
    assert run["status"] == "error"
    assert run["health"]["status"] == "critical"
    assert run["run_summary"]["sources"]["reusable-source-packs"][
        "status"
    ] == "error"


def test_public_search_initialization_failure_is_a_structured_critical_run(
    tmp_path, monkeypatch
):
    from ht_lead_radar import application

    app = LeadRadarApplication(tmp_path / "runtime.sqlite")
    monkeypatch.setattr(app, "_collect_fixed", lambda *_args, **_kwargs: ([], {}))
    monkeypatch.setattr(
        application,
        "_search_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("search initialization failed")
        ),
    )
    payload = {
        **DEFAULTS,
        "direction": "硬科技",
        "provider": "auto",
        "demo": False,
        "request_plan": {},
        "env_file": None,
        "candidate_pool_size": 20,
        "run_date": product_date().isoformat(),
        "source_state_db": str(tmp_path / "sources.sqlite"),
        "fixed_sources": str(tmp_path / "missing-fixed.json"),
        "source_packs": str(tmp_path / "packs.json"),
    }
    context = SimpleNamespace(
        value=payload,
        effect_once=lambda _key, operation: operation("token"),
    )

    collected = app._collect(context)

    run = collected["metadata"]["source_runs"][0]
    assert run["provider"] == "public-search-fallback"
    assert run["status"] == "error"
    assert run["health"]["status"] == "critical"
    assert collected["metadata"]["source_failures"]


def test_unstable_josint_snapshot_is_a_local_structured_warning(
    tmp_path, monkeypatch
):
    from ht_lead_radar import application
    from ht_lead_radar.josint_snapshot import JOSINTSnapshotUnstable

    app = LeadRadarApplication(tmp_path / "runtime.sqlite")
    monkeypatch.setattr(app, "_collect_fixed", lambda *_args, **_kwargs: ([], {}))
    monkeypatch.setattr(
        application,
        "collect_josint",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            JOSINTSnapshotUnstable("live family kept changing")
        ),
    )
    josint = tmp_path / "jobs.sqlite"
    josint.touch()
    payload = {
        **DEFAULTS,
        "direction": "硬科技",
        "provider": "fixed",
        "demo": False,
        "request_plan": {},
        "env_file": None,
        "candidate_pool_size": 20,
        "run_date": product_date().isoformat(),
        "josint_db": str(josint),
        "source_state_db": str(tmp_path / "sources.sqlite"),
        "fixed_sources": str(tmp_path / "missing-fixed.json"),
        "source_packs": str(tmp_path / "packs.json"),
    }
    context = SimpleNamespace(
        value=payload,
        effect_once=lambda _key, operation: operation("token"),
    )

    collected = app._collect(context)

    warning = collected["metadata"]["source_runs"][-1]
    assert warning["provider"] == "JOSINT late validation"
    assert warning["status"] == "partial"
    assert warning["health"]["status"] == "warning"
    assert warning["run_summary"] == {
        "warning_class": "JOSINTSnapshotUnstable",
        "retry_count": 3,
    }
    assert collected["mode"].endswith("+josint-warning")


def test_legacy_fixed_sources_are_flattened_into_adapter_runs(tmp_path, monkeypatch):
    from ht_lead_radar import application

    class FakeLegacyCollector:
        provider_name = "fixed-source-registry"

        def __init__(self, **_kwargs):
            self.last_run_summary = {"sources": {}, "errors": []}

        def collect(self, *_args, **_kwargs):
            self.last_run_summary = {
                "sources": {
                    "legacy-ok": {
                        "status": "ok",
                        "evidence_count": 3,
                        "detail_success_count": None,
                        "detail_failure_count": 0,
                        "error": "",
                    },
                    "legacy-partial": {
                        "status": "partial",
                        "evidence_count": 1,
                        "detail_success_count": None,
                        "detail_failure_count": 1,
                        "error": "DetailFetchError: one detail failed",
                    },
                },
                "errors": ["legacy-partial: one detail failed"],
            }
            return []

    class EmptyPackCollector:
        def __init__(self, **_kwargs):
            self.last_run_summary = {"sources": {}, "dedicated_aggregate": {}}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def collect(self, *_args, **_kwargs):
            return []

        def source_health_summary(self):
            return {}

    registry = tmp_path / "fixed.json"
    registry.write_text('{"sources": []}', encoding="utf-8")
    monkeypatch.setattr(application, "FixedSourceCollector", FakeLegacyCollector)
    from ht_lead_radar import source_pack_collector

    monkeypatch.setattr(source_pack_collector, "SourcePackCollector", EmptyPackCollector)
    context = SimpleNamespace(effect_once=lambda _key, operation: operation("token"))
    app = LeadRadarApplication(tmp_path / "runtime.sqlite")

    _, summary = app._collect_fixed(
        context,
        {
            "fixed_sources": str(registry),
            "source_packs": str(tmp_path / "packs.json"),
            "source_state_db": str(tmp_path / "sources.sqlite"),
            "limit_per_query": 8,
        },
        "hardtech",
        date.today().year,
        env={},
    )

    legacy = summary["runs"][0]
    assert legacy["status"] == "partial"
    assert legacy["run_summary"]["sources"]["legacy-ok"]["status"] == "ok"
    assert (
        legacy["run_summary"]["sources"]["legacy-partial"]["status"]
        == "partial"
    )


def test_adapter_metrics_preserve_native_counters_without_stage_inference():
    # This shape mirrors AdapterRun.to_dict().  A dedicated adapter does not
    # emit the generic discovered/observation or semantic-attempt/accepted
    # counters, so those fields must remain unknown rather than being inferred
    # from superficially similar pipeline stages.
    native_run = {
        "listing_count": 20,
        "incremental_count": 18,
        "detail_success_count": 17,
        "detail_failure_count": 1,
        "rule_event_count": 4,
        "minimax_event_count": 16,
        "evidence_count": 19,
        "semantic_failure_count": 1,
        "prefiltered_count": 2,
        "adaptive_used_count": 0,
        "omissions_detected": 0,
    }

    counts = _adapter_metric_counts(native_run)

    assert counts["listing_count"] == 20
    assert counts["detail_failure_count"] == 1
    assert counts["rule_event_count"] == 4
    assert counts["minimax_event_count"] == 16
    assert counts["discovered_count"] is None
    assert counts["observation_count"] is None
    assert counts["detail_error_count"] is None
    assert counts["semantic_attempt_count"] is None
    assert counts["semantic_accepted_count"] is None
    assert counts["semantic_prefiltered_count"] is None


def test_ops_metrics_persistence_failure_is_not_silently_ignored(
    tmp_path, monkeypatch
):
    from ht_lead_radar import application

    def fail_metrics(*_args, **_kwargs):
        raise OSError("provider token=must-not-surface")

    monkeypatch.setattr(application.OpsMetricsStore, "record_run", fail_metrics)
    payload = _payload(tmp_path)

    with pytest.raises(StageExecutionError, match="ops metrics persistence failed"):
        LeadRadarApplication(payload["runtime_db"]).run(
            payload,
            default_idempotency_key(payload, refresh=True),
        )
    assert b"must-not-surface" not in (tmp_path / "runtime.sqlite").read_bytes()
