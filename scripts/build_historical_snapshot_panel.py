#!/usr/bin/env python3
"""Build monthly historical company samples from frozen news and job artifacts."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from ht_lead_radar.historical_panel import build_historical_panel


def _read(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--news", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--cutoff", action="append", required=True)
    parser.add_argument("--artifact-root", type=Path, default=Path("."))
    parser.add_argument("--horizon-days", type=int, default=90)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    panel = build_historical_panel(
        pool=_read(args.pool),
        news=_read(args.news),
        jobs=_read(args.jobs),
        cutoffs=[date.fromisoformat(value) for value in args.cutoff],
        artifact_root=args.artifact_root,
        horizon_days=args.horizon_days,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(panel, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(panel["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
