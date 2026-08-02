"""Validation and agreement checks for Semantic v25 Gold annotations."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from .aggregate_adapters.semantic import ALLOWED_EVENT_STATUS, ALLOWED_EVENT_TYPES


ALLOWED_DISPOSITIONS = frozenset({"accepted", "rejected", "ambiguous"})
ALLOWED_IMPORTANCE = frozenset({"strong", "weak"})


def _exact_span(span: Any, body: str) -> bool:
    if not isinstance(span, Mapping):
        return False
    try:
        start = int(span.get("char_start"))
        end = int(span.get("char_end"))
    except (TypeError, ValueError):
        return False
    text = str(span.get("text") or "")
    return 0 <= start < end <= len(body) and body[start:end] == text


def validate_gold_case(case: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    body = str(case.get("clean_body") or "")
    annotation = case.get("annotation")
    if not isinstance(annotation, Mapping):
        return ["missing_annotation"]
    if annotation.get("annotation_status") not in {"complete", "gold_ambiguous"}:
        errors.append("annotation_status_not_final")
    required_claims = {
        str(claim_id)
        for candidate in case.get("candidates") or []
        if isinstance(candidate, Mapping)
        for claim_id in candidate.get("required_claim_ids") or []
        if str(claim_id)
    }
    dispositions = annotation.get("candidate_dispositions")
    if not isinstance(dispositions, list):
        errors.append("candidate_dispositions_not_list")
        dispositions = []
    disposition_ids = [
        str(row.get("claim_id") or "")
        for row in dispositions
        if isinstance(row, Mapping)
    ]
    if set(disposition_ids) != required_claims or len(disposition_ids) != len(
        required_claims
    ):
        errors.append("candidate_disposition_coverage_mismatch")
    accepted_claims: set[str] = set()
    for row in dispositions:
        if not isinstance(row, Mapping):
            errors.append("invalid_candidate_disposition")
            continue
        disposition = str(row.get("disposition") or "")
        if disposition not in ALLOWED_DISPOSITIONS:
            errors.append("invalid_candidate_disposition")
        if not str(row.get("reason_code") or "").strip():
            errors.append("missing_disposition_reason")
        if disposition == "accepted":
            accepted_claims.add(str(row.get("claim_id") or ""))

    gold_events = annotation.get("gold_events")
    if not isinstance(gold_events, list):
        errors.append("gold_events_not_list")
        gold_events = []
    covered_claims: list[str] = []
    atomic_groups: Counter[tuple[Any, ...]] = Counter()
    atomic_values: dict[tuple[Any, ...], list[str]] = {}
    for event in gold_events:
        if not isinstance(event, Mapping):
            errors.append("invalid_gold_event")
            continue
        if not str(event.get("canonical_company") or "").strip():
            errors.append("gold_event_missing_company")
        if event.get("event_type") not in ALLOWED_EVENT_TYPES - {"other"}:
            errors.append("gold_event_invalid_type")
        if event.get("event_status") not in ALLOWED_EVENT_STATUS - {"cumulative"}:
            errors.append("gold_event_invalid_status")
        if event.get("importance") not in ALLOWED_IMPORTANCE:
            errors.append("gold_event_invalid_importance")
        if not _exact_span(event.get("evidence_span"), body):
            errors.append("gold_event_non_exact_span")
        if event.get("status_context_span") is not None and not _exact_span(
            event.get("status_context_span"), body
        ):
            errors.append("gold_event_non_exact_status_context_span")
        evidence_span = event.get("evidence_span") or {}
        atomic_key = (
            str(event.get("canonical_company") or ""),
            str(event.get("event_type") or ""),
            str(event.get("event_status") or ""),
            int(evidence_span.get("char_start") or 0),
            int(evidence_span.get("char_end") or 0),
        )
        atomic_groups[atomic_key] += 1
        discriminator = str(event.get("atomic_discriminator") or "").strip()
        if discriminator and (
            len(discriminator) > 80
            or "=" not in discriminator
            or discriminator.split("=", 1)[0]
            not in {
                "funding_round",
                "funding_amount",
                "cumulative_funding_amount",
            }
        ):
            errors.append("gold_event_invalid_atomic_discriminator")
        atomic_values.setdefault(atomic_key, []).append(discriminator)
        claim_ids = [str(value) for value in event.get("claim_ids") or []]
        if not claim_ids and event.get("candidate_gap") is not True:
            errors.append("gold_event_without_claim_or_gap")
        if any(claim_id not in required_claims for claim_id in claim_ids):
            errors.append("gold_event_unknown_claim")
        covered_claims.extend(claim_ids)
    if set(covered_claims) != accepted_claims:
        errors.append("accepted_claim_event_coverage_mismatch")
    if any(count > 1 for count in Counter(covered_claims).values()):
        errors.append("accepted_claim_covered_more_than_once")
    for key, count in atomic_groups.items():
        if count <= 1:
            continue
        values = atomic_values[key]
        if any(not value for value in values) or len(set(values)) != count:
            errors.append("duplicate_gold_events_need_atomic_discriminator")
    return sorted(set(errors))


def validate_gold_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    failures: dict[str, list[str]] = {}
    cases = packet.get("cases")
    if not isinstance(cases, list):
        return {"valid": False, "case_count": 0, "failures": {"$": ["cases_not_list"]}}
    for case in cases:
        if not isinstance(case, Mapping):
            failures[f"row-{len(failures)}"] = ["invalid_case"]
            continue
        errors = validate_gold_case(case)
        if errors:
            failures[str(case.get("key") or f"row-{len(failures)}")] = errors
    return {
        "valid": not failures,
        "case_count": len(cases),
        "failure_count": len(failures),
        "failures": failures,
    }


def _event_signatures(case: Mapping[str, Any]) -> set[tuple[Any, ...]]:
    annotation = case.get("annotation") or {}
    return {
        (
            str(event.get("canonical_company") or ""),
            str(event.get("event_type") or ""),
            str(event.get("event_status") or ""),
            tuple(sorted(str(value) for value in event.get("claim_ids") or [])),
            int((event.get("evidence_span") or {}).get("char_start") or 0),
            int((event.get("evidence_span") or {}).get("char_end") or 0),
        )
        for event in annotation.get("gold_events") or []
        if isinstance(event, Mapping)
    }


def _disposition_signatures(case: Mapping[str, Any]) -> set[tuple[str, str]]:
    annotation = case.get("annotation") or {}
    return {
        (
            str(item.get("claim_id") or ""),
            str(item.get("disposition") or ""),
        )
        for item in annotation.get("candidate_dispositions") or []
        if isinstance(item, Mapping)
    }


def compare_primary_annotations(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
) -> dict[str, Any]:
    first_by_key = {
        str(case.get("key") or ""): case
        for case in first.get("cases") or []
        if isinstance(case, Mapping)
    }
    second_by_key = {
        str(case.get("key") or ""): case
        for case in second.get("cases") or []
        if isinstance(case, Mapping)
    }
    if set(first_by_key) != set(second_by_key):
        raise ValueError("primary annotation keys differ")
    disagreements = [
        key
        for key in sorted(first_by_key)
        if (
            _event_signatures(first_by_key[key])
            != _event_signatures(second_by_key[key])
            or _disposition_signatures(first_by_key[key])
            != _disposition_signatures(second_by_key[key])
        )
    ]
    return {
        "case_count": len(first_by_key),
        "agreement_count": len(first_by_key) - len(disagreements),
        "disagreement_count": len(disagreements),
        "disagreement_keys": disagreements,
    }


__all__ = [
    "compare_primary_annotations",
    "validate_gold_case",
    "validate_gold_packet",
]
