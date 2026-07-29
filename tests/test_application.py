import json

from ht_lead_radar.application import (
    LeadRadarApplication,
    default_idempotency_key,
)
from ht_lead_radar.requests import plan_opportunity_request


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
