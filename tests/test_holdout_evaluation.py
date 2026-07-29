from ht_lead_radar.holdout_evaluation import evaluate_holdout


def _report(cutoff, company_type, job_id):
    return {
        "manifest": {
            "cutoff": cutoff,
            "workforce_precursors_enabled": False,
            "prediction_inputs_exclude_job_ads": True,
            "snapshot_schema_version": 2,
        },
        "counts": {
            "candidate_count": 1,
            "analyzed_company_count": 1,
            "failed_company_count": 0,
            "companies_with_hypotheses": 1,
            "candidate_prediction_coverage": 1.0,
            "predictions": 1,
            "role_matches": 1,
            "company_only_matches": 0,
            "not_observed": 0,
            "distinct_predicted_titles": 1,
            "distinct_predicted_role_families": 1,
            "distinct_canonical_role_keys": 1,
        },
        "matches": [{
            "status": "family_match",
            "company": f"company-{job_id}",
            "predicted_title": f"role-{job_id}",
            "predicted_family": "product",
            "canonical_role_key": f"product:{job_id}",
            "company_type": company_type,
            "actual_job_id": job_id,
        }],
        "distinct_predicted_titles": [f"role-{job_id}"],
        "distinct_predicted_role_families": ["product"],
        "distinct_canonical_role_keys": [f"product:{job_id}"],
        "verified_company_types": [company_type],
    }


def test_holdout_uses_pre_registered_count_and_type_gates():
    manifest = {
        "holdout_version": "v1",
        "cutoffs": ["2026-01-01", "2026-04-01"],
        "acceptance": {
            "minimum_matches_per_cutoff": 1,
            "minimum_distinct_matched_jobs": 2,
            "minimum_distinct_matched_companies": 2,
            "minimum_candidate_prediction_coverage": 1.0,
            "required_matched_company_types": ["listed", "foreign"],
        },
    }
    result = evaluate_holdout([
        _report("2026-01-01", "listed", "a"),
        _report("2026-04-01", "foreign", "b"),
    ], manifest)
    assert result["passed"] is True
    assert result["gates"]["registered_cutoffs_only"] is True
    assert result["counts"]["distinct_matched_companies"] == 2
    assert result["counts"]["candidate_prediction_coverage"] == 1.0


def test_holdout_rejects_unregistered_cutoff():
    manifest = {
        "cutoffs": ["2026-01-01"],
        "acceptance": {"minimum_matches_per_cutoff": 1},
    }
    result = evaluate_holdout(
        [_report("2026-02-01", "listed", "a")], manifest
    )
    assert result["passed"] is False
    assert result["gates"]["registered_cutoffs_only"] is False


def test_holdout_rejects_low_candidate_prediction_coverage():
    report = _report("2026-01-01", "listed", "a")
    report["counts"]["candidate_count"] = 2
    report["counts"]["companies_with_hypotheses"] = 1
    report["counts"]["candidate_prediction_coverage"] = 0.5
    manifest = {
        "cutoffs": ["2026-01-01"],
        "acceptance": {
            "minimum_matches_per_cutoff": 1,
            "minimum_candidate_prediction_coverage": 0.75,
        },
    }
    result = evaluate_holdout([report], manifest)
    assert result["passed"] is False
    assert result["gates"]["candidate_prediction_coverage"] is False


def test_holdout_enforces_pre_registered_prediction_diversity():
    manifest = {
        "cutoffs": ["2026-01-01"],
        "acceptance": {
            "minimum_matches_per_cutoff": 1,
            "minimum_distinct_predicted_titles": 2,
            "minimum_distinct_predicted_role_families": 2,
            "minimum_distinct_canonical_role_keys": 2,
        },
    }

    result = evaluate_holdout(
        [_report("2026-01-01", "foreign", "a")],
        manifest,
    )

    assert result["passed"] is False
    assert result["gates"]["role_diversity"] is False
    assert result["gates"]["role_family_diversity"] is False
    assert result["gates"]["canonical_role_diversity"] is False


def test_holdout_rejects_duplicate_reports_for_one_cutoff():
    manifest = {
        "cutoffs": ["2026-01-01"],
        "acceptance": {"minimum_matches_per_cutoff": 1},
    }
    report = _report("2026-01-01", "listed", "a")
    result = evaluate_holdout([report, report], manifest)
    assert result["passed"] is False
    assert result["gates"]["one_report_per_registered_cutoff"] is False


def test_holdout_enforces_runtime_contract_when_registered():
    manifest = {
        "cutoffs": ["2026-01-01"],
        "prompt_version": "frozen-v1",
        "temperature": 0.0,
        "companies": ["company-a"],
        "acceptance": {"minimum_matches_per_cutoff": 1},
    }
    report = _report("2026-01-01", "listed", "a")
    report["manifest"]["prompt_version"] = "changed"
    report["manifest"]["candidate_companies"] = ["company-a"]
    report["manifest"]["runner"] = {"temperature": 0.0}
    result = evaluate_holdout([report], manifest)
    assert result["passed"] is False
    assert result["gates"]["pre_registered_runtime_contract"] is False
