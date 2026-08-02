#!/usr/bin/env python3
"""Create the scope-corrected V27 development Gold lineage v2.

Lineage v1 remains immutable.  V2 removes two conference-hosting events whose
subjects are investment/financial-service organizations rather than hard-tech
operating companies.  The change is a benchmark-scope correction, not a model
prediction-driven edit.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


PARENT_DATASET_VERSION = "semantic-v27-final-v2-lineage-v1"
DATASET_VERSION = "semantic-v27-final-v2-lineage-v2"
SCOPE_CASE = "pedaily-investment-news:564315"
SCOPE_COMPANIES = {"清科控股", "吴中金控集团"}


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return payload


def build(parent: dict[str, Any], *, parent_file: str, parent_sha256: str) -> dict[str, Any]:
    if parent.get("dataset_version") != PARENT_DATASET_VERSION:
        raise ValueError("unexpected parent dataset version")
    output = deepcopy(parent)
    output["dataset_version"] = DATASET_VERSION
    output["annotation_role"] = "scope_corrected_development_lineage"
    output["annotator_id"] = "lead-radar-scope-contract-v2"
    output["instructions"] = (
        "Opened development Gold only. Excludes non-hard-tech investment and "
        "financial-service organizations from operating-company event Gold."
    )
    output["lineage"] = {
        "parent_dataset_version": PARENT_DATASET_VERSION,
        "parent_file": parent_file,
        "parent_sha256": parent_sha256,
        "benchmark_status": "opened_development_only",
        "parent_prediction_must_not_be_rerun": True,
        "scope_contract": {
            "eligible_subject": (
                "hard-tech operating company or a uniquely grounded operating "
                "company brand"
            ),
            "excluded_subjects": [
                "investment institution",
                "fund",
                "financial-service organization",
                "media",
                "government/public body",
                "product/project/concept",
            ],
        },
        "changes": [],
    }

    removed = 0
    for case in output.get("cases", []):
        if case.get("key") != SCOPE_CASE:
            continue
        annotation = case["annotation"]
        kept = []
        exclusions = list(annotation.get("review_exclusions") or [])
        lineage_changes = list(annotation.get("lineage_changes") or [])
        for index, event in enumerate(annotation.get("gold_events") or []):
            if (
                event.get("canonical_company") in SCOPE_COMPANIES
                and event.get("event_type") == "partnership"
                and "SuperLink大会" in str(event.get("evidence_span", {}).get("text") or "")
            ):
                removed += 1
                change_id = f"scope-v2:{SCOPE_CASE}:{index}"
                change = {
                    "change_id": change_id,
                    "decision": "drop",
                    "reason_code": "non_hardtech_financial_hosting_subject",
                    "reason": (
                        "The subject is an investment/financial-service organizer, "
                        "not a hard-tech operating company in the Lead Radar "
                        "recruiting-opportunity scope."
                    ),
                    "guide_sections": [
                        "operating-company subject",
                        "non-operating entity exclusion",
                    ],
                    "lineage_v1_event_index": index,
                    "event": deepcopy(event),
                }
                exclusions.append(change)
                lineage_changes.append(change)
                output["lineage"]["changes"].append(
                    {
                        "case_key": SCOPE_CASE,
                        **deepcopy(change),
                    }
                )
                continue
            kept.append(event)
        annotation["gold_events"] = kept
        annotation["review_exclusions"] = exclusions
        annotation["lineage_changes"] = lineage_changes

    if removed != 2:
        raise ValueError(f"expected exactly 2 scope removals, got {removed}")
    output["lineage"]["removed_event_count"] = removed
    output["lineage"]["final_event_count"] = sum(
        len(case.get("annotation", {}).get("gold_events") or [])
        for case in output.get("cases", [])
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        _load(args.parent),
        parent_file=args.parent.as_posix(),
        parent_sha256=_sha256(args.parent),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "dataset_version": result["dataset_version"],
                "removed_event_count": result["lineage"]["removed_event_count"],
                "final_event_count": result["lineage"]["final_event_count"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
