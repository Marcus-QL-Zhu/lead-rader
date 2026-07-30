import json

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


CHANNEL = SourceChannel(
    source_id="industry-history-test",
    name="industry history",
    url="https://example.com",
    source_grade="B",
    event_prior=("executive_change", "factory_or_capacity"),
    allowed_hosts=("example.com",),
)


class Runner:
    def __init__(self, response: str) -> None:
        self.response = response

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return self.response


def test_historical_industry_event_is_not_promoted_as_current_increment():
    historical = (
        "\u53bb\u5e74\uff0c\u745e\u8428\u7535\u5b50"
        "\u4efb\u547d\u4e86\u65b0\u4efb\u4e2d\u56fd\u533a\u603b\u88c1\u3002"
    )
    current = (
        "7\u670829\u65e5\uff0c\u745e\u8428\u7535\u5b50"
        "\u5ba3\u5e03\u6269\u5efa\u4e0a\u6d77\u7814\u53d1\u4e2d\u5fc3\u3002"
    )
    index = SourceArticleIndex(
        source_id=CHANNEL.source_id,
        source_article_id="1",
        channel="industry",
        canonical_url="https://example.com/1",
        title="\u745e\u8428\u7535\u5b50\u4e2d\u56fd\u4e1a\u52a1\u8fdb\u5c55",
        published_at="2026-07-29",
        discovered_at="2026-07-29T12:00:00+00:00",
        cursor_value="1",
        listing_page=CHANNEL.url,
        listing_position=1,
        content_hash="index",
        discovery_method="exact",
    )
    article = CleanArticle(
        index=index,
        clean_body=f"{historical}{current}",
        content_hash="body",
    )
    response = json.dumps(
        {
            "events": [
                {
                    "company": "\u745e\u8428\u7535\u5b50",
                    "event_type": "executive_change",
                    "industry_tags": ["semiconductor"],
                    "funding_round": "",
                    "funding_amount": "",
                    "cumulative_funding_amount": "",
                    "investors": [],
                    "event_status": "completed",
                    "event_summary": historical,
                    "evidence_quotes": [historical],
                    "confidence": "high",
                },
                {
                    "company": "\u745e\u8428\u7535\u5b50",
                    "event_type": "factory_or_capacity",
                    "industry_tags": ["semiconductor"],
                    "funding_round": "",
                    "funding_amount": "",
                    "cumulative_funding_amount": "",
                    "investors": [],
                    "event_status": "completed",
                    "event_summary": current,
                    "evidence_quotes": [current],
                    "confidence": "high",
                },
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )

    events = MiniMaxSemanticProcessor(Runner(response)).process(
        CHANNEL,
        article,
        [
            SemanticEvent(
                source_id=CHANNEL.source_id,
                source_article_id="1",
                canonical_url=article.index.canonical_url,
                company_mentions=("瑞萨电子",),
                canonical_company="瑞萨电子",
                event_type="factory_or_capacity",
                event_date="2026-07-29",
                industry_tags=("semiconductor",),
                event_summary=current,
                evidence_quotes=(current,),
                processor="rules:test",
                content_hash="body",
                event_status="completed",
            )
        ],
    )

    assert len(events) == 1
    assert events[0].event_type == "factory_or_capacity"
    assert events[0].evidence_quotes == (current,)
