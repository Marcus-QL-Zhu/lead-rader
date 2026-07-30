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
