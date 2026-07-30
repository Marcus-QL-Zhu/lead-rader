import json

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


class _Runner:
    def __init__(self, responses):
        self.responses = iter(responses)

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return next(self.responses)


def test_repair_salvages_grounded_two_character_investor_names():
    company = "原力灵机（重庆）智能科技有限公司"
    event_quote = f"{company}宣布完成新一轮战略融资。"
    investor_quote = "本轮融资中，智谱、商汤、阿里等机构同步加持。"
    channel = SourceChannel(
        source_id="test",
        name="test",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding",),
        allowed_hosts=("example.com",),
    )
    index = SourceArticleIndex(
        source_id="test",
        source_article_id="1",
        channel="funding",
        canonical_url="https://example.com/1",
        title=event_quote,
        published_at="2026-06-12",
        discovered_at="2026-06-12T00:00:00+00:00",
        cursor_value="1",
        listing_page=channel.url,
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
    )
    article = CleanArticle(
        index=index,
        clean_body=f"{event_quote}{investor_quote}",
        content_hash="article",
    )
    seed = SemanticEvent(
        source_id="test",
        source_article_id="1",
        canonical_url=index.canonical_url,
        company_mentions=(company, "原力灵机"),
        canonical_company=company,
        event_type="funding",
        event_date="2026-06-12",
        industry_tags=(),
        funding_round="战略融资",
        evidence_quotes=(event_quote,),
        content_hash="article",
    )
    base = {
        "company": company,
        "event_type": "funding",
        "industry_tags": [],
        "funding_round": "战略融资",
        "funding_amount": "",
        "cumulative_funding_amount": "",
        "event_status": "completed",
        "event_summary": event_quote,
        "confidence": "high",
    }
    first = json.dumps(
        {
            "events": [
                {
                    **base,
                    "investors": ["智谱", "商汤", "阿里"],
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
                    **base,
                    "investors": [],
                    "evidence_quotes": [event_quote],
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )

    events = MiniMaxSemanticProcessor(_Runner([first, repaired])).process(
        channel,
        article,
        [seed],
    )

    assert events[0].investors == ("智谱", "商汤", "阿里")
    assert any(investor_quote in quote for quote in events[0].evidence_quotes)
