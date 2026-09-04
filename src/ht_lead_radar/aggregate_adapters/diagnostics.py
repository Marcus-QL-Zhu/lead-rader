"""Bounded, allowlisted operational diagnostics for persistent audit stores."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..sanitization import sanitize_text, sanitize_tree

# Deliberately excludes prompts, model responses, headers, request/response
# payloads, raw HTML, and arbitrary model-authored objects.
SAFE_SEMANTIC_AUDIT_FIELDS = frozenset(
    {
        "source_id",
        "source_article_id",
        "prompt_version",
        "model_identity",
        "cache_key",
        "claim_contract_version",
        "claim_centric_v27",
        "strict_claim_contract",
        "strict_claim_contract_ready",
        "index_content_hash",
        "article_content_hash",
        "cache_rebound_from_index_content_hash",
        "cache_rebound_at",
        "legacy_hashes_recovered_at",
        "status",
        "error",
        "rule_seed_count",
        "final_event_count",
        "rules_preserved_count",
        "omissions_detected",
        "chunk_count",
        "chunk_statuses",
        "candidate_count",
        "rejected_candidate_count",
        "rejected_candidate_ids",
        "explicitly_rejected_seed_count",
        "explicitly_rejected_seed_ids",
        "unmapped_candidate_count",
        "unmapped_candidate_ids",
        "model_unadjudicated_claim_ids",
        "model_unadjudicated_claim_ids_before_retry",
        "failed_claim_ids",
        "accepted_claim_ids",
        "model_accepted_claim_ids",
        "suppressed_claim_ids",
        "rejected_claim_ids",
        "host_fallback_claim_ids",
        "batch_statuses",
        "validation_issue_count",
        "validation_issues",
        "rejection_issue_count",
        "rejection_issues",
        "rejection_reason_counts",
        "infrastructure_errors",
        "skipped_claim_ids_due_to_infrastructure",
        "entity_count",
        "eligible_entity_count",
        "action_span_count",
        "batch_count",
        "document_type",
        "document_route_reason",
        "document_family",
        "processing_mode",
        "route_gate_confidence",
        "route_llm_gate_required",
        "route_gate_signals",
        "document_unit_ids",
        "raw_model_event_count",
        "cited_model_event_count",
        "uncited_model_event_count",
        "bad_claim_pair_event_count",
        "rejection_conflict_removed_count",
    }
)
_STRUCTURED_AUDIT_FIELD = re.compile(
    r"(?i)(?:^|_)(?:id|ids|hash|sha(?:1|256|512)?|digest|fingerprint|"
    r"version|date|timestamp|count|status)(?:$|_)|^model_identity$"
)


def redact_diagnostic(value: object, *, limit: int = 4000) -> str:
    """Remove common secret shapes from arbitrary exception/trace text."""

    return sanitize_text(value, limit=limit)


def _safe_value(value: Any, *, depth: int = 0, field: str = "") -> Any:
    if depth > 4:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if _STRUCTURED_AUDIT_FIELD.search(field) and not value.startswith(
            ("http://", "https://")
        ):
            return value[:4000]
        return redact_diagnostic(value)
    if isinstance(value, (list, tuple)):
        return [
            _safe_value(item, depth=depth + 1, field=field)
            for item in value[:100]
        ]
    if isinstance(value, Mapping):
        bounded = dict(list(value.items())[:100])
        sanitized = sanitize_tree(bounded, redact_pii=True)
        return {
            str(key): _safe_value(item, depth=depth + 1, field=str(key))
            for key, item in sanitized.items()
        }
    return redact_diagnostic(value)


def sanitize_semantic_audit(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Project a model audit onto the persistent operational allowlist."""

    return {
        key: _safe_value(audit[key], field=key)
        for key in SAFE_SEMANTIC_AUDIT_FIELDS
        if key in audit
    }


__all__ = ["redact_diagnostic", "sanitize_semantic_audit"]
