from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from ht_lead_radar.semantic_gold import validate_gold_packet
from scripts.build_semantic_v27_gold_lineage import build_lineage


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_lineage_is_valid_and_does_not_mutate_frozen_parent() -> None:
    parent = json.loads(
        (
            PROJECT_ROOT
            / "evaluation/semantic-v27/final-v2-gold/adjudication.json"
        ).read_text(encoding="utf-8")
    )
    frozen = deepcopy(parent)

    lineage = build_lineage(parent)

    assert parent == frozen
    assert lineage["dataset_version"] == "semantic-v27-final-v2-lineage-v1"
    assert lineage["lineage"]["benchmark_status"] == "opened_development_only"
    assert validate_gold_packet(lineage)["valid"] is True
    assert (
        sum(
            len(case["annotation"]["gold_events"])
            for case in lineage["cases"]
        )
        == 86
    )


def test_lineage_removes_non_operating_subjects_and_adds_atomic_events() -> None:
    parent = json.loads(
        (
            PROJECT_ROOT
            / "evaluation/semantic-v27/final-v2-gold/adjudication.json"
        ).read_text(encoding="utf-8")
    )
    lineage = build_lineage(parent)
    cases = {case["key"]: case for case in lineage["cases"]}

    weekly = cases["nbd-vcpe-weekly:4517408"]["annotation"]
    assert all(
        event["canonical_company"] not in {"元禾厚望", "长石资本"}
        for event in weekly["gold_events"]
    )
    ractigen = cases["pedaily-vcpe-events:566982"]["annotation"]
    evidence = {
        event["evidence_span"]["text"] for event in ractigen["gold_events"]
    }
    assert "已获FDA快速通道资格" in evidence
    assert "II期临床入组已全部完成" in evidence


def test_lineage_uses_atomic_spans_and_explicit_target_context() -> None:
    parent = json.loads(
        (
            PROJECT_ROOT
            / "evaluation/semantic-v27/final-v2-gold/adjudication.json"
        ).read_text(encoding="utf-8")
    )
    lineage = build_lineage(parent)
    cases = {case["key"]: case for case in lineage["cases"]}

    digin = cases["vbdata-funding:1519087619"]["annotation"]["gold_events"]
    target = next(
        event
        for event in digin
        if event["event_type"] == "technical_milestone"
        and event["event_status"] == "target"
    )
    assert target["evidence_span"]["text"].startswith("迪英加将")
    assert target["status_context_span"]["text"].startswith("本次C轮融资完成")

    pinshan = cases["vbdata-funding:1519086865"]["annotation"]["gold_events"]
    order = next(event for event in pinshan if event["event_type"] == "major_order")
    assert order["evidence_span"]["text"] == "已经获得多家药企的大规模生产订单"

    roadmap = cases["36kr-financing-flash:3919125291412872"]["annotation"][
        "gold_events"
    ]
    targets = [
        event
        for event in roadmap
        if event["event_type"] == "technical_milestone"
        and event["event_status"] == "target"
    ]
    assert len(targets) >= 2
    assert all("将重点投向" in event["status_context_span"]["text"] for event in targets)
