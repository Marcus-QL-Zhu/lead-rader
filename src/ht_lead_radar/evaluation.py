"""Count-based acceptance gates for historical Lead Radar backtests."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping


REQUIRED_COMPANY_TYPES = frozenset({"startup_private", "listed", "foreign"})


@dataclass(frozen=True)
class AcceptanceCriteria:
    minimum_cutoffs: int = 3
    minimum_role_matches_per_cutoff: int = 2
    minimum_distinct_titles: int = 30
    minimum_distinct_role_families: int = 8
    minimum_distinct_canonical_role_keys: int = 30
    minimum_distinct_matched_jobs: int = 5
    required_company_types: frozenset[str] = REQUIRED_COMPANY_TYPES


def evaluate_acceptance(
    reports: Iterable[Mapping[str, Any]],
    criteria: AcceptanceCriteria = AcceptanceCriteria(),
) -> dict[str, Any]:
    values = list(reports)
    cutoffs: list[dict[str, Any]] = []
    titles: set[str] = set()
    role_families: set[str] = set()
    role_keys: set[str] = set()
    company_types: set[str] = set()
    matched_job_owners: dict[str, set[str]] = {}
    leakage_safe_manifests = True
    reports_are_consistent = True
    for report in values:
        manifest = report.get("manifest") or {}
        counts = report.get("counts") or {}
        cutoff = str(manifest.get("cutoff") or "")
        matches = [
            match
            for match in report.get("matches") or ()
            if isinstance(match, Mapping)
        ]
        matched = [
            match
            for match in matches
            if match.get("status") in {"exact_match", "family_match"}
        ]
        role_matches = len(matched)
        derived_titles = {
            re.sub(r"\s+", "", str(match.get("predicted_title") or ""))
            for match in matches
            if str(match.get("predicted_title") or "").strip()
        }
        derived_families = {
            str(match.get("predicted_family") or "").strip()
            for match in matches
            if str(match.get("predicted_family") or "").strip()
            and str(match.get("predicted_family") or "") != "other"
        }
        derived_role_keys = {
            str(match.get("canonical_role_key") or "").strip()
            for match in matches
            if str(match.get("canonical_role_key") or "").strip()
        }
        derived_company_types = {
            str(match.get("company_type") or "").strip()
            for match in matched
            if str(match.get("company_type") or "").strip()
        }
        report_consistent = (
            int(counts.get("predictions") or 0) == len(matches)
            and int(counts.get("role_matches") or 0) == role_matches
            and int(counts.get("company_only_matches") or 0)
            == sum(match.get("status") == "company_only_match" for match in matches)
            and int(counts.get("not_observed") or 0)
            == sum(match.get("status") == "not_observed" for match in matches)
            and int(counts.get("distinct_predicted_titles") or 0)
            == len(derived_titles)
            and int(counts.get("distinct_predicted_role_families") or 0)
            == len(derived_families)
            and int(counts.get("distinct_canonical_role_keys") or 0)
            == len(derived_role_keys)
            and set(report.get("distinct_predicted_titles") or ())
            == derived_titles
            and set(report.get("distinct_predicted_role_families") or ())
            == derived_families
            and set(report.get("distinct_canonical_role_keys") or ())
            == derived_role_keys
            and set(report.get("verified_company_types") or ())
            == derived_company_types
        )
        reports_are_consistent = reports_are_consistent and report_consistent
        leakage_safe_manifests = leakage_safe_manifests and (
            manifest.get("workforce_precursors_enabled") is False
            and manifest.get("prediction_inputs_exclude_job_ads") is True
            and int(manifest.get("snapshot_schema_version") or 0) >= 2
        )
        cutoffs.append(
            {
                "cutoff": cutoff,
                "role_matches": role_matches,
                "stable_gate_passed": (
                    role_matches >= criteria.minimum_role_matches_per_cutoff
                ),
            }
        )
        titles.update(derived_titles)
        role_families.update(derived_families)
        role_keys.update(derived_role_keys)
        for match in matches:
            if match.get("status") not in {"exact_match", "family_match"}:
                continue
            identity = str(match.get("actual_job_id") or "").strip() or "|".join(
                str(match.get(key) or "")
                for key in (
                    "company",
                    "actual_title",
                    "actual_published_at",
                    "predicted_family",
                )
            )
            if identity:
                matched_job_owners.setdefault(identity, set()).add(cutoff)
        company_types.update(derived_company_types)
    missing_types = sorted(criteria.required_company_types - company_types)
    unique_cutoffs = {item["cutoff"] for item in cutoffs if item["cutoff"]}

    gates = {
        "enough_historical_cutoffs": (
            len(unique_cutoffs) >= criteria.minimum_cutoffs
        ),
        "leakage_safe_configuration": leakage_safe_manifests,
        "report_integrity": reports_are_consistent,
        "stable_role_generation": (
            len(unique_cutoffs) >= criteria.minimum_cutoffs
            and all(item["stable_gate_passed"] for item in cutoffs)
        ),
        "role_diversity": len(titles) >= criteria.minimum_distinct_titles,
        "role_family_diversity": (
            len(role_families) >= criteria.minimum_distinct_role_families
        ),
        "canonical_role_diversity": (
            len(role_keys) >= criteria.minimum_distinct_canonical_role_keys
        ),
        "independent_job_support": (
            len(matched_job_owners) >= criteria.minimum_distinct_matched_jobs
        ),
        "company_type_coverage": not missing_types,
    }
    return {
        "passed": all(gates.values()),
        "gates": gates,
        "counts": {
            "historical_cutoffs": len(unique_cutoffs),
            "distinct_predicted_titles": len(titles),
            "distinct_predicted_role_families": len(role_families),
            "distinct_canonical_role_keys": len(role_keys),
            "distinct_matched_jobs": len(matched_job_owners),
            "verified_company_types": len(company_types),
        },
        "cutoffs": cutoffs,
        "verified_company_types": sorted(company_types),
        "missing_company_types": missing_types,
        "criteria": {
            "minimum_cutoffs": criteria.minimum_cutoffs,
            "minimum_role_matches_per_cutoff": (
                criteria.minimum_role_matches_per_cutoff
            ),
            "minimum_distinct_titles": criteria.minimum_distinct_titles,
            "minimum_distinct_role_families": (
                criteria.minimum_distinct_role_families
            ),
            "minimum_distinct_canonical_role_keys": (
                criteria.minimum_distinct_canonical_role_keys
            ),
            "minimum_distinct_matched_jobs": (
                criteria.minimum_distinct_matched_jobs
            ),
            "required_company_types": sorted(criteria.required_company_types),
        },
    }


__all__ = [
    "AcceptanceCriteria",
    "REQUIRED_COMPANY_TYPES",
    "evaluate_acceptance",
]
