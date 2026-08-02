import json

from ht_lead_radar.aggregate_adapters.finance_rules import (
    FundingRuleConfig,
    extract_funding_events,
)
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


CHANNEL = SourceChannel(
    source_id="funding-salvage-test",
    name="funding salvage",
    url="https://example.com",
    source_grade="B",
    event_prior=("funding",),
    allowed_hosts=("example.com",),
)


class Runner:
    def __init__(self, response: str) -> None:
        self.response = response

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return self.response


def test_salvaged_investor_gets_exact_current_round_quote():
    company = "\u6c49\u79be\u751f\u7269"
    investor = "\u56fd\u79d1\u521b\u6295"
    current_quote = (
        f"\u8fd1\u65e5\uff0c{company}\u5ba3\u5e03\u5b8c\u6210"
        f"\u6570\u5343\u4e07\u5143\u6218\u7565\u878d\u8d44\uff0c"
        f"\u672c\u8f6e\u7531{investor}\u6295\u8d44\u3002"
    )
    historical_quote = (
        f"\u6b64\u524d\uff0c{investor}\u66fe\u6295\u8d44"
        "\u53e6\u4e00\u5bb6\u516c\u53f8\u3002"
    )
    index = SourceArticleIndex(
        source_id=CHANNEL.source_id,
        source_article_id="1",
        channel="funding",
        canonical_url="https://example.com/1",
        title=f"{company}\u5b8c\u6210\u6218\u7565\u878d\u8d44",
        published_at="2026-07-29",
        discovered_at="2026-07-29T12:00:00+00:00",
        cursor_value="1",
        listing_page=CHANNEL.url,
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
        structured_data={"company": company},
    )
    article = CleanArticle(
        index=index,
        clean_body=f"{current_quote}{historical_quote}",
        content_hash="body",
    )
    seeds = extract_funding_events(
        CHANNEL,
        article,
        config=FundingRuleConfig(processor="rules:test"),
    )
    invalid = json.dumps(
        {
            "events": [
                {
                    "company": company,
                    "event_type": "funding",
                    "industry_tags": ["biotech"],
                    "funding_round": "\u6218\u7565\u878d\u8d44",
                    "funding_amount": "\u6570\u5343\u4e07\u5143",
                    "cumulative_funding_amount": "",
                    "investors": [investor],
                    "event_status": "completed",
                    "event_summary": "\u5b8c\u6210\u878d\u8d44",
                    "evidence_quotes": ["not verbatim"],
                    "confidence": "high",
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )

    events = MiniMaxSemanticProcessor(Runner(invalid)).process(
        CHANNEL,
        article,
        seeds,
    )

    assert events[0].investors == (investor,)
    assert current_quote in events[0].evidence_quotes
    assert historical_quote not in events[0].evidence_quotes
    assert all(
        any(investor in quote for quote in events[0].evidence_quotes)
        for investor in events[0].investors
    )


def test_salvage_does_not_bind_investor_from_another_company_round():
    company = "\u661f\u6cb3\u82af\u7247"
    other_company = "\u94f6\u6cb3\u673a\u5668\u4eba"
    wrong_investor = "\u56fd\u79d1\u521b\u6295"
    primary = (
        f"{company}\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\uff0c"
        "\u8fdc\u5c71\u8d44\u672c\u9886\u6295\u3002"
    )
    other = (
        f"{other_company}\u5b8c\u62102\u4ebf\u5143B\u8f6e\u878d\u8d44\uff0c"
        f"\u672c\u8f6e\u7531{wrong_investor}\u9886\u6295\u3002"
    )
    index = SourceArticleIndex(
        source_id=CHANNEL.source_id,
        source_article_id="cross-event",
        channel="funding",
        canonical_url="https://example.com/cross-event",
        title=f"{company}\u5b8c\u6210A\u8f6e\u878d\u8d44",
        published_at="2026-07-29",
        discovered_at="2026-07-29T12:00:00+00:00",
        cursor_value="cross-event",
        listing_page=CHANNEL.url,
        listing_position=1,
        content_hash="cross-index",
        discovery_method="exact",
        structured_data={"company": company},
    )
    article = CleanArticle(
        index=index,
        clean_body=primary + other,
        content_hash="cross-body",
    )
    seeds = extract_funding_events(
        CHANNEL,
        article,
        config=FundingRuleConfig(processor="rules:test"),
    )
    response = json.dumps(
        {
            "events": [
                {
                    "company": company,
                    "event_type": "funding",
                    "industry_tags": ["semiconductor"],
                    "funding_round": "A\u8f6e",
                    "funding_amount": "1\u4ebf\u5143",
                    "cumulative_funding_amount": "",
                    "investors": [wrong_investor],
                    "event_status": "completed",
                    "event_summary": primary,
                    "evidence_quotes": [primary],
                    "confidence": "high",
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )
    processor = MiniMaxSemanticProcessor(Runner(response))

    events = processor.process(CHANNEL, article, seeds)

    assert processor.last_audit["status"] == "partial"
    target = next(item for item in events if item.canonical_company == company)
    assert wrong_investor not in target.investors
    assert other not in target.evidence_quotes


def test_salvage_rejects_conflicting_round_in_current_investor_sentence():
    company = "\u661f\u6cb3\u82af\u7247"
    investor = "\u56fd\u79d1\u521b\u6295"
    event_quote = f"{company}\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\u3002"
    investor_quote = (
        f"\u672c\u8f6eB\u8f6e\u878d\u8d44\u7531{investor}\u9886\u6295\u3002"
    )
    seed = SemanticEvent(
        source_id=CHANNEL.source_id,
        source_article_id="round-conflict",
        canonical_url="https://example.com/round-conflict",
        company_mentions=(company,),
        canonical_company=company,
        event_type="funding",
        event_date="2026-07-29",
        industry_tags=(),
        funding_round="A\u8f6e",
        funding_amount="1\u4ebf\u5143",
        evidence_quotes=(event_quote,),
        content_hash="round-conflict",
    )

    quote = MiniMaxSemanticProcessor._current_investor_quote(
        event_quote + investor_quote,
        investor,
        seed,
    )

    assert quote == ""
