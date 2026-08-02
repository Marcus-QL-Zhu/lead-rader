#!/usr/bin/env python3
"""Evaluate frozen Semantic v25 predictions against adjudicated Gold."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable

from ht_lead_radar.aggregate_adapters.entities import canonical_company_name
from ht_lead_radar.aggregate_adapters.entity_ledger import (
    ArticleEntityLedger,
    build_article_entity_ledger,
)
from ht_lead_radar.aggregate_adapters.models import CleanArticle, SourceArticleIndex
from ht_lead_radar.semantic_gold import validate_gold_packet


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _company(value: object) -> str:
    normalized = _text(canonical_company_name(str(value or "")))
    normalized = re.sub(r"(?:股份有限公司|有限责任公司|有限公司|集团)$", "", normalized)
    digit_map = str.maketrans("零〇一二三四五六七八九", "00123456789")
    return re.sub(
        r"[零〇一二三四五六七八九]{3,}",
        lambda match: match.group(0).translate(digit_map),
        normalized,
    )


def _atomic_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        character
        for character in normalized
        if character.isalnum() or character == "+"
    )


def _shingles(value: str, width: int = 5) -> set[str]:
    return (
        {value[index : index + width] for index in range(len(value) - width + 1)}
        if len(value) >= width
        else ({value} if value else set())
    )


def _evidence_aligned(gold: dict[str, Any], predicted: dict[str, Any]) -> bool:
    gold_claims = {str(value) for value in gold.get("claim_ids") or []}
    predicted_claims = {str(value) for value in predicted.get("claim_ids") or []}
    if gold_claims.intersection(predicted_claims):
        return True
    evidence_span = gold.get("evidence_span") or {}
    span = _text(
        evidence_span.get("text")
        if isinstance(evidence_span, dict)
        else evidence_span
    )
    if not span:
        return False
    for quote in predicted.get("evidence_quotes") or []:
        candidate = _text(quote)
        if not candidate:
            continue
        if min(len(span), len(candidate)) >= 8 and (
            span in candidate or candidate in span
        ):
            return True
        union = _shingles(span).union(_shingles(candidate))
        overlap = _shingles(span).intersection(_shingles(candidate))
        if union and len(overlap) / len(union) >= 0.5:
            return True
    return False


def _atomic_discriminator_aligned(
    gold: dict[str, Any], predicted: dict[str, Any]
) -> bool:
    discriminator = str(gold.get("atomic_discriminator") or "").strip()
    if not discriminator:
        return True
    if "=" not in discriminator:
        return discriminator == str(
            predicted.get("atomic_discriminator") or ""
        ).strip()
    field, expected = discriminator.split("=", 1)
    if field not in {
        "funding_round",
        "funding_amount",
        "cumulative_funding_amount",
    }:
        return False
    return _atomic_text(predicted.get(field)) == _atomic_text(expected)


def _entity_ledger_for_case(case: dict[str, Any]) -> ArticleEntityLedger:
    """Build the article-local alias graph used by the production extractor."""

    key = str(case.get("key") or "evaluation:unknown")
    source_id, _, article_id = key.partition(":")
    article = CleanArticle(
        index=SourceArticleIndex(
            source_id=str(case.get("source_id") or source_id or "evaluation"),
            source_article_id=article_id or key,
            channel="evaluation",
            canonical_url=str(case.get("canonical_url") or ""),
            title=str(case.get("title") or ""),
            published_at=str(case.get("published_at") or ""),
            discovered_at=str(case.get("published_at") or ""),
            cursor_value=article_id or key,
            listing_page="",
            listing_position=0,
            content_hash=str(case.get("article_sha256") or ""),
            discovery_method="frozen_evaluation",
        ),
        clean_body=str(case.get("clean_body") or ""),
        content_hash=str(case.get("article_sha256") or ""),
    )
    return build_article_entity_ledger(article, case.get("candidates") or [], [])


def _subject_equivalent(
    left: object,
    right: object,
    entity_ledger: ArticleEntityLedger | None,
) -> bool:
    if entity_ledger is not None:
        left_entity = entity_ledger.entity_for_name(str(left or ""))
        right_entity = entity_ledger.entity_for_name(str(right or ""))
        if left_entity is not None and right_entity is not None:
            return left_entity.entity_id == right_entity.entity_id
    return _company(left) == _company(right)


def _edge(
    gold: dict[str, Any],
    predicted: dict[str, Any],
    *,
    require_type: bool = True,
    require_status: bool = True,
    entity_ledger: ArticleEntityLedger | None = None,
) -> bool:
    if not _subject_equivalent(
        gold.get("canonical_company"),
        predicted.get("canonical_company"),
        entity_ledger,
    ):
        return False
    if require_type and gold.get("event_type") != predicted.get("event_type"):
        return False
    if require_status and gold.get("event_status") != predicted.get("event_status"):
        return False
    return _atomic_discriminator_aligned(gold, predicted) and _evidence_aligned(
        gold, predicted
    )


def _maximum_matches(
    gold: list[dict[str, Any]],
    predicted: list[dict[str, Any]],
    *,
    require_type: bool = True,
    require_status: bool = True,
    entity_ledger: ArticleEntityLedger | None = None,
) -> dict[int, int]:
    edges: dict[int, list[int]] = {}
    for left, gold_event in enumerate(gold):
        candidates = [
            right
            for right, event in enumerate(predicted)
            if _edge(
                gold_event,
                event,
                require_type=require_type,
                require_status=require_status,
                entity_ledger=entity_ledger,
            )
        ]
        if not require_status:
            candidates.sort(
                key=lambda right: (
                    gold_event.get("event_status")
                    != predicted[right].get("event_status"),
                    right,
                )
            )
        edges[left] = candidates
    predicted_to_gold: dict[int, int] = {}

    def augment(left: int, seen: set[int]) -> bool:
        for right in edges[left]:
            if right in seen:
                continue
            seen.add(right)
            incumbent = predicted_to_gold.get(right)
            if incumbent is None or augment(incumbent, seen):
                predicted_to_gold[right] = left
                return True
        return False

    for left in sorted(edges, key=lambda value: (len(edges[value]), value)):
        augment(left, set())
    return {left: right for right, left in predicted_to_gold.items()}


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _metric_counts(
    gold: list[dict[str, Any]],
    predicted: list[dict[str, Any]],
    entity_ledger: ArticleEntityLedger | None = None,
) -> Counter[str]:
    exact = _maximum_matches(gold, predicted, entity_ledger=entity_ledger)
    relaxed_status = _maximum_matches(
        gold,
        predicted,
        require_status=False,
        entity_ledger=entity_ledger,
    )
    # Subject identity is an independent axis.  It intentionally does not
    # depend on evidence, event type, status, discriminator, or one-to-one
    # event matching.
    subject_match_count = sum(
        any(
            _subject_equivalent(
                event.get("canonical_company"),
                gold_event.get("canonical_company"),
                entity_ledger,
            )
            for gold_event in gold
        )
        for event in predicted
    )
    strong_indexes = {
        index for index, event in enumerate(gold) if event.get("importance") == "strong"
    }
    strong_matches = len(strong_indexes.intersection(exact))
    status_correct = sum(
        gold[left].get("event_status") == predicted[right].get("event_status")
        for left, right in relaxed_status.items()
    )
    return Counter(
        {
            "gold_event_count": len(gold),
            "predicted_event_count": len(predicted),
            "exact_match_count": len(exact),
            "strong_gold_count": len(strong_indexes),
            "strong_match_count": strong_matches,
            "status_comparable_count": len(relaxed_status),
            "status_correct_count": status_correct,
            "subject_match_count": subject_match_count,
            "supported_event_count": len(relaxed_status),
        }
    )


def _format_metrics(counts: Counter[str]) -> dict[str, Any]:
    gold_count = counts["gold_event_count"]
    predicted_count = counts["predicted_event_count"]
    exact_count = counts["exact_match_count"]
    supported_count = counts["supported_event_count"]
    strong_count = counts["strong_gold_count"]
    status_count = counts["status_comparable_count"]
    subject_count = counts["subject_match_count"]
    return {
        "gold_event_count": gold_count,
        "predicted_event_count": predicted_count,
        "exact_match_count": exact_count,
        "exact_precision": _ratio(exact_count, predicted_count),
        "exact_recall": _ratio(exact_count, gold_count),
        "unsupported_predicted_event_count": predicted_count - supported_count,
        "event_support_precision": _ratio(supported_count, predicted_count),
        "event_support_recall": _ratio(supported_count, gold_count),
        "strong_gold_count": strong_count,
        "strong_current_recall": _ratio(counts["strong_match_count"], strong_count),
        "status_comparable_count": status_count,
        "status_accuracy": _ratio(counts["status_correct_count"], status_count),
        "subject_match_count": subject_count,
        "company_subject_precision": _ratio(subject_count, predicted_count),
    }


def _candidate_metrics(
    cases: Iterable[dict[str, Any]],
    predictions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    confusion: Counter[tuple[str, str]] = Counter()
    missing = 0
    total = 0
    for case in cases:
        audit = dict(predictions.get(str(case["key"]), {}).get("audit") or {})
        accepted = set(audit.get("model_accepted_candidate_ids") or []) | set(
            audit.get("accepted_candidate_ids") or []
        )
        ambiguous = set(audit.get("ambiguous_candidate_ids") or [])
        rejected = (
            set(audit.get("rejected_candidate_ids") or [])
            | set(audit.get("failed_candidate_ids") or [])
            | set(audit.get("deterministic_rejected_candidate_ids") or [])
            | set(audit.get("deterministic_rejected_claim_ids") or [])
        )
        for item in case.get("annotation", {}).get("candidate_dispositions") or []:
            claim_id = str(item.get("claim_id") or "")
            gold_value = str(item.get("disposition") or "")
            if claim_id in accepted:
                predicted_value = "accepted"
            elif claim_id in ambiguous:
                predicted_value = "ambiguous"
            elif claim_id in rejected:
                predicted_value = "rejected"
            else:
                predicted_value = "unadjudicated"
                missing += 1
            total += 1
            confusion[(gold_value, predicted_value)] += 1
    correct = sum(count for (gold, predicted), count in confusion.items() if gold == predicted)
    return {
        "candidate_count": total,
        "candidate_accuracy": _ratio(correct, total),
        "unadjudicated_candidate_count": missing,
        "confusion": {
            f"{gold}->{predicted}": count
            for (gold, predicted), count in sorted(confusion.items())
        },
    }


def evaluate(gold: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    validation = validate_gold_packet(gold)
    if not validation["valid"]:
        raise ValueError("Gold packet is invalid")
    if gold.get("dataset_version") != prediction.get("dataset_version"):
        raise ValueError("Gold and prediction dataset versions differ")
    predictions = {
        str(item["key"]): item for item in prediction.get("results") or []
    }
    gold_keys = {str(case["key"]) for case in gold.get("cases") or []}
    if set(predictions) != gold_keys:
        raise ValueError("Gold and prediction keys differ")

    eligible_cases = [
        case
        for case in gold["cases"]
        if case.get("annotation", {}).get("annotation_status") == "complete"
    ]
    excluded_ambiguous_count = len(gold["cases"]) - len(eligible_cases)
    overall_counts: Counter[str] = Counter()
    by_document_counts: dict[str, Counter[str]] = defaultdict(Counter)
    by_event_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for case in eligible_cases:
        key = str(case["key"])
        gold_events = list(case["annotation"].get("gold_events") or [])
        predicted_events = list(predictions[key].get("events") or [])
        document_type = str(case.get("document_type") or "unknown")
        entity_ledger = _entity_ledger_for_case(case)
        case_counts = _metric_counts(
            gold_events, predicted_events, entity_ledger
        )
        overall_counts.update(case_counts)
        by_document_counts[document_type].update(case_counts)
        event_types = {
            str(event.get("event_type") or "unknown")
            for event in gold_events + predicted_events
        }
        for event_type in event_types:
            typed_gold = [
                event
                for event in gold_events
                if str(event.get("event_type") or "unknown") == event_type
            ]
            typed_predicted = [
                event
                for event in predicted_events
                if str(event.get("event_type") or "unknown") == event_type
            ]
            by_event_counts[event_type].update(
                _metric_counts(typed_gold, typed_predicted, entity_ledger)
            )

    overall = _format_metrics(overall_counts)
    candidate = _candidate_metrics(eligible_cases, predictions)
    summary = dict(prediction.get("summary") or {})
    gates = {
        "prediction_complete": prediction.get("status") == "complete",
        "all_articles_present": len(predictions) == len(gold_keys),
        "no_uncited_final_events": int(summary.get("uncited_model_event_count") or 0)
        == 0,
        "no_bad_claim_pairs": int(summary.get("bad_claim_pair_event_count") or 0)
        == 0,
        "candidate_disposition_complete": candidate["unadjudicated_candidate_count"]
        == 0,
        "no_unsupported_predicted_events": (
            overall["unsupported_predicted_event_count"] == 0
        ),
        "all_articles_strict_ready": int(summary.get("strict_ready_article_count") or 0)
        == len(gold_keys),
        "company_subject_precision_at_least_98pct": (
            (overall["company_subject_precision"] or 0.0) >= 0.98
        ),
        "strong_current_recall_at_least_90pct": (
            (overall["strong_current_recall"] or 0.0) >= 0.90
        ),
        "status_accuracy_at_least_90pct": (
            (overall["status_accuracy"] or 0.0) >= 0.90
        ),
    }
    return {
        "schema_version": 2,
        "metric_contract": {
            "company_subject_precision": (
                "predicted events whose company is equivalent to any Gold company "
                "through the article-local alias graph / all predicted events; "
                "independent of event type, status, discriminator, and evidence"
            ),
            "event_support_precision": (
                "one-to-one subject+type+atomic-discriminator+evidence supported "
                "predicted events / all predicted events; status is ignored"
            ),
            "event_support_recall": (
                "one-to-one subject+type+atomic-discriminator+evidence supported "
                "Gold events / all Gold events; status is ignored"
            ),
            "status_accuracy": (
                "status-correct supported pairs / all supported pairs"
            ),
        },
        "dataset_version": gold["dataset_version"],
        "gold_validation": validation,
        "eligible_gold_case_count": len(eligible_cases),
        "excluded_gold_ambiguous_case_count": excluded_ambiguous_count,
        "prediction_summary": summary,
        "overall": overall,
        "candidate_dispositions": candidate,
        "by_document_type": {
            key: _format_metrics(by_document_counts[key])
            for key in sorted(by_document_counts)
        },
        "by_event_type": {
            key: _format_metrics(by_event_counts[key])
            for key in sorted(by_event_counts)
        },
        "gates": gates,
        "passed": all(gates.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(_read(args.gold), _read(args.prediction))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
