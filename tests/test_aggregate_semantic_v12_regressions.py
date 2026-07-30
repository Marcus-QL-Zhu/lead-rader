from ht_lead_radar.aggregate_adapters.industry_rules import (
    IndustryRuleConfig,
    extract_media_events,
)
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


def test_next_historical_sentence_does_not_hide_current_primary_quote():
    quote = "\u8fd1\u65e5\uff0c\u6c49\u79be\u751f\u7269\u5b8c\u6210\u6218\u7565\u878d\u8d44\u3002"
    text = (
        f"{quote}"
        "\u6b64\u524d\uff0c\u6c49\u79be\u751f\u7269\u5df2\u83b7\u5f97\u5176\u4ed6\u673a\u6784\u6295\u8d44\u3002"
    )

    assert not MiniMaxSemanticProcessor._event_evidence_is_historical(
        text,
        [quote],
        "2026-07-14",
    )


def test_funding_use_without_operational_action_is_not_second_event():
    evidence = (
        "\u8be5\u9879\u76ee\u662f\u4ea7\u4e1a\u5316\u5e03\u5c40\u4e2d\u7684\u6838\u5fc3\u6807\u6746\u9879\u76ee\u3002"
        "\u672c\u6b21\u8d44\u91d1\u5c06\u4e13\u9879\u7528\u4e8e\u8be5\u9879\u76ee\u3002"
    )

    assert MiniMaxSemanticProcessor._funding_use_only_nonfunding(
        "factory_or_capacity",
        evidence,
    )


def test_media_rules_preserve_funding_and_current_shipment_milestone():
    channel = SourceChannel(
        source_id="media",
        name="media",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding", "technical_milestone"),
        allowed_hosts=("example.com",),
    )
    index = SourceArticleIndex(
        source_id=channel.source_id,
        source_article_id="1",
        channel="latest",
        canonical_url="https://example.com/1",
        title="\u57c3\u82af\u534a\u5bfc\u4f53\u5b8c\u6210B+\u8f6e\u878d\u8d44",
        published_at="2026-07-29",
        discovered_at="2026-07-29T00:00:00+00:00",
        cursor_value="1",
        listing_page=channel.url,
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
        structured_data={"company": "\u57c3\u82af\u534a\u5bfc\u4f53"},
    )
    article = CleanArticle(
        index=index,
        clean_body=(
            "\u57c3\u82af\u534a\u5bfc\u4f53\u5b8c\u6210B+\u8f6e\u878d\u8d44\u3002"
            "2026\u5e74\u7d2f\u8ba1\u51fa\u8d27\u7a81\u7834\u767e\u53f0\u3002"
        ),
        content_hash="article",
    )

    events = extract_media_events(
        channel,
        article,
        config=IndustryRuleConfig(processor="rules:media"),
        funding_processor="rules:media-funding",
    )

    assert {event.event_type for event in events} == {
        "funding",
        "technical_milestone",
    }
