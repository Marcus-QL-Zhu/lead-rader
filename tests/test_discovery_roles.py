from ht_lead_radar.collectors import SearchResult
from ht_lead_radar.discovery import attributable_context, extract_company_names
from ht_lead_radar.role_inference import roles_for


def test_generic_company_extraction_covers_multiple_hardtech_sectors():
    text = "蓝箭航天完成新一轮融资，能量奇点能源宣布装置建设，脑虎科技获批临床"
    assert extract_company_names(text) == ["蓝箭航天", "能量奇点能源", "脑虎科技"]


def test_attribution_rejects_event_from_another_headline_company():
    result = SearchResult(
        title="甲科技完成融资",
        url="https://example.test/a",
        snippet="乙科技也在文章中被提及。",
    )
    assert attributable_context("乙科技", result) == ""


def test_known_industries_produce_specific_director_plus_roles():
    assert "注册法规总监" in roles_for(
        "脑机接口", {"regulatory_approval"}
    )
    assert "工艺整合总监" in roles_for(
        "半导体", {"factory_or_capacity"}
    )
    assert "型号总师" in roles_for(
        "商业航天", {"technical_milestone"}
    )
