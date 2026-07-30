import json

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


CHANNEL = SourceChannel(
    source_id="semantic-v5-test",
    name="semantic v5",
    url="https://example.com",
    source_grade="B",
    event_prior=("new_site_or_entity",),
    allowed_hosts=("example.com",),
)


class Runner:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        self.calls += 1
        return next(self.responses)


def _article(body: str) -> CleanArticle:
    return CleanArticle(
        index=SourceArticleIndex(
            source_id=CHANNEL.source_id,
            source_article_id="1",
            channel="industry",
            canonical_url="https://example.com/1",
            title="\u683c\u5f0f\u5854\u79d1\u6280\u6700\u65b0\u8fdb\u5c55",
            published_at="2026-07-14",
            discovered_at="2026-07-14T12:00:00+08:00",
            cursor_value="1",
            listing_page=CHANNEL.url,
            listing_position=1,
            content_hash="index",
            discovery_method="exact",
        ),
        clean_body=body,
        content_hash="body",
    )


def _event_payload(*, ambiguities):
    return json.dumps(
        {
            "events": [{
                "company": "\u683c\u5f0f\u5854\u79d1\u6280",
                "event_type": "new_site_or_entity",
                "industry_tags": ["embodied_ai"],
                "funding_round": "",
                "funding_amount": "",
                "cumulative_funding_amount": "",
                "investors": [],
                "event_status": "completed",
                "event_summary": "\u683c\u5f0f\u5854\u4e0a\u6d77\u603b\u90e8\u4e5f\u6b63\u5f0f\u542f\u7528",
                "evidence_quotes": ["\u683c\u5f0f\u5854\u4e0a\u6d77\u603b\u90e8\u4e5f\u6b63\u5f0f\u542f\u7528"],
                "confidence": "high",
            }],
            "ambiguities": ambiguities,
        },
        ensure_ascii=False,
    )


def _seed(article: CleanArticle) -> SemanticEvent:
    quote = "\u683c\u5f0f\u5854\u4e0a\u6d77\u603b\u90e8\u4e5f\u6b63\u5f0f\u542f\u7528"
    return SemanticEvent(
        source_id=CHANNEL.source_id,
        source_article_id="1",
        canonical_url=article.index.canonical_url,
        company_mentions=("\u683c\u5f0f\u5854\u79d1\u6280", "\u683c\u5f0f\u5854"),
        canonical_company="\u683c\u5f0f\u5854\u79d1\u6280",
        event_type="new_site_or_entity",
        event_date="2026-07-14",
        industry_tags=("embodied_ai",),
        event_summary=quote,
        evidence_quotes=(quote,),
        processor="rules:test",
        content_hash="body",
        event_status="completed",
    )


def test_later_interview_does_not_reemit_early_month_event():
    article = _article(
        "7\u6708\u521d\uff0c\u683c\u5f0f\u5854\u79d1\u6280\u5b8c\u6210\u65b0\u4e00\u8f6e\u878d\u8d44\u3002"
        "\u4e0e\u6b64\u540c\u65f6\uff0c\u683c\u5f0f\u5854\u4e0a\u6d77\u603b\u90e8\u4e5f\u6b63\u5f0f\u542f\u7528\u3002"
        "\u4eca\u5929\u516c\u53f8\u63a5\u53d7\u91c7\u8bbf\u3002"
    )
    processor = MiniMaxSemanticProcessor(
        Runner([_event_payload(ambiguities=[])])
    )

    assert processor.process(CHANNEL, article, [_seed(article)]) == []


def test_non_string_ambiguity_requires_repair():
    article = _article(
        "7\u670814\u65e5\uff0c\u683c\u5f0f\u5854\u4e0a\u6d77\u603b\u90e8\u4e5f\u6b63\u5f0f\u542f\u7528\u3002"
    )
    invalid = _event_payload(ambiguities=[{"field": "event_date"}])
    repaired = _event_payload(
        ambiguities=["event_date_needs_confirmation"]
    )
    runner = Runner([invalid, repaired])
    processor = MiniMaxSemanticProcessor(runner)

    events = processor.process(CHANNEL, article, [_seed(article)])

    assert runner.calls == 2
    assert len(events) == 1
    assert events[0].ambiguities == ("event_date_needs_confirmation",)
    assert processor.last_audit["status"] == "repaired"