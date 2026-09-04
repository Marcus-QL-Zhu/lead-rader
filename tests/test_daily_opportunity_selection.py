from copy import deepcopy
import json
import sqlite3

from ht_lead_radar.daily_opportunity_selection import select_daily_opportunities
from ht_lead_radar.talent_pool import generate_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore
from test_talent_pool import sample_report


def _set_delivery_time(store, snapshot_id, delivered_at):
    with sqlite3.connect(store.database) as connection:
        connection.execute(
            "UPDATE talent_pool_delivery_ledger SET delivered_at=? "
            "WHERE snapshot_id=? AND status='delivered'",
            (delivered_at, snapshot_id),
        )


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
    _set_delivery_time(store, pending["snapshot_id"], "2026-07-26T08:00:00+08:00")
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


def test_lower_score_duplicate_contributes_new_evidence_and_roles(tmp_path):
    store, report = _reported_store(tmp_path)
    current = deepcopy(report)
    current["manifest"]["as_of"] = "2026-07-29"
    current["manifest"]["run_id"] = "run-duplicate-evidence"
    highest = current["leads"][0]
    highest["score"] = 88
    highest["target_roles"] = ["Manufacturing Director"]
    duplicate = deepcopy(highest)
    duplicate["score"] = 12
    duplicate["target_roles"] = ["Quality Director"]
    duplicate["evidence"] = [
        {
            "event_type": "major_order",
            "source_url": "https://example.com/lower-score-new-order",
            "source_grade": "A",
        }
    ]
    current["leads"] = [highest, duplicate]

    selected = select_daily_opportunities(
        current,
        history_database=store.database,
        cooldown_days=7,
    )

    assert len(selected["leads"]) == 1
    lead = selected["leads"][0]
    assert lead["score"] == 88
    assert lead["target_roles"] == ["Manufacturing Director", "Quality Director"]
    assert {item["source_url"] for item in lead["evidence"]} == {
        report["leads"][0]["evidence"][0]["source_url"],
        "https://example.com/lower-score-new-order",
    }
    assert selected["daily_opportunity_segments"]["new_opportunities"][0][
        "new_evidence_urls"
    ] == ["https://example.com/lower-score-new-order"]


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


def test_fallback_delivery_is_the_same_cooldown_authority(tmp_path):
    store = TalentPoolStore(tmp_path / "talent.sqlite")
    report = sample_report(leads=1)
    report["manifest"]["as_of"] = "2026-07-26"
    report["manifest"]["run_id"] = "run-fallback"
    bundle = generate_draft_bundle(report, target_count=3)
    store.save_bundle(bundle.to_dict())
    snapshot = store.current_bundle("2026-07-26", bundle.direction)["_snapshot_id"]
    store.record_delivery(snapshot, channel="feishu_fallback", status="delivered")
    _set_delivery_time(store, snapshot, "2026-07-26T08:00:00+08:00")

    current = deepcopy(report)
    current["manifest"]["as_of"] = "2026-07-29"
    current["manifest"]["run_id"] = "run-after-fallback"
    selected = select_daily_opportunities(current, history_database=store.database)

    assert selected["leads"] == []
    assert selected["daily_opportunity_segments"]["cooldown"][0]["company"]


def test_utc_delivery_timestamp_uses_shanghai_calendar_day(tmp_path):
    store = TalentPoolStore(tmp_path / "talent.sqlite")
    report = sample_report(leads=1)
    report["manifest"]["as_of"] = "2026-08-24"
    report["manifest"]["run_id"] = "run-before-midnight-utc"
    bundle = generate_draft_bundle(report, target_count=3)
    store.save_bundle(bundle.to_dict())
    snapshot = store.current_bundle("2026-08-24", bundle.direction)["_snapshot_id"]
    store.record_delivery(snapshot, channel="feishu_fallback", status="delivered")
    with sqlite3.connect(store.database) as connection:
        connection.execute(
            "UPDATE talent_pool_delivery_ledger SET delivered_at=? "
            "WHERE snapshot_id=?",
            ("2026-08-25T16:30:00Z", snapshot),
        )

    current = deepcopy(report)
    current["manifest"]["as_of"] = "2026-09-01"
    current["manifest"]["run_id"] = "run-shanghai-boundary"
    selected = select_daily_opportunities(
        current,
        history_database=store.database,
        cooldown_days=7,
    )

    assert selected["leads"] == []
    assert selected["daily_opportunity_segments"]["cooldown"][0][
        "last_shown_date"
    ] == "2026-08-26"


