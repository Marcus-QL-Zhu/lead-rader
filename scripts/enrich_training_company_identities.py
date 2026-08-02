#!/usr/bin/env python3
"""Add stable company/family identities and harmonize leakage-safe splits."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "evaluation" / "training-v3" / "company-pool.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "training-v3" / "company-pool-v2.json"

FAMILY_GROUPS = {
    "siemens": {
        "members": {
            "\u897f\u95e8\u5b50\uff08\u4e2d\u56fd\uff09",
            "\u897f\u95e8\u5b50\u533b\u7597\uff08\u4e2d\u56fd\uff09",
            "\u74e6\u91cc\u5b89\uff08\u4e2d\u56fd\uff09",
        },
        "split": "test",
    },
}


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def enrich_pool(pool: Mapping[str, Any]) -> dict[str, Any]:
    groups_by_member = {
        member: (group, config)
        for group, config in FAMILY_GROUPS.items()
        for member in config["members"]
    }
    companies: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in pool.get("companies") or []:
        if not isinstance(raw, Mapping):
            continue
        company = str(raw.get("company") or "").strip()
        if not company:
            continue
        canonical_id = _stable_id("co", company)
        if canonical_id in seen_ids:
            raise ValueError(f"duplicate canonical identity: {company}")
        seen_ids.add(canonical_id)
        group_record = groups_by_member.get(company)
        if group_record:
            group, config = group_record
            family_id = _stable_id("fam", f"corporate-family:{group}")
            split = str(config["split"])
        else:
            family_id = _stable_id("fam", company)
            split = str(raw.get("split") or "")
        row = {
            **dict(raw),
            "canonical_company_id": canonical_id,
            "corporate_family_id": family_id,
            "split": split,
        }
        prior_split = str(raw.get("split") or "")
        if prior_split and prior_split != split:
            row["split_before_family_harmonization"] = prior_split
        companies.append(row)
    family_splits: dict[str, set[str]] = {}
    for row in companies:
        family_splits.setdefault(row["corporate_family_id"], set()).add(row["split"])
    leaking = [family for family, splits in family_splits.items() if len(splits) > 1]
    if leaking:
        raise ValueError(f"corporate-family split leakage: {sorted(leaking)}")
    identity_digest = hashlib.sha256(
        json.dumps(
            companies,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **{key: value for key, value in pool.items() if key != "companies"},
        "schema_version": 2,
        "identity_policy": "sha256-canonical-company-v1; corporate-family-isolated",
        "family_split_overrides": {
            group: str(config["split"])
            for group, config in FAMILY_GROUPS.items()
        },
        "companies": companies,
        "identity_sha256": identity_digest,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = _parser().parse_args()
    pool = json.loads(args.input.read_text(encoding="utf-8"))
    output = enrich_pool(pool)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts = {
        split: sum(row["split"] == split for row in output["companies"])
        for split in ("train", "calibration", "test")
    }
    print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
