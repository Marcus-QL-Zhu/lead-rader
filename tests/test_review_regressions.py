from datetime import date

from ht_lead_radar.backtest import (
    BacktestConfig,
    _anonymize_prediction_packet,
    evidence_before_cutoff,
)
from ht_lead_radar.models import Evidence
from ht_lead_radar.taxonomy import classify_seniority


def test_anonymization_preserves_source_group_equivalence():
    packet = {
        "lead_index": 1,
        "company": "Acme",
        "direction": "robotics",
        "evidence": [
            {"source_group": "same-media", "fact": "Acme a"},
            {"source_group": "same-media", "fact": "Acme b"},
            {"source_group": "other-media", "fact": "Acme c"},
        ],
    }

    result = _anonymize_prediction_packet(packet)

    groups = [item["source_group"] for item in result["evidence"]]
    assert groups[0] == groups[1]
    assert groups[2] != groups[0]


def test_dynamic_media_without_capture_date_is_not_pre_cutoff_evidence():
    item = Evidence(
        company="Acme",
        event_type="funding",
        event_date="2026-01-01",
        phase="funding",
        title="Financing",
        snippet="Acme financing",
        published_at="2026-01-02",
        observed_at="",
        source_excerpt="Acme completed a financing round.",
        source_url="https://example.com/living-profile",
        source_name="Example",
        source_kind="mainstream_media",
        company_type="startup_private",
    )

    assert evidence_before_cutoff(
        [item],
        BacktestConfig(cutoff=date(2026, 5, 1)),
    ) == []


def test_below_director_chinese_titles_are_excluded_before_target_match():
    for title in ("高级经理（总监储备）", "制造副总监", "总监助理", "算法专家/总监"):
        assert classify_seniority(title, "负责团队和预算")[1] is False
