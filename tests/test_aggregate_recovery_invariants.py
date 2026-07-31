from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import sqlite3

from ht_lead_radar.aggregate_adapters.base import AggregateAdapter
from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.storage import AggregateStateStore


CHANNEL = SourceChannel(
    source_id="recovery-test",
    name="recovery",
    url="https://example.com/list",
    source_grade="B",
    event_prior=("technical_milestone",),
    allowed_hosts=("example.com",),
    allowed_path_patterns=(r"/detail/1",),
)


class _RecoveryAdapter(AggregateAdapter):
    adapter_id = "recovery"
    channels = (CHANNEL,)
    minimum_listing_count = 0

    def __init__(self):
        self.listed = True
        self.detail_complete = False

    def parse_listing(self, channel, html, context):
        del channel, html
        if not self.listed:
            return []
        return [self._index(context.now)]

    @staticmethod
    def _index(now):
        return SourceArticleIndex(
            source_id=CHANNEL.source_id,
            source_article_id="1",
            channel="news",
            canonical_url="https://example.com/detail/1",
            title="\u661f\u6cb3\u79d1\u6280\u53d1\u5e03\u65b0\u4ea7\u54c1",
            published_at="2026-07-31",
            discovered_at=now.isoformat(),
            cursor_value="1",
            listing_page=CHANNEL.url,
            listing_position=1,
            content_hash="index-1",
            discovery_method="fixture",
            summary="\u661f\u6cb3\u79d1\u6280\u53d1\u5e03\u65b0\u4ea7\u54c1\u5e76\u8fdb\u5165\u5ba2\u6237\u9a8c\u8bc1\u9636\u6bb5\u3002",
        )

    def parse_detail(self, channel, index, html, context):
        del channel, html, context
        body = (
            "\u661f\u6cb3\u79d1\u6280\u53d1\u5e03\u65b0\u4ea7\u54c1\u5e76\u8fdb\u5165\u5ba2\u6237\u9a8c\u8bc1\u9636\u6bb5\u3002"
            if self.detail_complete
            else index.summary
        )
        return CleanArticle(
            index=index,
            clean_body=body,
            fetch_status="ok" if self.detail_complete else "listing_fallback",
            failure_reason="fixture_detail_unavailable" if not self.detail_complete else "",
            content_hash=sha256(body.encode()).hexdigest(),
        )

    def rule_events(self, channel, article):
        return [
            SemanticEvent(
                source_id=channel.source_id,
                source_article_id=article.index.source_article_id,
                canonical_url=article.index.canonical_url,
                company_mentions=("\u661f\u6cb3\u79d1\u6280",),
                canonical_company="\u661f\u6cb3\u79d1\u6280",
                event_type="technical_milestone",
                event_date="2026-07-31",
                industry_tags=("hardtech",),
                event_summary=article.clean_body,
                evidence_quotes=(article.clean_body,),
                confidence="high",
                processor="rules:test",
                content_hash=article.content_hash,
            )
        ]


class _Runner:
    def __init__(self):
        self.calls = 0

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        self.calls += 1
        quote = "星河科技发布新产品并进入客户验证阶段。"
        return json.dumps(
            {
                "events": [
                    {
                        "company": "星河科技",
                        "event_type": "technical_milestone",
                        "industry_tags": ["hardtech"],
                        "funding_round": "",
                        "funding_amount": "",
                        "cumulative_funding_amount": "",
                        "investors": [],
                        "event_status": "completed",
                        "event_summary": quote,
                        "evidence_quotes": [quote],
                        "confidence": "high",
                    }
                ],
                "ambiguities": [],
            },
            ensure_ascii=False,
        )


def test_open_dead_letter_is_drained_after_item_leaves_listing(tmp_path):
    adapter = _RecoveryAdapter()
    routes = {
        CHANNEL.url: b"listing",
        "https://example.com/detail/1": b"detail",
    }
    database = tmp_path / "recovery.sqlite3"
    first = DedicatedAggregateCoordinator(
        state_db=database,
        registry=DedicatedAdapterRegistry((adapter,)),
        fetch=lambda url: routes[url],
        now=datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc),
    ).collect_source(CHANNEL.source_id, "hardtech")
    assert first.run.status == "partial"

    adapter.listed = False
    adapter.detail_complete = True
    second = DedicatedAggregateCoordinator(
        state_db=database,
        registry=DedicatedAdapterRegistry((adapter,)),
        fetch=lambda url: routes[url],
        now=datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc),
    ).collect_source(CHANNEL.source_id, "hardtech")

    assert second.run.status == "ok"
    assert second.run.listing_count == 0
    assert second.run.detail_success_count == 1
    with AggregateStateStore(database) as store:
        assert store.health()["open_dead_letter_count"] == 0


def test_unchanged_detail_after_recheck_does_not_call_minimax_again(tmp_path):
    adapter = _RecoveryAdapter()
    adapter.detail_complete = True
    runner = _Runner()
    routes = {
        CHANNEL.url: b"listing",
        "https://example.com/detail/1": b"detail",
    }
    database = tmp_path / "idempotent.sqlite3"
    first_now = datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)
    first = DedicatedAggregateCoordinator(
        state_db=database,
        registry=DedicatedAdapterRegistry((adapter,)),
        fetch=lambda url: routes[url],
        llm_runner=runner,
        now=first_now,
    ).collect_source(CHANNEL.source_id, "hardtech")
    assert first.run.status == "ok"
    assert runner.calls == 1

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE aggregate_clean_articles SET fetched_at = ?",
            (first_now.isoformat(),),
        )
        connection.commit()

    second = DedicatedAggregateCoordinator(
        state_db=database,
        registry=DedicatedAdapterRegistry((adapter,)),
        fetch=lambda url: routes[url],
        llm_runner=runner,
        now=first_now + timedelta(hours=13),
    ).collect_source(CHANNEL.source_id, "hardtech")

    assert second.run.status == "ok"
    assert second.run.incremental_count == 1
    assert runner.calls == 1
    assert len(second.evidence) == len(first.evidence) == 1
