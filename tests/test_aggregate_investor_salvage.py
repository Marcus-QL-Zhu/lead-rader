import json

from ht_lead_radar.aggregate_adapters.finance_rules import (
    FundingRuleConfig,
    extract_funding_events,
)
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
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
