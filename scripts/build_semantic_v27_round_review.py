#!/usr/bin/env python3
"""Build an anonymized, prompt-free packet for independent round review."""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import random
from typing import Any, Sequence


def _load(path: Path) -> dict[str, Any]:
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


def _selection_eligible(
    prediction: dict[str, Any], evaluation: dict[str, Any]
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    summary = dict(prediction.get("summary") or {})
    host = dict(evaluation.get("host_contract") or {})
    overall = dict(evaluation.get("overall") or {})
    if int(summary.get("failed_claim_count") or 0):
        reasons.append("failed_claims")
    if any(
        row.get("audit", {}).get("infrastructure_errors")
        for row in prediction.get("results") or []
    ):
        reasons.append("infrastructure_failure")
    if int(host.get("uncited_event_count") or 0):
        reasons.append("uncited_events")
    if int(host.get("ungrounded_evidence_event_count") or 0):
        reasons.append("ungrounded_evidence")
    if int(overall.get("unsupported_predicted_event_count") or 0):
        reasons.append("unsupported_predicted_events")
    return not reasons, reasons


def build_packet(
    *,
    gold: dict[str, Any],
    predictions: Sequence[dict[str, Any]],
    evaluations: Sequence[dict[str, Any]],
    seed: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if len(predictions) != 3 or len(evaluations) != 3:
        raise ValueError("exactly three predictions and evaluations are required")
    key_sets = [tuple(row.get("selected_keys") or ()) for row in predictions]
    if not key_sets[0] or any(keys != key_sets[0] for keys in key_sets[1:]):
        raise ValueError("round candidates must use the same ordered article keys")
    if len({row.get("code_contract_sha256") for row in predictions}) != 1:
        raise ValueError("round candidates must use the same code contract")
    if len({row.get("model") for row in predictions}) != 1:
        raise ValueError("round candidates must use the same model")

    labels = ["candidate-alpha", "candidate-beta", "candidate-gamma"]
    random.Random(seed).shuffle(labels)
    gold_by_key = {
        str(case["key"]): deepcopy(case)
        for case in gold.get("cases") or []
    }
    candidates = []
    mapping: dict[str, Any] = {}
    for label, prediction, evaluation in zip(labels, predictions, evaluations):
        eligible, reasons = _selection_eligible(prediction, evaluation)
        safe_prediction = {
            key: deepcopy(value)
            for key, value in prediction.items()
            if key
            not in {
                "prompt_config",
                "prompt_config_sha256",
                "prompt_version",
                "variant",
            }
        }
        candidates.append(
            {
                "label": label,
                "selection_eligible": eligible,
                "ineligibility_reasons": reasons,
                "prediction": safe_prediction,
                "evaluation": deepcopy(evaluation),
            }
        )
        mapping[label] = {
            "variant": prediction.get("variant"),
            "prompt_version": prediction.get("prompt_config", {}).get(
                "prompt_version"
            ),
            "prompt_config_sha256": prediction.get("prompt_config_sha256"),
        }

    packet = {
        "schema_version": 1,
        "purpose": "blind-independent-semantic-v27-round-review",
        "seed_sha256": sha256(seed.encode("utf-8")).hexdigest(),
        "selected_keys": list(key_sets[0]),
        "rubric": {
            "hard_disqualifiers": [
                "infrastructure failure",
                "failed or non-terminal Claim",
                "unsupported or uncited event",
                "wrong operating-company subject",
            ],
            "rank_order": [
                "company subject precision",
                "supported strong-current event recall",
                "event status accuracy",
                "atomic completeness without summary duplicates",
                "projection stability",
            ],
            "required_output": (
                "rank all eligible anonymous candidates, select at most one "
                "winner, cite case-level errors, and say no-winner if all fail"
            ),
        },
        "gold_cases": [gold_by_key[key] for key in key_sets[0]],
        "candidates": candidates,
    }
    return packet, {
        "schema_version": 1,
        "purpose": "private-round-label-mapping",
        "mapping": mapping,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--prediction", type=Path, action="append", required=True)
    parser.add_argument("--evaluation", type=Path, action="append", required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mapping-output", type=Path, required=True)
    args = parser.parse_args()
    packet, mapping = build_packet(
        gold=_load(args.gold),
        predictions=[_load(path) for path in args.prediction],
        evaluations=[_load(path) for path in args.evaluation],
        seed=args.seed,
    )
    _write(args.output, packet)
    _write(args.mapping_output, mapping)
    print(
        json.dumps(
            {
                "candidate_count": len(packet["candidates"]),
                "eligible_count": sum(
                    row["selection_eligible"] for row in packet["candidates"]
                ),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
