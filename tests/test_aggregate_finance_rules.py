from ht_lead_radar.aggregate_adapters.finance_rules import (
    FundingRuleConfig,
    extract_funding_events,
)
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SourceArticleIndex,
    SourceChannel,
)


CHANNEL = SourceChannel(
    source_id="funding-test",
    name="融资测试",
    url="https://example.com",
    source_grade="B",
    event_prior=("funding",),
    allowed_hosts=("example.com",),
)


def _article(body):
    index = SourceArticleIndex(
        source_id=CHANNEL.source_id,
        source_article_id="1",
        channel="funding",
        canonical_url="https://example.com/1",
        title="投融资日报",
        published_at="2026-07-29",
        discovered_at="2026-07-29T12:00:00+00:00",
        cursor_value="1",
        listing_page=CHANNEL.url,
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
    )
    return CleanArticle(index=index, clean_body=body, content_hash="body")


def test_reusable_rules_split_completed_started_and_multiple_companies():
    events = extract_funding_events(
        CHANNEL,
        _article(
            "“星河芯片”完成超1亿元A轮融资。"
            "原定八月开始的B轮已提前开始。"
            "“智谷机器人”官宣完成近亿元战略融资。"
        ),
        config=FundingRuleConfig(processor="rules:test"),
    )

    assert {
        (event.canonical_company, event.funding_round, event.event_status)
        for event in events
    } == {
        ("星河芯片", "A轮", "completed"),
        ("星河芯片", "B轮", "started"),
        ("智谷机器人", "战略融资", "completed"),
    }


def test_shared_rules_keep_pre_ipo_round_and_reject_target_valuation_as_amount():
    events = extract_funding_events(
        CHANNEL,
        _article(
            "月之暗面已开始筹划Pre-IPO轮融资，并已开始接触潜在投资者，"
            "目标是以500亿美元的投前估值完成筹资。"
        ),
        config=FundingRuleConfig(processor="rules:test"),
    )

    assert len(events) == 1
    assert events[0].canonical_company == "月之暗面"
    assert events[0].funding_round == "Pre-IPO轮"
    assert events[0].funding_amount == ""
    assert events[0].event_status == "started"
