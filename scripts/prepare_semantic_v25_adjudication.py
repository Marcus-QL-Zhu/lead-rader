#!/usr/bin/env python3
"""Build a blinded adjudication packet from two valid primary Gold packets."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from ht_lead_radar.semantic_gold import (
    compare_primary_annotations,
    validate_gold_packet,
)


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def build_adjudication(
    first: dict[str, Any],
    second: dict[str, Any]
) -> dict[str, Any]:
    for label, packet in (("first", first), ("second", second)):
        validation = validate_gold_packet(packet)
        if not validation["valid"]:
            raise ValueError(f"{label} primary annotation is invalid")
    if first.get("dataset_version") != second.get("dataset_version"):
        raise ValueError("primary annotation dataset versions differ")
    comparison = compare_primary_annotations(first, second)
    disagreement_keys = set(comparison["disagreement_keys"])
    second_by_key = {str(case["key"]): case for case in second["cases"]}
    cases: list[dict[str, Any]] = []
    for source_case in first["cases"]:
        key = str(source_case["key"])
        case = deepcopy(source_case)
        case["adjudication_audit"] = {
            "resolution": "requires_adjudication"
            if key in disagreement_keys
            else "primary_agreement",
        }
        if key in disagreement_keys:
            case["primary_annotations"] = {
                "annotator_a": deepcopy(source_case["annotation"]),
                "annotator_b": deepcopy(second_by_key[key]["annotation"]),
            }
            case["annotation"] = {
                "candidate_dispositions": [],
                "gold_events": [],
                "article_notes": "",
                "annotation_status": "unlabelled",
            }
        cases.append(case)
    return {
        "schema_version": 1,
        "dataset_version": first["dataset_version"],
        "annotation_role": "independent_adjudicator",
        "annotator_id": "adjudicator",
        "instructions": first.get("instructions"),
        "source_manifest_sha256": first.get("source_manifest_sha256"),
        "source_bundle_sha256": first.get("source_bundle_sha256"),
        "comparison": comparison,
        "status": "awaiting_adjudication" if disagreement_keys else "complete",
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_adjudication(_read(args.first), _read(args.second))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["comparison"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
