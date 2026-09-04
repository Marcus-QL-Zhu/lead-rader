import json

from ht_lead_radar.aggregate_adapters.models import (
    SemanticEvent,
    SourceArticleIndex,
)
from ht_lead_radar.aggregate_adapters.storage import AggregateStateStore


def test_dead_letter_can_be_resolved_after_retry(tmp_path):
    state = tmp_path / "state.sqlite3"
    with AggregateStateStore(state) as store:
        store.record_dead_letter(
            source_id="source",
            source_article_id="article",
            canonical_url="https://example.com/article",
            stage="semantic_validation",
            error="first failure",
        )
        assert store.health()["open_dead_letter_count"] == 1

        store.resolve_dead_letter(
            source_id="source",
            source_article_id="article",
            stage="semantic_validation",
        )

        assert store.health()["open_dead_letter_count"] == 0


def test_dead_letter_recovery_uses_relational_canonical_url_authority(tmp_path):
    state = tmp_path / "state.sqlite3"
    index = SourceArticleIndex(
        source_id="source",
        source_article_id="2026-08-12-7",
        channel="news",
        canonical_url=(
            "https://www.jiqizhixin.com/articles/2026-08-12-7"
        ),
        title="A sufficiently long article title",
        published_at="2026-08-12",
        discovered_at="2026-08-12T00:00:00+00:00",
        cursor_value="2026-08-12-7",
        listing_page="https://www.jiqizhixin.com/",
        listing_position=1,
        content_hash="listing-hash",
        discovery_method="exact",
    )
    with AggregateStateStore(state) as store:
        store.upsert_index(index)
        store.record_dead_letter(
            source_id=index.source_id,
            source_article_id=index.source_article_id,
            canonical_url=index.canonical_url,
            stage="detail_fetch",
            error="temporary failure",
        )
        raw = index.to_dict()
        raw["canonical_url"] = (
            "https://www.jiqizhixin.com/articles/[redacted-phone]"
        )
        with store.connection:
            store.connection.execute(
                "UPDATE aggregate_article_index SET index_json=? "
                "WHERE source_id=? AND source_article_id=?",
                (
                    json.dumps(raw),
                    index.source_id,
                    index.source_article_id,
                ),
            )

        recovered = store.open_dead_letter_indexes(index.source_id)

    assert len(recovered) == 1
    assert recovered[0].canonical_url == index.canonical_url


def test_event_reads_use_relational_canonical_url_authority(tmp_path):
    state = tmp_path / "state.sqlite3"
    index = SourceArticleIndex(
        source_id="source",
        source_article_id="2026-08-12-7",
        channel="news",
        canonical_url=(
            "https://www.jiqizhixin.com/articles/2026-08-12-7"
        ),
        title="A sufficiently long article title",
        published_at="2026-08-12",
        discovered_at="2026-08-12T00:00:00+00:00",
        cursor_value="2026-08-12-7",
        listing_page="https://www.jiqizhixin.com/",
        listing_position=1,
        content_hash="listing-hash",
        discovery_method="exact",
    )
    event = SemanticEvent(
        source_id=index.source_id,
        source_article_id=index.source_article_id,
        canonical_url=index.canonical_url,
        company_mentions=("星河科技",),
        canonical_company="星河科技",
        event_type="technical_milestone",
        event_date="2026-08-12",
        industry_tags=("hardtech",),
        content_hash="body-hash",
    )
    with AggregateStateStore(state) as store:
        store.upsert_index(index)
        store.store_events(index.source_id, index.source_article_id, [event])
        with store.connection:
            row = store.connection.execute(
                "SELECT rowid, event_json FROM aggregate_semantic_events"
            ).fetchone()
            raw = json.loads(str(row["event_json"]))
            raw["canonical_url"] = (
                "https://www.jiqizhixin.com/articles/[redacted-phone]"
            )
            store.connection.execute(
                "UPDATE aggregate_semantic_events SET event_json=? WHERE rowid=?",
                (json.dumps(raw), int(row["rowid"])),
            )

        recovered = store.events_for_article(
            index.source_id,
            index.source_article_id,
        )

    assert len(recovered) == 1
    assert recovered[0].canonical_url == index.canonical_url
