from dataclasses import replace
import json

import pytest

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
)
from ht_lead_radar.aggregate_adapters.semantic import (
    CLAIM_CONTRACT_VERSION,
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


def _store_complete_audit(
    store: AggregateStateStore,
    index: SourceArticleIndex,
    audit: dict,
    *,
    article_hash: str = "body-stable",
) -> None:
    """Persist the complete body/events/audit unit required by cache reuse."""

    audit.setdefault("article_content_hash", article_hash)
    audit.setdefault("final_event_count", 0)
    store.store_article(
        CleanArticle(
            index=index,
            clean_body="A complete persisted article body for cache validation.",
            content_hash=article_hash,
        )
    )
    store.store_events(index.source_id, index.source_article_id, [])
    store.store_semantic_audit(audit)


def test_processor_cache_key_includes_prompt_model_and_contract():
    processor = MiniMaxSemanticProcessor(Runner())

    assert processor.model_identity == "minimax/MiniMax-M3"
    assert processor.cache_key == (
        f"{PROMPT_VERSION}|minimax/MiniMax-M3|{CLAIM_CONTRACT_VERSION}"
    )


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
        "claim_contract_version": processor.semantic_claim_contract_version,
        "index_content_hash": index.content_hash,
        "claim_centric_v27": True,
        "strict_claim_contract": True,
        "status": "accepted",
    }
    assert processor.semantic_prompt_version != PROMPT_VERSION
    assert processor.cache_key.startswith(f"{processor.semantic_prompt_version}|")
    with AggregateStateStore(tmp_path / "state.sqlite3") as store:
        _store_complete_audit(store, index, audit)
        assert store.semantic_is_current(
            index,
            prompt_version=processor.semantic_prompt_version,
            model_identity=processor.model_identity,
            claim_contract_version=processor.semantic_claim_contract_version,
            claim_centric_v27=True,
            strict_claim_contract=True,
        )
        assert not store.semantic_is_current(
            index,
            prompt_version=processor.semantic_prompt_version,
            model_identity=processor.model_identity,
            claim_contract_version=processor.semantic_claim_contract_version,
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
        assert not store.semantic_is_current(
            index,
            prompt_version=processor.semantic_prompt_version,
            model_identity=processor.model_identity,
            claim_contract_version="another-contract",
            claim_centric_v27=True,
            strict_claim_contract=True,
        )
    with AggregateStateStore(tmp_path / "legacy.sqlite3") as legacy_store:
        _store_complete_audit(
            legacy_store,
            index,
            {
                **audit,
                "prompt_version": PROMPT_VERSION,
                "claim_centric_v27": False,
                "strict_claim_contract": False,
            },
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
        _store_complete_audit(
            store,
            index,
            {
                "source_id": index.source_id,
                "source_article_id": index.source_article_id,
                "prompt_version": "aggregate-semantic-v27-claim-centric-r5",
                "model_identity": "minimax/MiniMax-M3",
                "index_content_hash": index.content_hash,
                "claim_centric_v27": True,
                "strict_claim_contract": True,
                "status": "no_claims",
            },
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
        _store_complete_audit(store, index, audit)

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


def test_semantic_cache_rebind_requires_unchanged_article_and_current_contract(
    tmp_path,
):
    original = _index("listing-old")
    changed_listing = replace(original, content_hash="listing-new")
    audit = {
        "source_id": original.source_id,
        "source_article_id": original.source_article_id,
        "prompt_version": PROMPT_VERSION,
        "model_identity": "minimax/MiniMax-M3",
        "index_content_hash": original.content_hash,
        "article_content_hash": "body-stable",
        "claim_centric_v27": False,
        "strict_claim_contract": False,
        "status": "accepted",
    }
    with AggregateStateStore(tmp_path / "state.sqlite3") as store:
        _store_complete_audit(store, original, audit)
        assert not store.rebind_semantic_cache(
            changed_listing,
            article_content_hash="body-changed",
            prompt_version=PROMPT_VERSION,
            model_identity="minimax/MiniMax-M3",
            claim_centric_v27=False,
            strict_claim_contract=False,
        )
        assert not store.rebind_semantic_cache(
            changed_listing,
            article_content_hash="body-stable",
            prompt_version=PROMPT_VERSION,
            model_identity="minimax/another-model",
            claim_centric_v27=False,
            strict_claim_contract=False,
        )
        assert store.rebind_semantic_cache(
            changed_listing,
            article_content_hash="body-stable",
            prompt_version=PROMPT_VERSION,
            model_identity="minimax/MiniMax-M3",
            claim_centric_v27=False,
            strict_claim_contract=False,
        )
        assert store.semantic_is_current(
            changed_listing,
            prompt_version=PROMPT_VERSION,
            model_identity="minimax/MiniMax-M3",
            claim_centric_v27=False,
            strict_claim_contract=False,
        )


def test_prior_semantic_attempt_is_reusable_across_prompt_versions(tmp_path):
    index = _index()
    with AggregateStateStore(tmp_path / "state.sqlite3") as store:
        first_audit = {
                "source_id": index.source_id,
                "source_article_id": index.source_article_id,
                "prompt_version": "aggregate-semantic-v23",
                "model_identity": "minimax/MiniMax-M3",
                "index_content_hash": index.content_hash,
                "status": "accepted",
            }
        _store_complete_audit(store, index, first_audit)
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


def test_legacy_v27_audit_hashes_are_recovered_without_model_backfill(tmp_path):
    index = _index()
    processor = MiniMaxSemanticProcessor(
        Runner(),
        claim_centric_v27=True,
        strict_claim_contract=True,
    )
    legacy_audit = {
        "source_id": index.source_id,
        "source_article_id": index.source_article_id,
        "prompt_version": processor.semantic_prompt_version,
        "model_identity": processor.model_identity,
        "claim_contract_version": processor.semantic_claim_contract_version,
        "claim_centric_v27": True,
        "strict_claim_contract": True,
        "status": "accepted",
        "final_event_count": 1,
    }
    with AggregateStateStore(tmp_path / "legacy-v27.sqlite3") as store:
        store.store_article(
            CleanArticle(
                index=index,
                clean_body="A complete legacy V27 article body.",
                content_hash="legacy-body-hash",
            )
        )
        store.store_events(
            index.source_id,
            index.source_article_id,
            [
                SemanticEvent(
                    source_id=index.source_id,
                    source_article_id=index.source_article_id,
                    canonical_url=index.canonical_url,
                    company_mentions=("Legacy Company",),
                    canonical_company="Legacy Company",
                    event_type="funding",
                    event_date="2026-07-29",
                    industry_tags=("hardtech",),
                    event_summary="Legacy Company completed a funding round.",
                    evidence_quotes=(
                        "Legacy Company completed a funding round.",
                    ),
                    processor="minimax",
                    prompt_version=processor.semantic_prompt_version,
                    content_hash="legacy-body-hash",
                )
            ],
        )
        store.store_semantic_audit(legacy_audit)

        assert store.semantic_is_current(
            index,
            prompt_version=processor.semantic_prompt_version,
            model_identity=processor.model_identity,
            claim_contract_version=processor.semantic_claim_contract_version,
            claim_centric_v27=True,
            strict_claim_contract=True,
        )
        row = store.connection.execute(
            "SELECT audit_json FROM aggregate_semantic_attempts"
        ).fetchone()
        recovered = json.loads(str(row["audit_json"]))
        assert recovered["index_content_hash"] == index.content_hash
        assert recovered["article_content_hash"] == "legacy-body-hash"
        assert recovered["legacy_hashes_recovered_at"]


def test_legacy_v27_zero_event_audit_is_not_rebound_to_unknown_body(tmp_path):
    index = _index()
    processor = MiniMaxSemanticProcessor(
        Runner(),
        claim_centric_v27=True,
        strict_claim_contract=True,
    )
    with AggregateStateStore(tmp_path / "legacy-v27-zero.sqlite3") as store:
        store.store_article(
            CleanArticle(
                index=index,
                clean_body="A body with no materialized semantic event.",
                content_hash="zero-event-body",
            )
        )
        store.store_events(index.source_id, index.source_article_id, [])
        store.store_semantic_audit(
            {
                "source_id": index.source_id,
                "source_article_id": index.source_article_id,
                "prompt_version": processor.semantic_prompt_version,
                "model_identity": processor.model_identity,
                "claim_contract_version": processor.semantic_claim_contract_version,
                "claim_centric_v27": True,
                "strict_claim_contract": True,
                "status": "no_claims",
                "final_event_count": 0,
            }
        )

        assert not store.semantic_is_current(
            index,
            prompt_version=processor.semantic_prompt_version,
            model_identity=processor.model_identity,
            claim_contract_version=processor.semantic_claim_contract_version,
            claim_centric_v27=True,
            strict_claim_contract=True,
        )


def test_legacy_v27_zero_event_audit_recovers_with_strict_write_order(tmp_path):
    index = _index()
    processor = MiniMaxSemanticProcessor(
        Runner(),
        claim_centric_v27=True,
        strict_claim_contract=True,
    )
    with AggregateStateStore(tmp_path / "legacy-v27-zero-safe.sqlite3") as store:
        store.store_article(
            CleanArticle(
                index=index,
                clean_body="An unchanged legacy no-claims body.",
                content_hash="zero-event-stable-body",
            )
        )
        store.connection.execute(
            "UPDATE aggregate_clean_articles SET fetched_at = ?",
            ("2026-01-01T00:00:00+00:00",),
        )
        store.connection.commit()
        store.store_events(index.source_id, index.source_article_id, [])
        store.store_semantic_audit(
            {
                "source_id": index.source_id,
                "source_article_id": index.source_article_id,
                "prompt_version": processor.semantic_prompt_version,
                "model_identity": processor.model_identity,
                "claim_contract_version": processor.semantic_claim_contract_version,
                "claim_centric_v27": True,
                "strict_claim_contract": True,
                "status": "no_claims",
                "final_event_count": 0,
            }
        )

        assert store.semantic_is_current(
            index,
            prompt_version=processor.semantic_prompt_version,
            model_identity=processor.model_identity,
            claim_contract_version=processor.semantic_claim_contract_version,
            claim_centric_v27=True,
            strict_claim_contract=True,
        )


def test_legacy_v27_recovery_rejects_events_from_another_prompt(tmp_path):
    index = _index()
    processor = MiniMaxSemanticProcessor(
        Runner(),
        claim_centric_v27=True,
        strict_claim_contract=True,
    )
    with AggregateStateStore(tmp_path / "legacy-v27-prompt.sqlite3") as store:
        store.store_article(
            CleanArticle(
                index=index,
                clean_body="A legacy body with an event from an old prompt.",
                content_hash="prompt-mismatch-body",
            )
        )
        store.store_events(
            index.source_id,
            index.source_article_id,
            [
                SemanticEvent(
                    source_id=index.source_id,
                    source_article_id=index.source_article_id,
                    canonical_url=index.canonical_url,
                    company_mentions=("Legacy Company",),
                    canonical_company="Legacy Company",
                    event_type="funding",
                    event_date="2026-07-29",
                    industry_tags=("hardtech",),
                    event_summary="Legacy Company completed a funding round.",
                    evidence_quotes=(
                        "Legacy Company completed a funding round.",
                    ),
                    processor="minimax",
                    prompt_version="an-older-prompt",
                    content_hash="prompt-mismatch-body",
                )
            ],
        )
        store.store_semantic_audit(
            {
                "source_id": index.source_id,
                "source_article_id": index.source_article_id,
                "prompt_version": processor.semantic_prompt_version,
                "model_identity": processor.model_identity,
                "claim_contract_version": processor.semantic_claim_contract_version,
                "claim_centric_v27": True,
                "strict_claim_contract": True,
                "status": "accepted",
                "final_event_count": 1,
            }
        )

        assert not store.semantic_is_current(
            index,
            prompt_version=processor.semantic_prompt_version,
            model_identity=processor.model_identity,
            claim_contract_version=processor.semantic_claim_contract_version,
            claim_centric_v27=True,
            strict_claim_contract=True,
        )


def test_semantic_result_rolls_back_audit_if_event_replace_fails(
    tmp_path,
    monkeypatch,
):
    index = _index()
    audit = {
        "source_id": index.source_id,
        "source_article_id": index.source_article_id,
        "prompt_version": PROMPT_VERSION,
        "model_identity": "minimax/MiniMax-M3",
        "index_content_hash": index.content_hash,
        "article_content_hash": "atomic-body",
        "status": "accepted",
        "final_event_count": 0,
    }
    with AggregateStateStore(tmp_path / "atomic.sqlite3") as store:
        store.store_article(
            CleanArticle(
                index=index,
                clean_body="Atomic semantic materialization body.",
                content_hash="atomic-body",
            )
        )

        def fail_event_replace(*_args, **_kwargs):
            raise RuntimeError("simulated interruption before event commit")

        monkeypatch.setattr(store, "store_events", fail_event_replace)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            store.store_semantic_result(
                source_id=index.source_id,
                source_article_id=index.source_article_id,
                audit=audit,
                events=[],
            )

        assert store.connection.execute(
            "SELECT COUNT(*) FROM aggregate_semantic_attempts"
        ).fetchone()[0] == 0


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
