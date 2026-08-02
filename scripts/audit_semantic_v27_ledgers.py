#!/usr/bin/env python3
"""Audit V27 entity/action coverage against an opened development Gold set."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import fields
import json
from pathlib import Path
import sys
from typing import Any
import unicodedata

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ht_lead_radar.aggregate_adapters.action_span_ledger import (  # noqa: E402
    build_action_span_ledger,
)
from ht_lead_radar.aggregate_adapters.entity_ledger import (  # noqa: E402
    build_article_entity_ledger,
)
from ht_lead_radar.aggregate_adapters.models import (  # noqa: E402
    CleanArticle,
    SourceArticleIndex,
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return payload


def _article(raw: dict[str, Any]) -> CleanArticle:
    index_fields = {item.name for item in fields(SourceArticleIndex)}
    article_fields = {item.name for item in fields(CleanArticle)} - {"index"}
    return CleanArticle(
        index=SourceArticleIndex(
            **{key: value for key, value in raw["index"].items() if key in index_fields}
        ),
        **{key: value for key, value in raw.items() if key in article_fields},
    )


def _evidence_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if character.isalnum() or character == "+"
    )


def _span_supports_evidence(span: str, evidence: str) -> bool:
    if evidence in span or span in evidence:
        return True
    span_key = _evidence_key(span)
    evidence_key = _evidence_key(evidence)
    return bool(
        min(len(span_key), len(evidence_key)) >= 6
        and (span_key in evidence_key or evidence_key in span_key)
    )


def _claim_supports_evidence(
    span: str, action_text: str, evidence: str
) -> bool:
    if _span_supports_evidence(span, evidence):
        return True
    action_key = _evidence_key(action_text)
    evidence_key = _evidence_key(evidence)
    return bool(
        min(len(action_key), len(evidence_key)) >= 5
        and action_key in evidence_key
    )


def audit(bundle: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    bundle_rows = {row["key"]: row for row in bundle.get("articles", [])}
    failures: list[dict[str, Any]] = []
    entity_total = entity_hits = 0
    action_total = action_hits = exact_span_hits = 0
    entity_rows = eligible_rows = action_spans = atomic_claims = 0
    by_event_type: Counter[str] = Counter()
    by_event_type_hit: Counter[str] = Counter()
    failure_reasons: Counter[str] = Counter()

    for case in gold.get("cases", []):
        key = str(case["key"])
        article = _article(bundle_rows[key]["article"])
        entities = build_article_entity_ledger(article, case.get("candidates", []), [])
        actions = build_action_span_ledger(
            article, entities, case.get("candidates", [])
        )
        span_by_id = actions.spans_by_id()
        entity_rows += len(entities.entities)
        eligible_rows += len(entities.eligible())
        action_spans += len(actions.spans)
        atomic_claims += len(actions.claims)

        case_events: list[dict[str, Any]] = []
        for event in case.get("annotation", {}).get("gold_events", []):
            company = str(event["canonical_company"])
            event_type = str(event["event_type"])
            evidence = str(event["evidence_span"]["text"])
            by_event_type[event_type] += 1
            entity_total += 1
            action_total += 1
            entity = entities.entity_for_name(company)
            entity_ok = bool(entity and entity.operating_subject_eligible)
            entity_hits += int(entity_ok)
            type_candidates = [
                claim
                for claim in actions.claims
                if (
                    claim.event_type_hint == event_type
                    or (
                        claim.event_type_hint == "open_action"
                        and event_type in claim.allowed_event_types
                    )
                )
            ]
            subject_candidates = [
                claim
                for claim in type_candidates
                if entity_ok
                and entity.entity_id in claim.allowed_subject_entity_ids
            ]
            discriminator_candidates = [
                claim
                for claim in subject_candidates
                if (
                    not str(event.get("atomic_discriminator") or "").startswith(
                        "funding_round="
                    )
                    or claim.funding_round_hint
                    == str(event.get("atomic_discriminator") or "").split(
                        "=", 1
                    )[1]
                )
            ]
            candidates = [
                claim
                for claim in discriminator_candidates
                if _claim_supports_evidence(
                    span_by_id[claim.span_id].text,
                    claim.action_text,
                    evidence,
                )
            ]
            candidates.sort(
                key=lambda claim: (
                    span_by_id[claim.span_id].text != evidence,
                    claim.claim_id,
                )
            )
            case_events.append(
                {
                    "key": key,
                    "company": company,
                    "event_type": event_type,
                    "entity_ok": entity_ok,
                    "evidence": evidence,
                    "candidate_claim_ids": [
                        claim.claim_id for claim in candidates
                    ],
                    "type_candidate_count": len(type_candidates),
                    "subject_candidate_count": len(subject_candidates),
                    "discriminator_candidate_count": len(
                        discriminator_candidates
                    ),
                    "discriminator_candidate_previews": [
                        {
                            "claim_id": claim.claim_id,
                            "action_text": claim.action_text,
                            "span_text": span_by_id[claim.span_id].text,
                        }
                        for claim in discriminator_candidates[:3]
                    ],
                    "exact_claim_ids": {
                        claim.claim_id
                        for claim in candidates
                        if span_by_id[claim.span_id].text == evidence
                    },
                }
            )

        # One Claim can produce only one SemanticEvent under the V27 contract.
        # Use bipartite matching so a broad Claim cannot falsely count as
        # coverage for several Gold events with the same span/type.
        claim_to_event: dict[str, int] = {}

        def assign(event_index: int, seen: set[str]) -> bool:
            for claim_id in case_events[event_index]["candidate_claim_ids"]:
                if claim_id in seen:
                    continue
                seen.add(claim_id)
                previous = claim_to_event.get(claim_id)
                if previous is None or assign(previous, seen):
                    claim_to_event[claim_id] = event_index
                    return True
            return False

        for event_index in range(len(case_events)):
            assign(event_index, set())
        event_to_claim = {
            event_index: claim_id
            for claim_id, event_index in claim_to_event.items()
        }

        for event_index, event_row in enumerate(case_events):
            event_type = event_row["event_type"]
            matched_claim = event_to_claim.get(event_index, "")
            action_ok = bool(matched_claim)
            action_hits += int(action_ok)
            by_event_type_hit[event_type] += int(action_ok)
            exact_span_hits += int(
                matched_claim in event_row["exact_claim_ids"]
            )
            if not event_row["entity_ok"] or not action_ok:
                if not event_row["entity_ok"]:
                    failure_reason = "entity_missing_or_ineligible"
                elif not event_row["type_candidate_count"]:
                    failure_reason = "event_type_not_detected"
                elif not event_row["subject_candidate_count"]:
                    failure_reason = "subject_not_bound_to_claim"
                elif not event_row["discriminator_candidate_count"]:
                    failure_reason = "atomic_discriminator_mismatch"
                elif not event_row["candidate_claim_ids"]:
                    failure_reason = "evidence_span_mismatch"
                else:
                    failure_reason = "claim_reused_by_one_to_one_matching"
                failure_reasons[failure_reason] += 1
                failures.append(
                    {
                        "key": event_row["key"],
                        "company": event_row["company"],
                        "event_type": event_type,
                        "entity_covered": event_row["entity_ok"],
                        "action_covered": action_ok,
                        "evidence": event_row["evidence"],
                        "candidate_claim_count": len(
                            event_row["candidate_claim_ids"]
                        ),
                        "type_candidate_count": event_row[
                            "type_candidate_count"
                        ],
                        "subject_candidate_count": event_row[
                            "subject_candidate_count"
                        ],
                        "discriminator_candidate_count": event_row[
                            "discriminator_candidate_count"
                        ],
                        "failure_reason": failure_reason,
                        "candidate_previews": event_row[
                            "discriminator_candidate_previews"
                        ],
                    }
                )

    entity_recall = entity_hits / entity_total if entity_total else 1.0
    action_recall = action_hits / action_total if action_total else 1.0
    exact_span_recall = exact_span_hits / action_total if action_total else 1.0
    return {
        "schema_version": 1,
        "dataset_version": gold.get("dataset_version", ""),
        "note": (
            "Coverage audit uses one-to-one Gold-to-Claim matching because one "
            "V27 Claim can emit only one event. It does not measure entity "
            "precision or model adjudication accuracy. The supplied Gold set "
            "must already be opened for development."
        ),
        "status": (
            "PASS" if entity_recall >= 0.98 and action_recall >= 0.95 else "FAIL"
        ),
        "gold_event_count": action_total,
        "entity_recall": entity_recall,
        "action_claim_recall": action_recall,
        "exact_span_recall": exact_span_recall,
        "entity_ledger_row_count": entity_rows,
        "eligible_entity_row_count": eligible_rows,
        "action_span_count": action_spans,
        "atomic_claim_count": atomic_claims,
        "by_event_type": {
            event_type: {
                "gold": count,
                "covered": by_event_type_hit[event_type],
                "recall": by_event_type_hit[event_type] / count,
            }
            for event_type, count in sorted(by_event_type.items())
        },
        "failure_reason_counts": dict(sorted(failure_reasons.items())),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bundle",
        type=Path,
        default=PROJECT_ROOT / "evaluation/semantic-v25/final-v1-bundle.jsonl",
    )
    parser.add_argument(
        "--gold",
        type=Path,
        default=PROJECT_ROOT / "evaluation/semantic-v25/gold-v1/adjudication.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(_load(args.bundle), _load(args.gold))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.buffer.write(rendered.encode("utf-8"))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
