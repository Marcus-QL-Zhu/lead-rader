#!/usr/bin/env python3
"""Audit lead-scope entity precision on the opened V27 development bundle.

This gate is intentionally separate from semantic recall.  Recall answers
"did we keep a real operating subject available for the event?"; this audit
answers "of the subjects offered to lead generation, how many are real
operating companies?"  The adjudication file records an independent blind
review and an expected row count so a later code change cannot silently add
unreviewed candidates.
"""

from __future__ import annotations

import argparse
from dataclasses import fields
import json
from pathlib import Path
import sys
import unicodedata
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ht_lead_radar.aggregate_adapters.entity_ledger import (  # noqa: E402
    build_article_entity_ledger,
)
from ht_lead_radar.aggregate_adapters.models import (  # noqa: E402
    CleanArticle,
    SourceArticleIndex,
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return payload


def _article(raw: dict[str, Any]) -> CleanArticle:
    index_fields = {item.name for item in fields(SourceArticleIndex)}
    article_fields = {item.name for item in fields(CleanArticle)} - {"index"}
    return CleanArticle(
        index=SourceArticleIndex(
            **{key: value for key, value in raw["index"].items() if key in index_fields}
        ),
        **{key: value for key, value in raw.items() if key in article_fields},
    )


def _key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if character.isalnum() or character == "+"
    )


def _candidate_key(article: CleanArticle, canonical_name: str) -> str:
    return (
        f"{article.index.source_id}:"
        f"{article.index.source_article_id}:"
        f"{canonical_name}"
    )


def audit(bundle: dict[str, Any], adjudication: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    duplicate_groups: list[list[str]] = []
    excluded: list[dict[str, Any]] = []

    for row in bundle.get("articles", []):
        article = _article(row["article"])
        ledger = build_article_entity_ledger(article, [], [])
        lead_entities = [
            entity for entity in ledger.entities if entity.lead_scope_eligible
        ]
        operating_entities = [
            entity
            for entity in ledger.entities
            if entity.operating_subject_eligible and not entity.lead_scope_eligible
        ]
        for entity in lead_entities:
            key = _candidate_key(article, entity.canonical_name)
            candidates.append(
                {
                    "key": key,
                    "source_id": article.index.source_id,
                    "source_article_id": article.index.source_article_id,
                    "article_key": row["key"],
                    "canonical_name": entity.canonical_name,
                    "aliases": list(entity.aliases),
                    "discovery_sources": list(entity.discovery_sources),
                }
            )
        for entity in operating_entities:
            excluded.append(
                {
                    "key": _candidate_key(article, entity.canonical_name),
                    "article_key": row["key"],
                    "canonical_name": entity.canonical_name,
                    "reason": "operating_subject_but_not_lead_scope",
                }
            )

        for left_index, left in enumerate(lead_entities):
            left_surfaces = {_key(left.canonical_name), *map(_key, left.aliases)}
            for right in lead_entities[left_index + 1 :]:
                right_surfaces = {_key(right.canonical_name), *map(_key, right.aliases)}
                if left_surfaces & right_surfaces:
                    duplicate_groups.append(
                        [
                            _candidate_key(article, left.canonical_name),
                            _candidate_key(article, right.canonical_name),
                        ]
                    )

    labels = adjudication.get("labels") or {}
    default_label = str(adjudication.get("default_label") or "")
    overrides = adjudication.get("overrides") or {}
    context_only_keys = set(adjudication.get("context_only_keys") or [])
    expected_count = adjudication.get("expected_candidate_count")
    label_rows: list[dict[str, Any]] = []
    missing_labels: list[str] = []
    for candidate in candidates:
        value = overrides.get(candidate["key"], default_label)
        if value not in {"tp", "fp"}:
            missing_labels.append(candidate["key"])
            continue
        label_rows.append({**candidate, "label": value})
    # An explicit labels map, when present, takes precedence over the compact
    # reviewer default.  This keeps the fixture readable while allowing
    # future adjudication to mark a single newly discovered false positive.
    for candidate in candidates:
        if candidate["key"] in labels:
            row = next(item for item in label_rows if item["key"] == candidate["key"])
            row["label"] = str(labels[candidate["key"]])
    unknown_labels = sorted(
        set(labels) - {candidate["key"] for candidate in candidates}
    )
    labeled = len(label_rows)
    true_positive = sum(row["label"] == "tp" for row in label_rows)
    precision = true_positive / labeled if labeled else 0.0
    status = (
        "PASS"
        if (
            expected_count == len(candidates)
            and not missing_labels
            and not unknown_labels
            and not (
                context_only_keys
                - {candidate["key"] for candidate in candidates}
            )
            and not duplicate_groups
            and precision >= 0.98
        )
        else "FAIL"
    )
    return {
        "schema_version": 1,
        "dataset_version": adjudication.get("dataset_version", ""),
        "status": status,
        "reviewer": adjudication.get("reviewer", ""),
        "review_method": adjudication.get("review_method", ""),
        "candidate_count": len(candidates),
        "expected_candidate_count": expected_count,
        "labeled_candidate_count": labeled,
        "true_positive_count": true_positive,
        "false_positive_count": labeled - true_positive,
        "precision": precision,
        "context_only_count": len(
            context_only_keys & {candidate["key"] for candidate in candidates}
        ),
        "context_only_unknown_keys": sorted(
            context_only_keys - {candidate["key"] for candidate in candidates}
        ),
        "duplicate_entity_group_count": len(duplicate_groups),
        "duplicate_entity_groups": duplicate_groups,
        "missing_labels": missing_labels,
        "unknown_labels": unknown_labels,
        "excluded_operating_subjects": excluded,
        "candidates": label_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bundle",
        type=Path,
        default=PROJECT_ROOT / "evaluation/semantic-v27/development-v2-bundle.jsonl",
    )
    parser.add_argument(
        "--adjudication",
        type=Path,
        default=PROJECT_ROOT
        / "evaluation/semantic-v27/development-v2-gold/entity_precision_adjudication.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(_load(args.bundle), _load(args.adjudication))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.buffer.write(rendered.encode("utf-8"))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
