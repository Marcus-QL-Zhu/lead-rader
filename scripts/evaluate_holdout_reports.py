#!/usr/bin/env python3
"""Evaluate frozen holdout reports against their pre-registered manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ht_lead_radar.holdout_evaluation import evaluate_holdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", nargs="+")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    reports = [
        json.loads(Path(path).read_text(encoding="utf-8"))
        for path in args.reports
    ]
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    result = evaluate_holdout(reports, manifest)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())