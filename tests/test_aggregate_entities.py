import pytest

from ht_lead_radar.aggregate_adapters.entities import (
    canonical_company_name,
    company_alias_candidates,
    is_company_like,
)


@pytest.mark.parametrize(
    ("reported", "canonical"),
    (
        (
            "聚焦新一代脑机交互神经调控技术的上海空山慧科技有限公司",
            "上海空山慧科技有限公司",
        ),
        (
            "可控核聚变FRC技术路线企业合肥星能玄光科技有限责任公司",
            "合肥星能玄光科技有限责任公司",
        ),
        (
            "跨境电商行业金融科技企业迈豹云数（深圳）科技有限公司",
            "迈豹云数（深圳）科技有限公司",
        ),
        (
            "机器人灵巧手初创企业苏州伯牙智能科技有限公司",
            "苏州伯牙智能科技有限公司",
        ),
    ),
)
def test_canonical_company_name_removes_only_editorial_prefix(reported, canonical):
    assert canonical_company_name(reported) == canonical


def test_company_alias_candidates_include_brand_before_location_parenthetical():
    aliases = company_alias_candidates(
        "原力灵机（重庆）智能科技有限公司"
    )

    assert "原力灵机" in aliases


def test_company_word_inside_real_legal_name_is_not_blindly_removed():
    company = "中小企业服务有限公司"

    assert canonical_company_name(company) == company


@pytest.mark.parametrize(
    "value",
    (
        "人工智能应用赛道紧扣AI从",
        "在公开",
        "天内",
        "按需采购",
        "换帅",
        "通用操作大脑",
        "由智元机器人临界点",
        "西北核技术研究所",
    ),
)
def test_sentence_fragments_products_and_research_bodies_are_not_companies(value):
    assert not is_company_like(value)


@pytest.mark.parametrize("value", ("白犀牛", "DeepSeek", "MiniMax", "月泉仿生"))
def test_explicit_brand_shapes_remain_company_like(value):
    assert is_company_like(value)
