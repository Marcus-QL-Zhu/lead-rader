"""Pre-registered gates for frozen out-of-sample historical holdouts."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .evaluation import AcceptanceCriteria, evaluate_acceptance


def _runtime_contract_matches(
    report: Mapping[str, Any], manifest: Mapping[str, Any]
) -> bool:
    actual = report.get("manifest") or {}
    direct_fields = (
        "horizon_months",
        "prompt_version",
        "prediction_max_roles_per_company",
        "workforce_precursors_enabled",
        "prediction_inputs_exclude_job_ads",
        "josint_inputs_enabled",
        "label_quality_protocol",
    )
    for field in direct_fields:
        if field in manifest and actual.get(field) != manifest.get(field):
            return False
    if "companies" in manifest:
        if sorted(str(value) for value in actual.get("candidate_companies") or ()) != sorted(
            str(value) for value in manifest.get("companies") or ()
        ):
            return False
    if "temperature" in manifest:
        runner = actual.get("runner") or {}
        if float(runner.get("temperature", 999.0)) != float(manifest["temperature"]):
            return False
    acceptance = manifest.get("acceptance") or {}
    if acceptance.get("snapshot_audit_required") is True:
        if actual.get("snapshot_audit_verified") is not True:
            return False
    if acceptance.get("uniform_label_search_required") is True:
        if actual.get("uniform_label_search_verified") is not True:
            return False
    if acceptance.get("label_quality_required") is True:
        if actual.get("label_quality_verified") is not True:
            return False
    return True


def evaluate_holdout(
    reports: Iterable[Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate reports against thresholds frozen before model execution."""

    values = list(reports)
    acceptance = manifest.get("acceptance") or {}
    required_types = frozenset(
        str(value) for value in acceptance.get("required_matched_company_types") or ()
    )
    expected_cutoffs = {
        str(value) for value in manifest.get("cutoffs") or () if str(value)
    }
    criteria = AcceptanceCriteria(
        minimum_cutoffs=len(expected_cutoffs),
        minimum_role_matches_per_cutoff=int(
            acceptance.get("minimum_matches_per_cutoff") or 0
        ),
        minimum_distinct_titles=int(
            acceptance.get("minimum_distinct_predicted_titles") or 0
        ),
        minimum_distinct_role_families=int(
            acceptance.get("minimum_distinct_predicted_role_families") or 0
        ),
        minimum_distinct_canonical_role_keys=int(
            acceptance.get("minimum_distinct_canonical_role_keys") or 0
        ),
        minimum_distinct_matched_jobs=int(
            acceptance.get("minimum_distinct_matched_jobs") or 0
        ),
        required_company_types=required_types,
    )
    result = evaluate_acceptance(values, criteria)
    matched_companies = {
        str(match.get("company") or "")
        for report in values
        for match in report.get("matches") or ()
        if match.get("status") in {"exact_match", "family_match"}
        and str(match.get("company") or "")
    }
    minimum_companies = int(
        acceptance.get("minimum_distinct_matched_companies") or 0
    )
    result["counts"]["distinct_matched_companies"] = len(matched_companies)
    result["gates"]["distinct_company_support"] = (
        len(matched_companies) >= minimum_companies
    )
    result["criteria"]["minimum_distinct_matched_companies"] = (
        minimum_companies
    )
    candidate_count = sum(
        int((report.get("counts") or {}).get("candidate_count") or 0)
        for report in values
    )
    companies_with_hypotheses = sum(
        int((report.get("counts") or {}).get("companies_with_hypotheses") or 0)
        for report in values
    )
    prediction_coverage = (
        companies_with_hypotheses / candidate_count if candidate_count else 0.0
    )
    minimum_prediction_coverage = float(
        acceptance.get("minimum_candidate_prediction_coverage") or 0.0
    )
    result["counts"]["candidate_count"] = candidate_count
    result["counts"]["companies_with_hypotheses"] = companies_with_hypotheses
    result["counts"]["candidate_prediction_coverage"] = prediction_coverage
    result["gates"]["candidate_prediction_coverage"] = (
        prediction_coverage >= minimum_prediction_coverage
        and (candidate_count > 0 or minimum_prediction_coverage == 0.0)
    )
    result["criteria"]["minimum_candidate_prediction_coverage"] = (
        minimum_prediction_coverage
    )
    observed_cutoff_values = [
        str((report.get("manifest") or {}).get("cutoff") or "")
        for report in values
    ]
    observed_cutoffs = set(observed_cutoff_values)
    result["gates"]["registered_cutoffs_only"] = observed_cutoffs == expected_cutoffs
    result["gates"]["one_report_per_registered_cutoff"] = (
        len(values) == len(expected_cutoffs)
        and all(observed_cutoff_values.count(cutoff) == 1 for cutoff in expected_cutoffs)
    )
    result["gates"]["pre_registered_runtime_contract"] = all(
        _runtime_contract_matches(report, manifest) for report in values
    )
    result["passed"] = all(result["gates"].values())
    result["holdout_version"] = str(manifest.get("holdout_version") or "")
    result["registered_cutoffs"] = sorted(expected_cutoffs)
    return result


__all__ = ["evaluate_holdout"]
