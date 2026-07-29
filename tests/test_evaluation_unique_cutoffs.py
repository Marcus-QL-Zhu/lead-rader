from ht_lead_radar.evaluation import evaluate_acceptance


def test_duplicate_cutoff_reports_do_not_satisfy_three_cutoff_gate():
    report = {
        "manifest": {
            "cutoff": "2026-05-01",
            "workforce_precursors_enabled": False,
            "prediction_inputs_exclude_job_ads": True,
            "snapshot_schema_version": 2,
        },
        "counts": {"role_matches": 5},
        "distinct_predicted_titles": [f"role-{index}" for index in range(30)],
        "distinct_predicted_role_families": [
            f"family-{index}" for index in range(8)
        ],
        "verified_company_types": [
            "startup_private",
            "listed",
            "foreign",
        ],
    }

    result = evaluate_acceptance([report, report, report])

    assert result["passed"] is False
    assert result["counts"]["historical_cutoffs"] == 1
    assert result["gates"]["enough_historical_cutoffs"] is False
