#!/usr/bin/env python3
"""Assess whether historical plus snapshot data is fit for model evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_THRESHOLDS = {
    "train_positive_companies": 25,
    "calibration_positive_companies": 8,
    "test_positive_companies": 12,
    "strict_precursor_evidence_companies": 30,
    "event_types": 10,
    "replayable_job_labels": 1,
}


def assess(
    *,
    historical: dict[str, Any],
    snapshot: dict[str, Any],
    thresholds: dict[str, int] | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or DEFAULT_THRESHOLDS
    positives: dict[str, set[str]] = {
        "train": set(),
        "calibration": set(),
        "test": set(),
    }
    for row in historical["rows"]:
        if row.get("label") == "positive" and row.get("split") in positives:
            positives[row["split"]].add(row["company"])
    provisional = {split: set(values) for split, values in positives.items()}
    replayable_job_labels = 0
    for row in snapshot["job_label_candidates"]:
        split = row["split"]
        provisional[split].add(row["company"])
        if row.get("label_status") == "replayable_exact_job_artifact":
            positives[split].add(row["company"])
            replayable_job_labels += 1

    observed = {
        "train_positive_companies": len(provisional["train"]),
        "calibration_positive_companies": len(provisional["calibration"]),
        "test_positive_companies": len(provisional["test"]),
        "strict_precursor_evidence_companies": snapshot["counts"][
            "evidence_companies"
        ],
        "event_types": snapshot["counts"]["event_types"],
        "replayable_job_labels": replayable_job_labels,
    }
    checks = {
        name: {
            "observed": observed[name],
            "required": required,
            "passed": observed[name] >= required,
        }
        for name, required in thresholds.items()
    }
    return {
        "ready": all(check["passed"] for check in checks.values()),
        "policy": (
            "Positive-company counts include verified search-snapshot candidates "
            "only as provisional feasibility counts. Production readiness still "
            "requires replayable exact job-page artifacts."
        ),
        "checks": checks,
        "strict_historical_positive_companies": {
            split: len(values) for split, values in positives.items()
        },
        "provisional_positive_companies": {
            split: len(values) for split, values in provisional.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = assess(
        historical=json.loads(args.historical.read_text(encoding="utf-8")),
        snapshot=json.loads(args.snapshot.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
