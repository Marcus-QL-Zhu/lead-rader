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


def test_claim_cache_namespace_and_mode_guards(tmp_path):
    index = _index()
    processor = MiniMaxSemanticProcessor(
        Runner(),
        claim_centric_v27=True,
        strict_claim_contract=True,
    )
    audit = {
        "source_id": index.source_id,
        "source_article_id": index.source_article_id,
        "prompt_version": processor.semantic_prompt_version,
        "model_identity": processor.model_identity,
        "index_content_hash": index.content_hash,
        "claim_centric_v27": True,
        "strict_claim_contract": True,
        "status": "accepted",
    }
    assert processor.semantic_prompt_version != PROMPT_VERSION
    assert processor.cache_key.startswith(f"{processor.semantic_prompt_version}|")
    with AggregateStateStore(tmp_path / "state.sqlite3") as store:
        store.store_semantic_audit(audit)
        assert store.semantic_is_current(
            index,
            prompt_version=processor.semantic_prompt_version,
            model_identity=processor.model_identity,
            claim_centric_v27=True,
            strict_claim_contract=True,
        )
        assert not store.semantic_is_current(
            index,
            prompt_version=processor.semantic_prompt_version,
            model_identity=processor.model_identity,
            claim_centric_v27=True,
            strict_claim_contract=False,
        )
        assert not store.semantic_is_current(
            index,
            prompt_version=PROMPT_VERSION,
            model_identity=processor.model_identity,
            claim_centric_v27=True,
            strict_claim_contract=True,
        )
    with AggregateStateStore(tmp_path / "legacy.sqlite3") as legacy_store:
        legacy_store.store_semantic_audit(
            {
                **audit,
                "prompt_version": PROMPT_VERSION,
                "claim_centric_v27": False,
                "strict_claim_contract": False,
            }
        )
        assert not legacy_store.semantic_is_current(
            index,
            prompt_version=processor.semantic_prompt_version,
            model_identity=processor.model_identity,
            claim_centric_v27=True,
            strict_claim_contract=True,
        )


def test_claim_mode_without_runner_keeps_rules_only_cache_namespace():
    processor = MiniMaxSemanticProcessor(
        None,
        claim_centric_v27=True,
        strict_claim_contract=True,
    )

    assert processor.semantic_prompt_version == PROMPT_VERSION
    assert processor.cache_key.startswith("aggregate-semantic-v27-claim-centric-r5|")


def test_no_claims_is_a_current_terminal_cache_status(tmp_path):
    index = _index()
    with AggregateStateStore(tmp_path / "state.sqlite3") as store:
        store.store_semantic_audit(
            {
                "source_id": index.source_id,
                "source_article_id": index.source_article_id,
                "prompt_version": "aggregate-semantic-v27-claim-centric-r5",
                "model_identity": "minimax/MiniMax-M3",
                "index_content_hash": index.content_hash,
                "claim_centric_v27": True,
                "strict_claim_contract": True,
                "status": "no_claims",
            }
        )
        assert store.semantic_is_current(
            index,
            prompt_version="aggregate-semantic-v27-claim-centric-r5",
            model_identity="minimax/MiniMax-M3",
            claim_centric_v27=True,
            strict_claim_contract=True,
        )


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


def test_prior_semantic_attempt_is_reusable_across_prompt_versions(tmp_path):
    index = _index()
    with AggregateStateStore(tmp_path / "state.sqlite3") as store:
        store.store_semantic_audit(
            {
                "source_id": index.source_id,
                "source_article_id": index.source_article_id,
                "prompt_version": "aggregate-semantic-v23",
                "model_identity": "minimax/MiniMax-M3",
                "index_content_hash": index.content_hash,
                "status": "accepted",
            }
        )
        assert store.has_prior_semantic_attempt(index)
        store.store_semantic_audit(
            {
                "source_id": index.source_id,
                "source_article_id": index.source_article_id,
                "prompt_version": "aggregate-semantic-v24",
                "model_identity": "minimax/MiniMax-M3",
                "index_content_hash": index.content_hash,
                "status": "fallback_to_rules",
            }
        )
        # A later failed attempt must not make an otherwise unchanged article
        # permanently bypass the current prompt migration.
        with store.connection:
            store.connection.execute(
                "DELETE FROM aggregate_semantic_attempts WHERE prompt_version = ?",
                ("aggregate-semantic-v23",),
            )
        assert not store.has_prior_semantic_attempt(index)
        assert not store.has_prior_semantic_attempt(
            replace(index, content_hash="changed")
        )


def test_claim_dead_letters_are_independent_and_resolved_by_claim_id(tmp_path):
    index = _index()
    with AggregateStateStore(tmp_path / "state.sqlite3") as store:
        store.sync_semantic_claim_dead_letters(
            source_id=index.source_id,
            source_article_id=index.source_article_id,
            canonical_url=index.canonical_url,
            failed_claim_ids=["c_1", "c_2"],
            error="two claims remain unresolved",
        )
        assert store.health()["open_dead_letter_count"] == 2

        store.sync_semantic_claim_dead_letters(
            source_id=index.source_id,
            source_article_id=index.source_article_id,
            canonical_url=index.canonical_url,
            failed_claim_ids=["c_2"],
            error="one claim remains unresolved",
        )
        assert store.health()["open_dead_letter_count"] == 1

        store.sync_semantic_claim_dead_letters(
            source_id=index.source_id,
            source_article_id=index.source_article_id,
            canonical_url=index.canonical_url,
            failed_claim_ids=[],
            error="",
        )
        assert store.health()["open_dead_letter_count"] == 0
