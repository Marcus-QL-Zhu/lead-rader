from ht_lead_radar.evaluation import AcceptanceCriteria, evaluate_acceptance


def test_aggregate_acceptance_rejects_workforce_precursor_manifest():
    report = {
        "manifest": {
            "cutoff": "2026-01-01",
            "workforce_precursors_enabled": True,
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
        "distinct_predicted_titles": ["role"],
        "distinct_predicted_role_families": ["family"],
        "distinct_canonical_role_keys": ["family:role"],
        "verified_company_types": ["startup_private"],
        "matches": [
            {
                "status": "family_match",
                "actual_job_id": "job-unique",
                "predicted_title": "role",
                "predicted_family": "family",
                "canonical_role_key": "family:role",
                "company_type": "startup_private",
            }
        ],
    }
    criteria = AcceptanceCriteria(
        minimum_cutoffs=1,
        minimum_role_matches_per_cutoff=1,
        minimum_distinct_titles=1,
        minimum_distinct_role_families=1,
        minimum_distinct_canonical_role_keys=1,
        required_company_types=frozenset({"startup_private"}),
    )

    result = evaluate_acceptance([report], criteria)

    assert result["gates"]["leakage_safe_configuration"] is False
    assert result["passed"] is False


def test_aggregate_acceptance_rejects_self_reported_count_inflation():
    report = {
        "manifest": {
            "cutoff": "2026-01-01",
            "workforce_precursors_enabled": False,
            "prediction_inputs_exclude_job_ads": True,
            "snapshot_schema_version": 2,
        },
        "counts": {
            "predictions": 1,
            "role_matches": 999,
            "company_only_matches": 0,
            "not_observed": 0,
            "distinct_predicted_titles": 1,
            "distinct_predicted_role_families": 1,
            "distinct_canonical_role_keys": 1,
        },
        "distinct_predicted_titles": ["role"],
        "distinct_predicted_role_families": ["family"],
        "distinct_canonical_role_keys": ["family:role"],
        "verified_company_types": ["startup_private"],
        "matches": [
            {
                "status": "family_match",
                "actual_job_id": "job-unique",
                "predicted_title": "role",
                "predicted_family": "family",
                "canonical_role_key": "family:role",
                "company_type": "startup_private",
            }
        ],
    }
    result = evaluate_acceptance(
        [report],
        AcceptanceCriteria(
            minimum_cutoffs=1,
            minimum_role_matches_per_cutoff=1,
            minimum_distinct_titles=1,
            minimum_distinct_role_families=1,
            minimum_distinct_canonical_role_keys=1,
            required_company_types=frozenset({"startup_private"}),
        ),
    )

    assert result["gates"]["report_integrity"] is False
    assert result["cutoffs"][0]["role_matches"] == 1
    assert result["passed"] is False
