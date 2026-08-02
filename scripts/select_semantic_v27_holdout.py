#!/usr/bin/env python3
"""Freeze a reproducible random three-case holdout sequence."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import random
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object:{path}")
    return payload


def select_holdout(
    split: dict[str, Any],
    *,
    prompt_config_sha256: str,
    count: int = 3,
) -> dict[str, Any]:
    keys = [str(value) for value in split.get("holdout_keys") or []]
    if count < 1 or len(keys) < count:
        raise ValueError("holdout does not contain enough cases")
    seed_material = (
        f"{split.get('dataset_version')}\0{prompt_config_sha256}\0{count}"
    )
    seed_sha256 = sha256(seed_material.encode("utf-8")).hexdigest()
    random.Random(int(seed_sha256, 16)).shuffle(keys)
    return {
        "schema_version": 1,
        "purpose": "frozen-company-isolated-consecutive-holdout-sequence",
        "split_dataset_version": split.get("dataset_version"),
        "prompt_config_sha256": prompt_config_sha256,
        "seed_sha256": seed_sha256,
        "required_consecutive_passes": count,
        "selected_keys": keys[:count],
        "selection_frozen_before_inference": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--prompt-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    config = _load(args.prompt_config)
    config = dict(config.get("prompt_config") or config)
    config_hash = sha256(
        json.dumps(
            config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    result = select_holdout(
        _load(args.split),
        prompt_config_sha256=config_hash,
        count=args.count,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
