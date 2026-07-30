from ht_lead_radar.aggregate_adapters.industry_rules import (
    IndustryRuleConfig,
    extract_media_events,
)
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SourceArticleIndex,
    SourceChannel,
)


def test_media_rules_keep_total_funding_as_cumulative_and_subject_milestone():
    company = "\u57c3\u82af\u534a\u5bfc\u4f53"
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
        title=f"{company}\u5b8c\u6210B+\u8f6e\u878d\u8d44",
        published_at="2026-07-29",
        discovered_at="2026-07-29T00:00:00+00:00",
        cursor_value="1",
        listing_page=channel.url,
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
        structured_data={"company": company},
    )
    shipment = (
        "\u4ece2023\u5e74\u9996\u53f0\u8bbe\u5907\u4ea4\u4ed8\uff0c\u52302026\u5e74"
        "\u8be5\u516c\u53f8\u7d2f\u8ba1\u51fa\u8d27\u7a81\u7834\u767e\u53f0\u3002"
    )
    generic = (
        "\u8fd9\u662f\u4e00\u6bb5\u66f4\u957f\u7684\u884c\u4e1a\u80cc\u666f\u6587\u5b57\uff0c"
        "\u5c55\u793a\u4ece\u6280\u672f\u7a81\u7834\u5230\u89c4\u6a21\u5316\u91cf\u4ea7\u7684\u8fc7\u7a0b\u3002"
    )
    article = CleanArticle(
        index=index,
        clean_body=(
            f"{company}\u5df2\u987a\u5229\u5b8c\u6210B+\u8f6e\u878d\u8d44\uff0c"
            "\u603b\u878d\u8d44\u89c4\u6a21\u8fd110\u4ebf\u5143\u3002"
            f"{shipment}{generic}"
        ),
        content_hash="article",
    )

    events = extract_media_events(
        channel,
        article,
        config=IndustryRuleConfig(processor="rules:media"),
        funding_processor="rules:media-funding",
    )
    funding = next(event for event in events if event.event_type == "funding")
    milestone = next(
        event for event in events if event.event_type == "technical_milestone"
    )

    assert funding.funding_amount == ""
    assert funding.cumulative_funding_amount == "\u8fd110\u4ebf\u5143"
    assert milestone.evidence_quotes == (shipment,)
