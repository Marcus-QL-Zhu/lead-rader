#!/usr/bin/env python3
"""Validate one Gold packet and optionally compare independent annotations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ht_lead_radar.semantic_gold import (
    compare_primary_annotations,
    validate_gold_packet,
)


def _read(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("packet", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    packet = _read(args.packet)
    result = {"validation": validate_gold_packet(packet)}
    if args.compare:
        other = _read(args.compare)
        result["comparison"] = compare_primary_annotations(packet, other)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["validation"]["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
