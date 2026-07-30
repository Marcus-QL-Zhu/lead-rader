from dataclasses import replace

from ht_lead_radar.aggregate_adapters.models import SourceArticleIndex
from ht_lead_radar.aggregate_adapters.semantic import (
    MiniMaxSemanticProcessor,
    PROMPT_VERSION,
)
from ht_lead_radar.aggregate_adapters.storage import AggregateStateStore


class Runner:
    config = type(
        "Config",
        (),
        {"provider": "minimax", "model": "MiniMax-M3"},
    )()

    def run(self, prompt, *, session_id, system_prompt=""):
        del prompt, session_id, system_prompt
        return '{"events":[],"ambiguities":[]}'


def _index(content_hash: str = "listing-hash") -> SourceArticleIndex:
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
        content_hash=content_hash,
        discovery_method="exact",
    )


def test_processor_cache_key_includes_prompt_and_model():
    processor = MiniMaxSemanticProcessor(Runner())

    assert processor.model_identity == "minimax/MiniMax-M3"
    assert processor.cache_key == f"{PROMPT_VERSION}|minimax/MiniMax-M3"


def test_semantic_cache_invalidates_on_model_or_index_change(tmp_path):
    index = _index()
    audit = {
        "source_id": index.source_id,
        "source_article_id": index.source_article_id,
        "prompt_version": PROMPT_VERSION,
        "model_identity": "minimax/MiniMax-M3",
        "index_content_hash": index.content_hash,
        "status": "accepted",
    }
    with AggregateStateStore(tmp_path / "state.sqlite3") as store:
        store.store_semantic_audit(audit)

        assert store.semantic_is_current(
            index,
            prompt_version=PROMPT_VERSION,
            model_identity="minimax/MiniMax-M3",
        )
        assert not store.semantic_is_current(
            index,
            prompt_version=PROMPT_VERSION,
            model_identity="minimax/MiniMax-M2.7",
        )
        assert not store.semantic_is_current(
            replace(index, content_hash="changed"),
            prompt_version=PROMPT_VERSION,
            model_identity="minimax/MiniMax-M3",
        )

        store.store_semantic_audit({**audit, "status": "fallback_to_rules"})
        assert not store.semantic_is_current(
            index,
            prompt_version=PROMPT_VERSION,
            model_identity="minimax/MiniMax-M3",
        )