def test_future_delivery_is_ignored_in_historical_as_of(tmp_path):
    store = TalentPoolStore(tmp_path / "talent.sqlite")
    report = sample_report(leads=1)
    report["manifest"]["as_of"] = "2026-08-24"
    report["manifest"]["run_id"] = "future-delivery-source"
    bundle = generate_draft_bundle(report, target_count=3)
    store.save_bundle(bundle.to_dict())
    snapshot = store.current_bundle("2026-08-24", bundle.direction)["_snapshot_id"]
    store.record_delivery(snapshot, channel="feishu_fallback", status="delivered")
    with sqlite3.connect(store.database) as connection:
        connection.execute(
            "UPDATE talent_pool_delivery_ledger SET delivered_at=? "
            "WHERE snapshot_id=?",
            ("2026-09-05T00:00:00+08:00", snapshot),
        )

    current = deepcopy(report)
    current["manifest"]["as_of"] = "2026-09-01"
    current["manifest"]["run_id"] = "historical-before-delivery"
    selected = select_daily_opportunities(
        current,
        history_database=store.database,
        cooldown_days=30,
    )

    assert len(selected["leads"]) == 1
    assert selected["daily_opportunity_segments"]["cooldown"] == []
    assert selected["daily_opportunity_segments"]["new_opportunities"][0][
        "reason"
    ] == "company_not_shown_before"


def test_undelivered_snapshot_is_not_cooldown_history(tmp_path):
    store = TalentPoolStore(tmp_path / "talent.sqlite")
    report = sample_report(leads=1)
    report["manifest"]["as_of"] = "2026-08-25"
    report["manifest"]["run_id"] = "undelivered-source-run"
    store.save_bundle(generate_draft_bundle(report, target_count=3).to_dict())

    current = deepcopy(report)
    current["manifest"]["as_of"] = "2026-08-27"
    current["manifest"]["run_id"] = "current-source-run"
    selected = select_daily_opportunities(
        current,
        history_database=store.database,
        cooldown_days=7,
    )

    assert len(selected["leads"]) == 1
    assert selected["daily_opportunity_segments"]["new_opportunities"][0][
        "reason"
    ] == "company_not_shown_before"
    assert selected["daily_opportunity_segments"]["ongoing_watchlist"] == []


def _lead(index: int, *, score: float | None = None, url_suffix: str = ""):
    company = f"Company-{index:02d}"
    return {
        "company": company,
        "direction": "hardtech",
        "score": float(100 - index if score is None else score),
        "target_roles": ["Manufacturing Director"],
        "evidence": [
            {
                "event_type": "factory_or_capacity",
                "source_url": f"https://example.com/{index}{url_suffix}",
                "source_grade": "B",
            }
        ],
    }


