#!/usr/bin/env python3
"""Query persisted talent-theme to company-role mappings for float analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ht_lead_radar.talent_pool_store import TalentPoolStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-db", default="data/talent-pool.sqlite")
    parser.add_argument("--term", action="append", default=[])
    parser.add_argument("--direction", default="")
    parser.add_argument("--current-only", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    values = TalentPoolStore(args.state_db).find_opportunities(
        terms=args.term,
        direction=args.direction,
        current_only=args.current_only,
        limit=args.limit,
    )
    print(json.dumps(values, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())