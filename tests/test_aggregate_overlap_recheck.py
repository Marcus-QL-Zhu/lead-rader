from datetime import datetime, timezone

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SourceArticleIndex,
)
from ht_lead_radar.aggregate_adapters.storage import AggregateStateStore


def _index() -> SourceArticleIndex:
    return SourceArticleIndex(
        source_id="source",
        source_article_id="article",
        channel="news",
        canonical_url="https://example.com/news/article",
        title="A sufficiently long article title",
        published_at="2026-07-29",
        discovered_at="2026-07-29T00:00:00+00:00",
        cursor_value="article",
        listing_page="https://example.com/news",
        listing_position=1,
        content_hash="listing-hash",
        discovery_method="exact",
    )


def test_recent_article_is_rechecked_but_old_article_keeps_cache(tmp_path):
    index = _index()
    article = CleanArticle(
        index=index,
        clean_body="A source body long enough for the cache contract.",
        content_hash="article-hash",
    )
    with AggregateStateStore(tmp_path / "state.sqlite3") as store:
        store.store_article(article)
        store.connection.execute(
            """
            UPDATE aggregate_clean_articles
            SET fetched_at = '2026-07-29T01:00:00+00:00'
            """
        )
        store.connection.commit()

        assert not store.article_is_current(
            index,
            now=datetime(2026, 7, 29, 14, tzinfo=timezone.utc),
        )
        assert store.article_is_current(
            index,
            now=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )
