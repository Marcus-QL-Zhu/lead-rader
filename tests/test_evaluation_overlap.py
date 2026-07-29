from ht_lead_radar.evaluation import AcceptanceCriteria, evaluate_acceptance


def _report(cutoff, unique_url):
    return {
        "manifest": {
            "cutoff": cutoff,
            "workforce_precursors_enabled": False,
            "prediction_inputs_exclude_job_ads": True,
            "snapshot_schema_version": 2,
        },
        "counts": {
            "predictions": 1,
            "role_matches": 1,
            "company_only_matches": 0,
            "not_observed": 0,
            "distinct_predicted_titles": 1,
            "distinct_predicted_role_families": 1,
            "distinct_canonical_role_keys": 1,
        },
        "distinct_predicted_titles": [f"title-{cutoff}"],
        "distinct_predicted_role_families": ["family"],
        "distinct_canonical_role_keys": [f"family:{cutoff}"],
        "verified_company_types": ["startup_private"],
        "matches": [
            {
                "status": "family_match",
                "actual_job_id": unique_url,
                "predicted_title": f"title-{cutoff}",
                "predicted_family": "family",
                "canonical_role_key": f"family:{cutoff}",
                "company_type": "startup_private",
            }
        ],
    }


def test_overlapping_cutoffs_still_need_enough_distinct_matched_jobs():
    reports = [
        _report("2026-01-01", "https://jobs.example/shared"),
        _report("2026-02-01", "https://jobs.example/shared"),
        _report("2026-03-01", "https://jobs.example/unique"),
    ]
    criteria = AcceptanceCriteria(
        minimum_role_matches_per_cutoff=1,
        minimum_distinct_titles=3,
        minimum_distinct_role_families=1,
        minimum_distinct_canonical_role_keys=3,
        required_company_types=frozenset({"startup_private"}),
    )

    result = evaluate_acceptance(reports, criteria)

    assert result["gates"]["independent_job_support"] is False
    assert result["passed"] is False
