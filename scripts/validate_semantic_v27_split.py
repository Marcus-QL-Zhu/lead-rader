#!/usr/bin/env python3
"""Validate frozen prompt-loop train/holdout/reserve isolation."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
import unicodedata

from scripts.semantic_v27_prompt_variants import MAX_PROMPT_ROUNDS


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object:{path}")
    return payload


def _company_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def validate_split(
    split: dict[str, Any],
    gold: dict[str, Any],
    *,
    bundle_sha256: str,
    gold_sha256: str,
) -> dict[str, Any]:
    rounds = list(split.get("rounds") or [])
    round_keys = [
        str(key)
        for row in rounds
        for key in row.get("training_keys") or []
    ]
    holdout = [str(key) for key in split.get("holdout_keys") or []]
    reserve = [str(key) for key in split.get("reserve_keys") or []]
    gold_by_key = {str(case["key"]): case for case in gold.get("cases") or []}
    all_keys = round_keys + holdout + reserve
    training_companies = {
        _company_key(str(event["canonical_company"]))
        for key in round_keys
        for event in gold_by_key.get(key, {}).get("annotation", {}).get(
            "gold_events", []
        )
    }
    holdout_companies = {
        _company_key(str(event["canonical_company"]))
        for key in holdout
        for event in gold_by_key.get(key, {}).get("annotation", {}).get(
            "gold_events", []
        )
    }
    overlap = sorted(training_companies & holdout_companies)
    constraints = split.get("constraints")
    constraints = constraints if isinstance(constraints, dict) else {}
    declared_max_rounds = constraints.get("maximum_rounds")
    try:
        declared_max_rounds = int(declared_max_rounds)
    except (TypeError, ValueError):
        declared_max_rounds = None
    try:
        round_numbers = [int(row.get("round")) for row in rounds]
    except (TypeError, ValueError):
        round_numbers = []
    gates = {
        "source_bundle_hash_matches": split.get("source_bundle_sha256")
        == bundle_sha256,
        "source_gold_hash_matches": split.get("source_gold_sha256") == gold_sha256,
        "maximum_rounds_declared": declared_max_rounds == MAX_PROMPT_ROUNDS,
        "three_rounds": len(rounds) == MAX_PROMPT_ROUNDS,
        "round_numbers_contiguous": round_numbers
        == list(range(1, MAX_PROMPT_ROUNDS + 1)),
        "three_articles_per_round": all(
            len(row.get("training_keys") or []) == 3 for row in rounds
        ),
        "all_partitions_disjoint": len(all_keys) == len(set(all_keys)),
        "all_gold_cases_partitioned": set(all_keys) == set(gold_by_key),
        "holdout_has_at_least_three_articles": len(holdout) >= 3,
        "training_holdout_company_isolated": not overlap,
    }
    return {
        "schema_version": 1,
        "status": "PASS" if all(gates.values()) else "FAIL",
        "gates": gates,
        "training_article_count": len(round_keys),
        "holdout_article_count": len(holdout),
        "reserve_article_count": len(reserve),
        "training_company_count": len(training_companies),
        "holdout_company_count": len(holdout_companies),
        "training_holdout_company_overlap": overlap,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate_split(
        _load(args.split),
        _load(args.gold),
        bundle_sha256=sha256(args.bundle.read_bytes()).hexdigest(),
        gold_sha256=sha256(args.gold.read_bytes()).hexdigest(),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
