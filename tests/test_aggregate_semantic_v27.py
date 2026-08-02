from __future__ import annotations

import pytest

from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


@pytest.mark.parametrize(
    ("sentence", "event_type"),
    (
        ("甲辰科技拟非公开发行募资不超过30亿元。", "funding"),
        ("乙巳机器人控股子公司获乘用车座椅项目定点。", "major_order"),
        ("丙午材料拟投建电子级高纯材料产线。", "factory_or_capacity"),
        ("丁未科技与远山集团签署合作备忘录。", "partnership"),
        ("戊申智能正式版API现已上线公测。", "technical_milestone"),
        ("己酉医药因涉嫌信息披露违规被证监会立案。", "regulatory_or_clinical"),
        ("庚戌科技在欧美市场实现销售发货。", "customer_validation"),
        ("辛亥股份正在筹划控制权变更事项。", "merger_acquisition"),
    ),
)
def test_v27_action_patterns_create_host_owned_claims(
    sentence: str,
    event_type: str,
) -> None:
    candidates = MiniMaxSemanticProcessor._event_candidates(sentence)

    matching = [item for item in candidates if item["event_type"] == event_type]
    assert matching
    assert all(item["claim_id"].startswith("c_") for item in matching)
    assert all(item["span_id"].startswith("s_") for item in matching)
    assert all(item["char_start"] >= 0 for item in matching)
    assert all(item["char_end"] <= len(sentence) for item in matching)
