#!/usr/bin/env python3
"""Evaluate each frozen holdout case independently and require a clean streak."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.evaluate_semantic_v27_development import (
    _filter_gold,
    _filter_prediction,
    evaluate_packet,
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object:{path}")
    return payload


def evaluate_sequence(
    gold: dict[str, Any],
    prediction: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    keys = [str(value) for value in manifest.get("selected_keys") or []]
    required = int(manifest.get("required_consecutive_passes") or 0)
    if len(keys) != required or required < 1:
        raise ValueError("invalid frozen holdout sequence")
    if prediction.get("purpose") != "opened-semantic-v27-prompt-loop-holdout":
        raise ValueError("prediction is not marked as holdout")
    if list(prediction.get("selected_keys") or []) != keys:
        raise ValueError("prediction key order differs from frozen sequence")
    cases = []
    streak = 0
    for position, key in enumerate(keys, start=1):
        result = evaluate_packet(
            _filter_gold(gold, [key]),
            _filter_prediction(prediction, [key]),
        )
        passed = bool(result["passed"])
        streak = streak + 1 if passed else 0
        cases.append(
            {
                "position": position,
                "key": key,
                "passed": passed,
                "consecutive_passes_after_case": streak,
                "gates": result["gates"],
                "overall": result["overall"],
                "claim_contract": result["claim_contract"],
                "host_contract": result["host_contract"],
            }
        )
    passed = streak == required and all(row["passed"] for row in cases)
    return {
        "schema_version": 1,
        "purpose": "company-isolated-holdout-consecutive-pass-gate",
        "required_consecutive_passes": required,
        "final_consecutive_passes": streak,
        "cases": cases,
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--prediction", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate_sequence(
        _load(args.gold), _load(args.prediction), _load(args.manifest)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
