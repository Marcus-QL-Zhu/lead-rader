from ht_lead_radar.aggregate_adapters.industry_rules import (
    IndustryRuleConfig,
    extract_industry_events,
)
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SourceArticleIndex,
    SourceChannel,
)


CHANNEL = SourceChannel(
    source_id="industry-test",
    name="产业测试",
    url="https://example.com",
    source_grade="B",
    event_prior=(
        "executive_change",
        "factory_or_capacity",
        "major_order",
    ),
    allowed_hosts=("example.com",),
)


def _article(body):
    index = SourceArticleIndex(
        source_id=CHANNEL.source_id,
        source_article_id="1",
        channel="industry",
        canonical_url="https://example.com/1",
        title="星河芯片最新动态",
        published_at="2026-07-29",
        discovered_at="2026-07-29T12:00:00+00:00",
        cursor_value="1",
        listing_page=CHANNEL.url,
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
        structured_data={"company": "星河芯片"},
    )
    return CleanArticle(index=index, clean_body=body, content_hash="body")


def test_reusable_industry_rules_split_distinct_early_signals():
    events = extract_industry_events(
        CHANNEL,
        _article(
            "星河芯片任命刘明出任中国区总裁。"
            "星河芯片计划新建晶圆产线并扩产。"
            "星河芯片获得头部客户订单。"
        ),
        config=IndustryRuleConfig(processor="rules:test"),
    )

    assert {
        (event.event_type, event.event_status, event.phase)
        for event in events
    } == {
        ("executive_change", "completed", "strategy_capital"),
        ("factory_or_capacity", "started", "build_organize"),
        ("major_order", "completed", "scale_delivery"),
    }
