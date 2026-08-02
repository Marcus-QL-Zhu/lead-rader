from dataclasses import asdict, replace
from datetime import date
import hashlib
import json

from ht_lead_radar.backtest import BacktestConfig, build_prediction_packets
from ht_lead_radar.company_demand_v2 import build_company_evidence_packets
from ht_lead_radar.company_timeline import build_company_timeline
from ht_lead_radar.models import Evidence


def _evidence(event_id: str, event_date: str, event_type: str) -> dict:
    return {
        "event_id": event_id,
        "company": "甲辰科技",
        "event_type": event_type,
        "event_date": event_date,
        "published_at": event_date,
        "title": event_id,
        "snippet": f"{event_id}事实",
        "source_url": f"https://example.com/{event_id}",
        "source_grade": "A",
    }


def test_timeline_builds_explicit_90_and_180_day_buckets() -> None:
    timeline = build_company_timeline(
        [
            _evidence("recent", "2026-07-15", "major_order"),
            _evidence("prior", "2026-03-15", "executive_change"),
            _evidence("old", "2025-12-01", "funding"),
            _evidence("future", "2026-08-02", "partnership"),
        ],
        as_of="2026-08-01",
    )

    assert [item["evidence_id"] for item in timeline["buckets"]["days_0_90"]] == [
        "recent"
    ]
    assert [item["evidence_id"] for item in timeline["buckets"]["days_91_180"]] == [
        "prior"
    ]
    assert timeline["selected_evidence_count"] == 2
    assert timeline["has_undated_evidence"] is False


def test_timeline_excludes_recruiting_inputs_and_is_idempotent() -> None:
    values = [
        _evidence("funding", "2026-07-01", "funding"),
        _evidence("job", "2026-07-20", "job_ad"),
    ]

    first = build_company_timeline(values, as_of="2026-08-01")
    second = build_company_timeline(values, as_of="2026-08-01")

    assert first == second
    assert [item["evidence_id"] for item in first["evidence"]] == ["funding"]


def test_production_and_backtest_use_the_same_timeline_contract() -> None:
    evidence = Evidence(
        company="甲辰科技",
        event_type="major_order",
        phase="build_organize",
        event_date="2026-07-01",
        title="获得客户订单",
        snippet="甲辰科技获得客户订单。",
        source_url="https://example.com/order",
        source_name="example",
        source_grade="A",
        direction="机器人",
        event_id="ev_order",
        published_at="2026-07-01",
        company_type="startup_private",
        source_excerpt="甲辰科技获得客户订单。",
        source_kind="mainstream_media",
        observed_at="2026-07-02",
    )
    hash_record = asdict(evidence)
    hash_record.pop("content_sha256")
    content_hash = hashlib.sha256(
        json.dumps(
            hash_record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    evidence = replace(evidence, content_sha256=content_hash)
    production = build_company_evidence_packets(
        {
            "manifest": {"as_of": "2026-08-01"},
            "leads": [
                {
                    "company": evidence.company,
                    "direction": evidence.direction,
                    "evidence": [asdict(evidence)],
                }
            ],
        }
    )[0]
    historical = build_prediction_packets(
        [evidence],
        BacktestConfig(cutoff=date(2026, 8, 1)),
    )[0]

    assert production["timeline"] == historical["timeline"]
    assert production["evidence"] == historical["evidence"]
