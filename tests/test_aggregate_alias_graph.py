from ht_lead_radar.aggregate_adapters.models import SemanticEvent
from ht_lead_radar.aggregate_adapters.storage import (
    AggregateStateStore,
    normalize_company_alias,
)


def _event(
    *,
    source_id: str,
    article_id: str,
    canonical: str,
    mentions: tuple[str, ...],
) -> SemanticEvent:
    return SemanticEvent(
        source_id=source_id,
        source_article_id=article_id,
        canonical_url=f"https://example.com/{article_id}",
        company_mentions=mentions,
        canonical_company=canonical,
        event_type="funding",
        event_date="2026-07-29",
        industry_tags=("semiconductor",),
        evidence_quotes=(f"{canonical} completed financing.",),
        confidence="high",
        content_hash=f"hash-{article_id}",
    )


def test_explicit_alias_graph_prefers_grounded_legal_entity(tmp_path):
    brand = "\u534e\u666f\u82af\u5149"
    legal = "\u4e0a\u6d77\u534e\u666f\u82af\u5149\u79d1\u6280\u6709\u9650\u516c\u53f8"
    unrelated = "\u5176\u4ed6\u673a\u5668\u4eba"
    with AggregateStateStore(tmp_path / "state.sqlite3") as store:
        store.store_events(
            "source-a",
            "article-a",
            [
                _event(
                    source_id="source-a",
                    article_id="article-a",
                    canonical=legal,
                    mentions=(legal, brand),
                )
            ],
        )
        store.store_events(
            "source-b",
            "article-b",
            [
                _event(
                    source_id="source-b",
                    article_id="article-b",
                    canonical=brand,
                    mentions=(brand,),
                )
            ],
        )
        store.store_events(
            "source-c",
            "article-c",
            [
                _event(
                    source_id="source-c",
                    article_id="article-c",
                    canonical=unrelated,
                    mentions=(unrelated,),
                )
            ],
        )

        aliases = store.canonical_alias_map()

    assert aliases[normalize_company_alias(brand)] == legal
    assert aliases[normalize_company_alias(legal)] == legal
    assert aliases[normalize_company_alias(unrelated)] == unrelated


def test_replacing_article_events_removes_stale_alias_edges(tmp_path):
    brand = "\u534e\u666f\u82af\u5149"
    legal = "\u4e0a\u6d77\u534e\u666f\u82af\u5149\u79d1\u6280\u6709\u9650\u516c\u53f8"
    with AggregateStateStore(tmp_path / "state.sqlite3") as store:
        store.store_events(
            "source",
            "article",
            [
                _event(
                    source_id="source",
                    article_id="article",
                    canonical=legal,
                    mentions=(legal, brand),
                )
            ],
        )
        store.store_events("source", "article", [])

        assert store.canonical_alias_map() == {}
