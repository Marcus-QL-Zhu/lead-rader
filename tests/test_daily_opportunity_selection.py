from copy import deepcopy

from ht_lead_radar.daily_opportunity_selection import select_daily_opportunities
from ht_lead_radar.talent_pool import generate_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore
from test_talent_pool import sample_report


def _reported_store(tmp_path):
    store = TalentPoolStore(tmp_path / "talent.sqlite")
    report = sample_report(leads=1)
    report["manifest"]["as_of"] = "2026-07-26"
    report["manifest"]["run_id"] = "run-0726"
    bundle = generate_draft_bundle(report, target_count=3)
    store.save_bundle(bundle.to_dict())
    pending = store.pending_openclaw_report()
    assert pending is not None
    assert store.mark_openclaw_reported(pending["snapshot_id"])
    return store, report


def test_reported_company_is_suppressed_for_seven_days_without_new_evidence(tmp_path):
    store, report = _reported_store(tmp_path)
    current = deepcopy(report)
    current["manifest"]["as_of"] = "2026-08-01"
    current["manifest"]["run_id"] = "run-0801"

    selected = select_daily_opportunities(
        current,
        history_database=store.database,
        cooldown_days=7,
    )

    assert selected["leads"] == []
    segments = selected["daily_opportunity_segments"]
    assert segments["cooldown_days"] == 7
    assert segments["eligible_company_count"] == 0
    assert segments["cooldown"][0]["reason"] == "shown_without_new_evidence"


def test_new_evidence_bypasses_cooldown(tmp_path):
    store, report = _reported_store(tmp_path)
    current = deepcopy(report)
    current["manifest"]["as_of"] = "2026-07-29"
    current["manifest"]["run_id"] = "run-0729"
    current["leads"][0]["evidence"].append(
        {
            "event_type": "major_order",
            "source_url": "https://example.com/new-order",
            "source_grade": "A",
        }
    )

    selected = select_daily_opportunities(
        current,
        history_database=store.database,
        cooldown_days=7,
    )

    assert len(selected["leads"]) == 1
    item = selected["daily_opportunity_segments"]["new_opportunities"][0]
    assert item["reason"] == "material_new_evidence"
    assert item["new_evidence_urls"] == ["https://example.com/new-order"]


def test_company_returns_after_cooldown_as_ongoing_watchlist(tmp_path):
    store, report = _reported_store(tmp_path)
    current = deepcopy(report)
    current["manifest"]["as_of"] = "2026-08-02"
    current["manifest"]["run_id"] = "run-0802"

    selected = select_daily_opportunities(
        current,
        history_database=store.database,
        cooldown_days=7,
    )

    assert len(selected["leads"]) == 1
    item = selected["daily_opportunity_segments"]["ongoing_watchlist"][0]
    assert item["reason"] == "returning_after_cooldown"


def test_same_day_rerun_is_suppressed_without_new_evidence(tmp_path):
    store, report = _reported_store(tmp_path)
    current = deepcopy(report)
    current["manifest"]["run_id"] = "run-0726-rerun"

    selected = select_daily_opportunities(
        current,
        history_database=store.database,
        cooldown_days=7,
    )

    assert selected["leads"] == []
    assert selected["daily_opportunity_segments"]["cooldown"][0][
        "last_shown_date"
    ] == "2026-07-26"
