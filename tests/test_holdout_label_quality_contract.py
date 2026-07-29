from ht_lead_radar.holdout_evaluation import evaluate_holdout


def _report():
    return {
        "manifest": {
            "cutoff": "2026-05-01",
            "label_quality_protocol": "v23-employer-scope-date-complete",
            "label_quality_verified": False,
        },
        "counts": {
            "candidate_count": 1,
            "companies_with_hypotheses": 1,
            "role_matches": 0,
            "distinct_predicted_titles": 1,
            "distinct_predicted_role_families": 1,
            "distinct_canonical_role_keys": 1,
        },
        "matches": [],
        "distinct_predicted_titles": ["制造总监"],
        "distinct_predicted_role_families": ["manufacturing"],
        "distinct_canonical_role_keys": ["manufacturing:制造"],
        "verified_company_types": [],
    }


def test_holdout_rejects_unverified_label_quality_contract():
    result = evaluate_holdout(
        [_report()],
        {
            "holdout_version": "v23",
            "cutoffs": ["2026-05-01"],
            "label_quality_protocol": "v23-employer-scope-date-complete",
            "acceptance": {
                "label_quality_required": True,
                "minimum_matches_per_cutoff": 0,
            },
        },
    )

    assert result["passed"] is False
    assert result["gates"]["pre_registered_runtime_contract"] is False
