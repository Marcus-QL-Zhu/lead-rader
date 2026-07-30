from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.models import SemanticEvent
from ht_lead_radar.fact_store import FactStore
from ht_lead_radar.models import Evidence


def _evidence(url: str, funding_round: str) -> Evidence:
    return Evidence(
        company="\u661f\u6cb3\u82af\u7247",
        event_type="funding",
        phase="strategy_capital",
        event_date="2026-07-29",
        title=f"\u661f\u6cb3\u82af\u7247\u5b8c\u6210{funding_round}\u878d\u8d44",
        snippet=f"\u661f\u6cb3\u82af\u7247\u5b8c\u6210{funding_round}\u878d\u8d44\u3002",
        source_url=url,
        source_name="aggregate source",
        event_slots={
            "funding_round": funding_round,
            "funding_amount": "1\u4ebf\u5143",
            "event_status": "completed",
        },
    )


def test_semantic_event_projects_investors_and_funding_slots():
    event = SemanticEvent(
        source_id="funding-source",
        source_article_id="1",
        canonical_url="https://example.com/1",
        company_mentions=("\u661f\u6cb3\u82af\u7247",),
        canonical_company="\u661f\u6cb3\u82af\u7247",
        event_type="funding",
        event_date="2026-07-29",
        industry_tags=("semiconductor",),
        funding_round="A\u8f6e",
        funding_amount="1\u4ebf\u5143",
        investors=("\u8fdc\u5c71\u8d44\u672c",),
        evidence_quotes=(
            "\u661f\u6cb3\u82af\u7247\u5b8c\u62101\u4ebf\u5143A\u8f6e\u878d\u8d44\u3002",
        ),
        content_hash="content",
    )

    evidence = DedicatedAggregateCoordinator._events_to_evidence(
        [event],
        "funding source",
        "B",
        "hardtech",
    )[0]

    assert evidence.organizations == ("\u8fdc\u5c71\u8d44\u672c",)
    assert evidence.event_slots == {
        "funding_round": "A\u8f6e",
        "funding_amount": "1\u4ebf\u5143",
        "cumulative_funding_amount": "",
        "event_status": "completed",
    }


def test_fact_store_clusters_same_round_but_separates_distinct_rounds(tmp_path):
    store = FactStore(tmp_path / "facts.sqlite3")

    first = store.ingest_legacy_evidence(
        _evidence("https://source-a.example/one", "A\u8f6e")
    )
    corroboration = store.ingest_legacy_evidence(
        _evidence("https://source-b.example/two", "A\u8f6e")
    )
    distinct_round = store.ingest_legacy_evidence(
        _evidence("https://source-c.example/three", "B\u8f6e")
    )

    assert first.event.id == corroboration.event.id
    assert distinct_round.event.id != first.event.id
    refreshed = store.get_event(first.event.id)
    assert refreshed is not None
    assert refreshed.independent_source_count == 2
