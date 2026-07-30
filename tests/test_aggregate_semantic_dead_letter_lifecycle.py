import json

from ht_lead_radar.aggregate_adapters.base import (
    AggregateAdapter,
)
from ht_lead_radar.aggregate_adapters.coordinator import (
    DedicatedAggregateCoordinator,
)
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry


QUOTE = "\u661f\u6cb3\u82af\u7247\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\u3002"


class Adapter(AggregateAdapter):
    adapter_id = "test"
    channels = (
        SourceChannel(
            source_id="test-source",
            name="test",
            url="https://example.com/list",
            source_grade="B",
            event_prior=("funding",),
            allowed_hosts=("example.com",),
        ),
    )

    def parse_listing(self, channel, html, context):
        del html
        item = SourceArticleIndex(
            source_id=channel.source_id,
            source_article_id="1",
            channel="funding",
            canonical_url="https://example.com/1",
            title=QUOTE,
            published_at="2026-07-29",
            discovered_at=context.now.isoformat(),
            cursor_value="1",
            listing_page=channel.url,
            listing_position=1,
            content_hash="index",
            discovery_method="exact",
        )
        self.validate_listing(channel, [item])
        return [item]

    def parse_detail(self, channel, index, html, context):
        del channel, html, context
        return CleanArticle(
            index=index,
            clean_body=QUOTE,
            content_hash="article",
        )

    def rule_events(self, channel, article):
        del channel
        return [
            SemanticEvent(
                source_id=article.index.source_id,
                source_article_id=article.index.source_article_id,
                canonical_url=article.index.canonical_url,
                company_mentions=("\u661f\u6cb3\u82af\u7247",),
                canonical_company="\u661f\u6cb3\u82af\u7247",
                event_type="funding",
                event_date="2026-07-29",
                industry_tags=("semiconductor",),
                funding_round="A\u8f6e",
                funding_amount="1\u4ebf\u5143",
                evidence_quotes=(QUOTE,),
                content_hash=article.content_hash,
            )
        ]


class Runner:
    def __init__(self, response):
        self.response = response

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return self.response


def _payload(company):
    return json.dumps(
        {
            "events": [
                {
                    "company": company,
                    "event_type": "funding",
                    "industry_tags": ["semiconductor"],
                    "funding_round": "A\u8f6e",
                    "funding_amount": "1\u4ebf\u5143",
                    "cumulative_funding_amount": "",
                    "investors": [],
                    "event_status": "completed",
                    "event_summary": QUOTE,
                    "evidence_quotes": [QUOTE],
                    "confidence": "high",
                }
            ],
            "ambiguities": [],
        },
        ensure_ascii=False,
    )


def test_semantic_failure_is_visible_and_successful_retry_resolves_it(tmp_path):
    state = tmp_path / "state.sqlite3"
    registry = DedicatedAdapterRegistry((Adapter(),))
    bad = DedicatedAggregateCoordinator(
        state_db=state,
        registry=registry,
        fetch=lambda _url: b"<html></html>",
        llm_runner=Runner(_payload("\u4e0d\u5b58\u5728\u516c\u53f8")),
    )

    failed = bad.collect_source("test-source", "semiconductor")

    assert failed.run.status == "partial"
    assert failed.run.semantic_failure_count == 1
    assert bad.health()["open_dead_letter_count"] == 1

    good = DedicatedAggregateCoordinator(
        state_db=state,
        registry=registry,
        fetch=lambda _url: b"<html></html>",
        llm_runner=Runner(_payload("\u661f\u6cb3\u82af\u7247")),
    )
    retried = good.collect_source(
        "test-source",
        "semiconductor",
        force_reprocess=True,
    )

    assert retried.run.status == "ok"
    assert retried.run.semantic_failure_count == 0
    assert good.health()["open_dead_letter_count"] == 0
