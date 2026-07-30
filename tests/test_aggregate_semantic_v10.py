import json

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


class Runner:
    def __init__(self, responses):
        self.responses = iter(responses)

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return next(self.responses)


def test_successful_repair_preserves_grounded_investor_from_first_response():
    company = "\u683c\u5f0f\u5854"
    investor = "\u56fd\u79d1\u521b\u6295"
    quote = (
        f"\u672c\u8f6e\u878d\u8d44\u7531 {investor} "
        "\u9886\u6295\uff0c\u5174\u6e58\u8d44\u672c\u65d7\u4e0b\u57fa\u91d1\u8ddf\u6295\u3002"
    )
    index = SourceArticleIndex(
        source_id="test",
        source_article_id="1",
        channel="funding",
        canonical_url="https://example.com/1",
        title=f"{company}\u5b8c\u6210A\u8f6e\u878d\u8d44",
        published_at="2026-07-29",
        discovered_at="2026-07-29T00:00:00+00:00",
        cursor_value="1",
        listing_page="https://example.com",
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
    )
    article = CleanArticle(
        index=index,
        clean_body=f"{company}\u5b8c\u6210A\u8f6e\u878d\u8d44\u3002{quote}",
        content_hash="body",
    )
    seed = SemanticEvent(
        source_id="test",
        source_article_id="1",
        canonical_url=index.canonical_url,
        company_mentions=(company,),
        canonical_company=company,
        event_type="funding",
        event_date="2026-07-29",
        industry_tags=(),
        funding_round="A\u8f6e",
        evidence_quotes=(f"{company}\u5b8c\u6210A\u8f6e\u878d\u8d44\u3002",),
        content_hash="body",
    )
    base_event = {
        "company": company,
        "event_type": "funding",
        "industry_tags": [],
        "funding_round": "A\u8f6e",
        "funding_amount": "",
        "cumulative_funding_amount": "",
        "event_status": "completed",
        "event_summary": f"{company}\u5b8c\u6210A\u8f6e\u878d\u8d44",
        "confidence": "high",
    }
    first = json.dumps(
        {
            "events": [
                {
                    **base_event,
                    "investors": [investor],
                    "evidence_quotes": ["not verbatim"],
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )
    repaired = json.dumps(
        {
            "events": [
                {
                    **base_event,
                    "investors": [],
                    "evidence_quotes": [
                        f"{company}\u5b8c\u6210A\u8f6e\u878d\u8d44\u3002"
                    ],
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )
    channel = SourceChannel(
        source_id="test",
        name="test",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding",),
        allowed_hosts=("example.com",),
    )

    processor = MiniMaxSemanticProcessor(Runner([first, repaired]))
    events = processor.process(channel, article, [seed])

    assert processor.last_audit["status"] == "repaired"
    assert events[0].investors == (investor,)
    assert quote in events[0].evidence_quotes
