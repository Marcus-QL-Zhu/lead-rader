#!/usr/bin/env python3
"""Evaluate V27 on an explicitly opened development Gold set.

This evaluator deliberately ignores the obsolete V1 candidate-disposition IDs.
It cannot be used to claim an independent test pass.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from scripts.evaluate_semantic_v25_final import evaluate


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object:{path}")
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _filter_gold(
    gold: dict[str, Any], keys: Iterable[str] | None
) -> dict[str, Any]:
    if not keys:
        return gold
    requested = set(keys)
    available = {str(case["key"]) for case in gold.get("cases") or []}
    unknown = sorted(requested - available)
    if unknown:
        raise ValueError(f"unknown Gold key(s):{unknown}")
    filtered = dict(gold)
    filtered["cases"] = [
        case
        for case in gold.get("cases") or []
        if str(case["key"]) in requested
    ]
    return filtered


def _filter_prediction(
    prediction: dict[str, Any], keys: Iterable[str] | None
) -> dict[str, Any]:
    if not keys:
        return prediction
    requested = set(keys)
    filtered = dict(prediction)
    filtered["results"] = [
        result
        for result in prediction.get("results") or []
        if str(result["key"]) in requested
    ]
    filtered["summary"] = _claim_summary(filtered["results"])
    return filtered


def _claim_summary(results: Iterable[dict[str, Any]]) -> dict[str, int]:
    rows = list(results)
    audits = [dict(row.get("audit") or {}) for row in rows]
    return {
        "article_count": len(rows),
        "event_count": sum(len(row.get("events") or []) for row in rows),
        "claim_count": sum(int(audit.get("candidate_count") or 0) for audit in audits),
        "accepted_claim_count": sum(
            len(audit.get("accepted_claim_ids") or []) for audit in audits
        ),
        "rejected_claim_count": sum(
            len(audit.get("rejected_claim_ids") or []) for audit in audits
        ),
        "failed_claim_count": sum(
            len(audit.get("failed_claim_ids") or []) for audit in audits
        ),
        "host_fallback_claim_count": sum(
            len(audit.get("host_fallback_claim_ids") or []) for audit in audits
        ),
        "strict_ready_article_count": sum(
            bool(audit.get("strict_claim_contract_ready")) for audit in audits
        ),
    }


def _evaluation_keys(
    prediction: dict[str, Any],
    explicit_keys: Iterable[str] | None,
) -> list[str] | None:
    if explicit_keys:
        return list(dict.fromkeys(str(value) for value in explicit_keys))
    selected = [str(value) for value in prediction.get("selected_keys") or []]
    return list(dict.fromkeys(selected)) or None


def _host_contract_counts(
    gold: dict[str, Any], prediction: dict[str, Any]
) -> dict[str, int]:
    body_by_key = {
        str(case["key"]): str(case.get("clean_body") or "")
        for case in gold.get("cases") or []
    }
    uncited = 0
    ungrounded = 0
    missing_host_ids = 0
    for result in prediction.get("results") or []:
        body = body_by_key.get(str(result["key"]), "")
        for event in result.get("events") or []:
            quotes = [str(value) for value in event.get("evidence_quotes") or []]
            if not quotes:
                uncited += 1
            if any(not quote or quote not in body for quote in quotes):
                ungrounded += 1
            if not (
                event.get("subject_entity_id")
                and event.get("claim_ids")
                and event.get("span_ids")
            ):
                missing_host_ids += 1
    return {
        "uncited_event_count": uncited,
        "ungrounded_evidence_event_count": ungrounded,
        "missing_host_id_event_count": missing_host_ids,
    }


def evaluate_packet(
    gold: dict[str, Any], prediction: dict[str, Any]
) -> dict[str, Any]:
    prediction_purpose = str(prediction.get("purpose") or "")
    result_purposes = {
        "opened-formal-v1-development-error-set-only": (
            "opened-development-evaluation-only-not-independent-test"
        ),
        "opened-semantic-v27-development-v2": (
            "opened-development-evaluation-only-not-independent-test"
        ),
        "opened-semantic-v27-prompt-loop-training": (
            "opened-development-prompt-loop-training-only"
        ),
        "opened-semantic-v27-prompt-loop-holdout": (
            "opened-development-company-isolated-holdout"
        ),
        "reserve-v1-one-time-prevalidation": "reserve-v1-one-time-prevalidation",
        "final-v2-one-time-acceptance": "final-v2-one-time-acceptance",
    }
    if prediction_purpose not in result_purposes:
        raise ValueError(f"unsupported V27 prediction purpose:{prediction_purpose}")
    base = evaluate(gold, prediction)
    claims = _claim_summary(prediction.get("results") or [])
    host_contract = _host_contract_counts(gold, prediction)
    zero_event_clean = (
        int(base["overall"].get("gold_event_count") or 0) == 0
        and int(base["overall"].get("predicted_event_count") or 0) == 0
    )
    terminal = (
        claims["accepted_claim_count"]
        + claims["rejected_claim_count"]
        + claims["failed_claim_count"]
    )
    gates = {
        "prediction_complete": prediction.get("status") == "complete",
        "all_articles_strict_ready": claims["strict_ready_article_count"]
        == len(gold.get("cases") or []),
        "all_claims_terminal": terminal == claims["claim_count"],
        "no_failed_claims": claims["failed_claim_count"] == 0,
        "no_uncited_events": host_contract["uncited_event_count"] == 0,
        "no_ungrounded_evidence": host_contract[
            "ungrounded_evidence_event_count"
        ]
        == 0,
        "no_missing_host_ids": host_contract["missing_host_id_event_count"] == 0,
        "no_unsupported_predicted_events": base["overall"][
            "unsupported_predicted_event_count"
        ]
        == 0,
        "company_subject_precision_at_least_98pct": (
            zero_event_clean
            or (
                base["overall"]["company_subject_precision"] is not None
                and base["overall"]["company_subject_precision"] >= 0.98
            )
        ),
        "strong_current_recall_at_least_90pct": (
            zero_event_clean
            or (
                base["overall"]["strong_current_recall"] is not None
                and base["overall"]["strong_current_recall"] >= 0.90
            )
        ),
        "status_accuracy_at_least_90pct": (
            zero_event_clean
            or (
                base["overall"]["status_accuracy"] is not None
                and base["overall"]["status_accuracy"] >= 0.90
            )
        ),
    }
    return {
        "schema_version": 1,
        "dataset_version": gold.get("dataset_version"),
        "purpose": result_purposes[prediction_purpose],
        "overall": base["overall"],
        "by_document_type": base["by_document_type"],
        "by_event_type": base["by_event_type"],
        "claim_contract": claims,
        "host_contract": host_contract,
        "legacy_candidate_dispositions": {
            "status": "not_applicable",
            "reason": "V27 Action Claim IDs replace V1 candidate IDs",
        },
        "gates": gates,
        "passed": all(gates.values()),
    }


def evaluate_development(
    gold: dict[str, Any], prediction: dict[str, Any]
) -> dict[str, Any]:
    if prediction.get("purpose") != "opened-formal-v1-development-error-set-only":
        raise ValueError("prediction is not marked as the opened V27 development set")
    return evaluate_packet(gold, prediction)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key", action="append")
    args = parser.parse_args()
    prediction_raw = _read(args.prediction)
    keys = _evaluation_keys(prediction_raw, args.key)
    gold = _filter_gold(_read(args.gold), keys)
    prediction = _filter_prediction(prediction_raw, keys)
    result = evaluate_packet(gold, prediction)
    _write(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