def test_thirty_candidate_replay_suppresses_without_refill_and_fills_from_eligible(
    tmp_path,
):
    """The scorer oversupplies; cooldown never puts a suppressed company back."""

    store = TalentPoolStore(tmp_path / "talent.sqlite")
    historical = {
        "run_date": "2026-08-25",
        "direction": "hardtech",
        "source_run_id": "historical-30-replay",
        "generation_provider": "test",
        "generation_model": "",
        "generation_error": "",
        "drafts": [],
        "final_report_opportunities": [
            {
                "company": f"Company-{index:02d}",
                "score": 100 - index,
                "role_hypotheses": ["Manufacturing Director"],
                "evidence_urls": [f"https://example.com/{index}"],
            }
            for index in range(1, 11)
        ],
    }
    store.save_bundle(historical)
    snapshot = store.current_bundle("2026-08-25", "hardtech")[
        "_snapshot_id"
    ]
    store.record_delivery(snapshot, channel="feishu_fallback", status="delivered")
    _set_delivery_time(store, snapshot, "2026-08-25T08:00:00+08:00")

    leads = [_lead(index) for index in range(30, 0, -1)]
    # Company 01 has materially new evidence and must bypass cooldown. A lower
    # scoring duplicate must not alter its rank.
    leads[29]["evidence"].append(
        {
            "event_type": "major_order",
            "source_url": "https://example.com/1-new",
            "source_grade": "A",
        }
    )
    leads.append(_lead(1, score=1))
    report = {
        "manifest": {"as_of": "2026-08-31", "direction": "hardtech"},
        "leads": leads,
    }

    selected = select_daily_opportunities(
        report,
        history_database=store.database,
        cooldown_days=7,
        target_count=20,
    )

    companies = [item["company"] for item in selected["leads"]]
    scores = [item["score"] for item in selected["leads"]]
    assert len(companies) == 20
    assert len(set(companies)) == 20
    assert companies[0] == "Company-01"
    assert companies[-1] == "Company-29"
    assert scores == sorted(scores, reverse=True)
    assert all(f"Company-{index:02d}" not in companies for index in range(2, 11))
    segments = selected["daily_opportunity_segments"]
    assert segments["input_company_count"] == 30
    assert segments["eligible_company_count"] == 21
    assert segments["selected_company_count"] == 20
    assert segments["suppressed_company_count"] == 9
    assert segments["new_evidence_company_count"] == 1


def test_zero_draft_snapshot_persists_all_report_opportunities_for_cooldown(tmp_path):
    store = TalentPoolStore(tmp_path / "talent.sqlite")
    opportunities = [
        {
            "company": f"Company-{index:02d}",
            "score": 100 - index,
            "role_hypotheses": ["Commercial Director"],
            "evidence_urls": [f"https://example.com/{index}"],
        }
        for index in range(1, 4)
    ]
    store.save_bundle(
        {
            "run_date": "2026-08-29",
            "direction": "hardtech",
            "source_run_id": "analysis-complete-drafts-failed",
            "generation_provider": "direct-llm",
            "generation_model": "MiniMax-M3",
            "generation_error": "ProviderError: token=must-not-survive",
            "drafts": [],
            "final_report_opportunities": opportunities,
            "completion_status": {
                "analysis_status": "completed",
                "draft_generation_status": "failed",
                "notification_status": "pending",
                "source_health_status": "warning",
            },
        }
    )
    bundle = store.current_bundle("2026-08-29", "hardtech")
    snapshot = bundle["_snapshot_id"]
    store.record_delivery(snapshot, channel="feishu_fallback", status="delivered")
    _set_delivery_time(store, snapshot, "2026-08-29T08:00:00+08:00")

    with sqlite3.connect(store.database) as connection:
        rows = connection.execute(
            """
            SELECT company, evidence_urls_json
            FROM talent_pool_final_report_opportunities
            WHERE snapshot_id=? ORDER BY ordinal
            """,
            (snapshot,),
        ).fetchall()
        stored_error = connection.execute(
            "SELECT generation_error FROM talent_pool_bundle_snapshots "
            "WHERE snapshot_id=?",
            (snapshot,),
        ).fetchone()[0]
    assert [row[0] for row in rows] == ["Company-01", "Company-02", "Company-03"]
    assert json.loads(rows[0][1]) == ["https://example.com/1"]
    assert "must-not-survive" not in stored_error

    current = {
        "manifest": {"as_of": "2026-08-31", "direction": "hardtech"},
        "leads": [_lead(index) for index in range(1, 4)],
    }
    selected = select_daily_opportunities(
        current,
        history_database=store.database,
        cooldown_days=7,
        target_count=20,
    )
    assert selected["leads"] == []
