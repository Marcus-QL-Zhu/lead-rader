import pytest

from ht_lead_radar.aggregate_adapters.entities import (
    canonical_company_name,
    company_alias_candidates,
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
