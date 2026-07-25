from ht_lead_radar.collectors import extract_company, infer_event
from ht_lead_radar.source_pack_collector import _document_relevant
from ht_lead_radar.source_packs import SourceDefinition


def _global_source() -> SourceDefinition:
    return SourceDefinition(
        id="global-venture",
        name="Global Venture",
        owner="Publisher",
        source_type="global_financing_media",
        grade="B",
        url="https://example.com/feed",
        adapter="rss",
        signal_types=("funding", "investor"),
        industry_tags=("generic",),
        enabled=True,
        verified_on="2026-07-25",
        status="verified_static_list",
        verification_note="test",
    )


def test_english_funding_titles_are_classified_and_company_is_extracted():
    title = "Natural raises $30M to reinvent payments for AI agents"

    assert infer_event(title) == ("funding", "strategy_capital")
    assert extract_company(title) == "Natural"


def test_global_funding_feed_is_relevant_for_general_funding_scan():
    assert _document_relevant(
        _global_source(),
        "Bluecore Energy raises $10M to build portable nuclear reactors",
        ("融资",),
        frozenset({"global-venture"}),
    )
