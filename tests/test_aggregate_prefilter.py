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
from ht_lead_radar.aggregate_adapters.sites.cls import ClsAdapter


CHANNEL = SourceChannel(
    source_id="prefilter-test",
    name="prefilter test",
    url="https://example.com/list",
    source_grade="B",
    event_prior=("executive_change",),
    allowed_hosts=("example.com",),
    allowed_path_patterns=(r"/detail/\d+",),
)


def _index(article_id: str, title: str, summary: str) -> SourceArticleIndex:
    return SourceArticleIndex(
        source_id=CHANNEL.source_id,
        source_article_id=article_id,
        channel="stream",
        canonical_url=f"https://example.com/detail/{article_id}",
        title=title,
        published_at="2026-07-29T08:00:00+08:00",
        discovered_at="2026-07-30T00:00:00+00:00",
        cursor_value=article_id,
        listing_page=CHANNEL.url,
        listing_position=int(article_id),
        content_hash=f"index-{article_id}",
        discovery_method="fixture",
        summary=summary,
    )


class HighFrequencyFixtureAdapter(AggregateAdapter):
    adapter_id = "prefilter-fixture"
    channels = (CHANNEL,)

    def parse_listing(self, channel, html, context):
        del channel, html, context
        return [
            _index("1", "market wrap", "price commentary only"),
            _index("2", "company event", "event cue and complete text"),
        ]

    def parse_detail(self, channel, index, html, context):
        del channel, html, context
        return CleanArticle(
            index=index,
            clean_body=index.summary,
            content_hash=f"body-{index.source_article_id}",
        )

    def rule_events(self, channel, article) -> list[SemanticEvent]:
        del channel, article
        return []

    def should_fetch_detail(self, channel, index):
        del channel
        return "event cue" in index.summary


def test_prefilter_indexes_every_item_but_skips_detail_and_llm(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return b"<html>fixture</html>"

    state_db = tmp_path / "state.sqlite3"
    coordinator = DedicatedAggregateCoordinator(
        state_db=state_db,
        registry=DedicatedAdapterRegistry((HighFrequencyFixtureAdapter(),)),
        fetch=fetch,
    )

    first = coordinator.collect_source(CHANNEL.source_id, "hardtech")
    calls.clear()
    second = coordinator.collect_source(CHANNEL.source_id, "hardtech")

    assert first.run.listing_count == 2
    assert first.run.incremental_count == 2
    assert first.run.prefiltered_count == 1
    assert first.run.detail_success_count == 1
    assert second.run.incremental_count == 0
    assert calls == [CHANNEL.url]

    connection = sqlite3.connect(state_db)
    rows = connection.execute(
        """
        SELECT source_article_id, article_json
        FROM aggregate_clean_articles
        ORDER BY source_article_id
        """
    ).fetchall()
    audits = connection.execute(
        """
        SELECT source_article_id, audit_json
        FROM aggregate_semantic_attempts
        ORDER BY source_article_id
        """
    ).fetchall()
    connection.close()

    assert len(rows) == 2
    assert json.loads(rows[0][1])["fetch_status"] == "prefiltered"
    assert json.loads(rows[1][1])["fetch_status"] == "ok"
    assert json.loads(audits[0][1])["status"] == "prefiltered"
    assert json.loads(audits[1][1])["status"] == "no_rule_seed"


def test_cls_prefilter_keeps_executive_change_and_rejects_market_noise():
    adapter = ClsAdapter()
    executive = _index(
        "1",
        "\u745e\u8428\u7535\u5b50\u4e2d\u56fd\u4efb\u547d\u65b0\u603b\u88c1",
        "\u745e\u8428\u7535\u5b50\u4e2d\u56fd\u5ba3\u5e03\u4efb\u547d\u65b0\u4e00\u53f7\u4f4d",
    )
    noise = _index(
        "2",
        "\u5e02\u573a\u6536\u76d8",
        "\u4e3b\u8981\u6307\u6570\u6da8\u8dcc\u4e92\u73b0",
    )

    assert adapter.should_fetch_detail(adapter.channels[0], executive)
    assert not adapter.should_fetch_detail(adapter.channels[0], noise)
