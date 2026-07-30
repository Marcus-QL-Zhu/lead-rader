import json

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


class _Runner:
    def __init__(self, payload):
        self.response = json.dumps(payload, ensure_ascii=False)
        self.calls = 0

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        self.calls += 1
        return self.response


def test_invalid_subject_event_does_not_discard_later_valid_event():
    correct_company = "月之暗面"
    wrong_company = "全球摇人"
    correct_quote = "月之暗面即将启动新一轮融资。"
    wrong_quote = "月之暗面即将启动新一轮融资。"
    channel = SourceChannel(
        source_id="test",
        name="test",
        url="https://example.com",
        source_grade="B",
        event_prior=("funding",),
        allowed_hosts=("example.com",),
    )
    article = CleanArticle(
        index=SourceArticleIndex(
            source_id="test",
            source_article_id="1",
            channel="latest",
            canonical_url="https://example.com/1",
            title="全球AI人才与融资观察",
            published_at="2026-07-29",
            discovered_at="2026-07-29T00:00:00+00:00",
            cursor_value="1",
            listing_page=channel.url,
            listing_position=1,
            content_hash="index",
            discovery_method="exact",
        ),
        clean_body=(
            f"{wrong_company}是文章栏目标题。{correct_quote}"
        ),
        content_hash="article",
    )
    base = {
        "event_type": "funding",
        "industry_tags": ["artificial_intelligence"],
        "funding_round": "",
        "funding_amount": "",
        "cumulative_funding_amount": "",
        "investors": [],
        "event_status": "started",
        "event_summary": correct_quote,
        "confidence": "high",
    }
    payload = {
        "events": [
            {
                **base,
                "company": wrong_company,
                "evidence_quotes": [wrong_quote],
            },
            {
                **base,
                "company": correct_company,
                "evidence_quotes": [correct_quote],
            },
        ],
        "ambiguities": [],
    }
    runner = _Runner(payload)
    processor = MiniMaxSemanticProcessor(runner)

    seed = SemanticEvent(
        source_id="test",
        source_article_id="1",
        canonical_url=article.index.canonical_url,
        company_mentions=(correct_company,),
        canonical_company=correct_company,
        event_type="funding",
        event_date="2026-07-29",
        industry_tags=("artificial_intelligence",),
        event_summary=correct_quote,
        evidence_quotes=(correct_quote,),
        processor="rules:test",
        content_hash="article",
        event_status="started",
    )
    events = processor.process(channel, article, [seed])

    assert runner.calls == 1
    assert processor.last_audit["status"] == "accepted"
    assert [event.canonical_company for event in events] == [correct_company]
