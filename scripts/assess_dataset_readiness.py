from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_GATES = {
    "train_positive_companies": 25,
    "calibration_positive_companies": 8,
    "test_positive_companies": 12,
    "positive_role_families": 10,
    "replayable_negative_rows": 1,
}


def assess(payload: dict, gates: dict[str, int] | None = None) -> dict:
    thresholds = gates or DEFAULT_GATES
    positive_rows = [row for row in payload["rows"] if row["label"] == "positive"]
    positive_companies = {
        split: {
            row["company_id"]
            for row in positive_rows
            if row["split"] == split
        }
        for split in ("train", "calibration", "test")
    }
    role_families = {row["role_family"] for row in positive_rows}
    replayable_negatives = [
        row
        for row in payload["rows"]
        if row["label"] == "negative" and row["observability"] == "replayable"
    ]
    actual = {
        "train_positive_companies": len(positive_companies["train"]),
        "calibration_positive_companies": len(positive_companies["calibration"]),
        "test_positive_companies": len(positive_companies["test"]),
        "positive_role_families": len(role_families),
        "replayable_negative_rows": len(replayable_negatives),
    }
    checks = {
        name: {
            "actual": actual[name],
            "required": required,
            "passed": actual[name] >= required,
        }
        for name, required in thresholds.items()
    }
    return {
        "ready": all(item["passed"] for item in checks.values()),
        "checks": checks,
        "headline_metrics_allowed": (
            checks["test_positive_companies"]["passed"]
            and checks["positive_role_families"]["passed"]
        ),
        "propensity_training_allowed": checks["replayable_negative_rows"]["passed"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = assess(json.loads(args.dataset.read_text(encoding="utf-8")))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["ready"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
