#!/usr/bin/env python3
"""Create the final scope-corrected V27 opened-development Gold lineage."""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from ht_lead_radar.semantic_gold import validate_gold_packet


PARENT_DATASET_VERSION = "semantic-v27-final-v2-lineage-v2"
DATASET_VERSION = "semantic-v27-final-v2-lineage-v3"
SCOPE_CASE = "nbd-vcpe-weekly:4482544"
SCOPE_COMPANY = "北京华夏视觉科技集团有限公司"


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return payload


def _refresh_audit(case: dict[str, Any]) -> None:
    audit = case.get("adjudication_audit")
    if not isinstance(audit, dict):
        return
    current = len(case.get("annotation", {}).get("gold_events") or [])
    added = int(audit.get("added_atomic_event_count") or 0)
    parent = int(audit.get("parent_event_count") or 0)
    kept = max(0, current - added)
    audit["kept_parent_event_count"] = kept
    audit["dropped_parent_event_count"] = max(0, parent - kept)


def build(parent: dict[str, Any], *, parent_file: str, parent_sha256: str) -> dict[str, Any]:
    if parent.get("dataset_version") != PARENT_DATASET_VERSION:
        raise ValueError("unexpected parent dataset version")
    output = deepcopy(parent)
    output["dataset_version"] = DATASET_VERSION
    output["annotation_role"] = "scope_corrected_development_lineage"
    output["annotator_id"] = "lead-radar-scope-contract-v3"
    output["instructions"] = (
        "Opened development Gold. Apply docs/semantic-event-gold-labeling-guide.md "
        "and the lineage scope contract; Gold is independent of predictions."
    )
    output["lineage"] = {
        "parent_dataset_version": PARENT_DATASET_VERSION,
        "parent_file": parent_file,
        "parent_sha256": parent_sha256,
        "benchmark_status": "opened_development_only",
        "parent_prediction_must_not_be_rerun": True,
        "base_gold_guide": "docs/semantic-event-gold-labeling-guide.md",
        "scope_contract": {
            "eligible_subject": (
                "a grounded operating company whose business is in the configured "
                "hard-tech scope"
            ),
            "excluded_subjects": [
                "investment or financial-service organization",
                "fund",
                "media or advertising operator",
                "government/public body",
                "product/project/concept",
            ],
        },
        "changes": [],
    }

    removed = 0
    for case in output.get("cases", []):
        annotation = case.get("annotation", {})
        if case.get("key") == SCOPE_CASE:
            kept = []
            exclusions = list(annotation.get("review_exclusions") or [])
            lineage_changes = list(annotation.get("lineage_changes") or [])
            for index, event in enumerate(annotation.get("gold_events") or []):
                if (
                    event.get("canonical_company") == SCOPE_COMPANY
                    and event.get("event_type") == "partnership"
                ):
                    removed += 1
                    change = {
                        "change_id": f"scope-v3:{SCOPE_CASE}:{index}",
                        "decision": "drop",
                        "reason_code": "non_hardtech_media_subsidiary_investment",
                        "reason": (
                            "The company is the wholly owned subsidiary of a media "
                            "operator and this event is only a fund investment, not "
                            "a hard-tech operating-company event."
                        ),
                        "guide_sections": [
                            "operating-company subject",
                            "non-operating entity exclusion",
                        ],
                        "lineage_v2_event_index": index,
                        "event": deepcopy(event),
                    }
                    exclusions.append(change)
                    lineage_changes.append(change)
                    output["lineage"]["changes"].append(
                        {"case_key": SCOPE_CASE, **deepcopy(change)}
                    )
                    continue
                kept.append(event)
            annotation["gold_events"] = kept
            annotation["review_exclusions"] = exclusions
            annotation["lineage_changes"] = lineage_changes
        _refresh_audit(case)

    if removed != 1:
        raise ValueError(f"expected exactly 1 scope removal, got {removed}")
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
    validation = validate_gold_packet(result)
    if not validation["valid"]:
        raise ValueError(f"generated Gold is invalid: {validation['errors']}")
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
                "validation": validation,
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
