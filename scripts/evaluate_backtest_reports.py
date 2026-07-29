#!/usr/bin/env python3
"""Aggregate historical validation reports into count-based acceptance gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ht_lead_radar.evaluation import evaluate_acceptance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    values = [
        json.loads(Path(path).read_text(encoding="utf-8"))
        for path in args.reports
    ]
    result = evaluate_acceptance(values)
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
