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
    source_id="funding-history-test",
    name="funding history test",
    url="https://example.com",
    source_grade="B",
    event_prior=("funding",),
    allowed_hosts=("example.com",),
)


def _article(body: str, *, published_at: str = "2026-07-14") -> CleanArticle:
    index = SourceArticleIndex(
        source_id=CHANNEL.source_id,
        source_article_id="1",
        channel="funding",
        canonical_url="https://example.com/1",
        title="\u516c\u53f8\u521b\u59cb\u4eba\u5bf9\u8bdd",
        published_at=published_at,
        discovered_at=f"{published_at}T12:00:00+00:00",
        cursor_value="1",
        listing_page=CHANNEL.url,
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
        structured_data={"company": "\u683c\u5f0f\u5854"},
    )
    return CleanArticle(index=index, clean_body=body, content_hash="body")


def test_earlier_month_period_in_interview_is_not_a_new_funding_event():
    body = (
        "\u56de\u987e\u4e03\u6708\u521d\uff0c\u683c\u5f0f\u5854"
        "\u5b8c\u62104.2\u4ebf\u5143\u5929\u4f7f+\u8f6e\u878d\u8d44\uff0c"
        "\u4ec5\u95f4\u9694\u56db\u4e2a\u6708\u518d\u6b21\u53d7\u8bbf\u3002"
    )

    events = extract_funding_events(
        CHANNEL,
        _article(body),
        config=FundingRuleConfig(processor="rules:test"),
    )

    assert events == []


def test_same_day_funding_statement_remains_current():
    body = (
        "7\u670814\u65e5\uff0c\u683c\u5f0f\u5854"
        "\u5b8c\u62104.2\u4ebf\u5143\u5929\u4f7f+\u8f6e\u878d\u8d44\u3002"
    )

    events = extract_funding_events(
        CHANNEL,
        _article(body),
        config=FundingRuleConfig(processor="rules:test"),
    )

    assert len(events) == 1
    assert events[0].canonical_company == "\u683c\u5f0f\u5854"
