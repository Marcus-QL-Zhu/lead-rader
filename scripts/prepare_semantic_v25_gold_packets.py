#!/usr/bin/env python3
"""Create blinded, hash-locked Semantic v25 Gold annotation packets."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import random
from typing import Any

from ht_lead_radar.aggregate_adapters.document_router import route_document
from ht_lead_radar.aggregate_adapters.models import CleanArticle, SourceArticleIndex
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _article(payload: dict[str, Any]) -> CleanArticle:
    return CleanArticle(
        index=SourceArticleIndex(**dict(payload["index"])),
        clean_body=str(payload.get("clean_body") or ""),
        author=str(payload.get("author") or ""),
        tags=tuple(payload.get("tags") or ()),
        structured_data=dict(payload.get("structured_data") or {}),
        extraction_method=str(payload.get("extraction_method") or "exact"),
        adaptive_similarity=payload.get("adaptive_similarity"),
        evidence_locators=dict(payload.get("evidence_locators") or {}),
        fetch_status=str(payload.get("fetch_status") or "ok"),
        failure_reason=str(payload.get("failure_reason") or ""),
        content_hash=str(payload.get("content_hash") or ""),
    )


def _packet_case(key: str, article: CleanArticle) -> dict[str, Any]:
    body = article.clean_body
    route = route_document(article)
    candidates = MiniMaxSemanticProcessor._event_candidates(body)
    return {
        "key": key,
        "article_sha256": sha256(body.encode("utf-8")).hexdigest(),
        "source_id": article.index.source_id,
        "canonical_url": article.index.canonical_url,
        "published_at": article.index.published_at,
        "title": article.index.title,
        "document_type": route.document_type,
        "clean_body": body,
        "document_units": [
            {
                "unit_id": unit.unit_id,
                "char_start": unit.char_start,
                "char_end": unit.char_end,
                "text": unit.text,
            }
            for unit in route.units
        ],
        "candidates": [
            {
                **candidate,
                "required_claim_ids": list(
                    MiniMaxSemanticProcessor._required_claim_ids(candidate)
                ),
            }
            for candidate in candidates
        ],
        "annotation": {
            "candidate_dispositions": [],
            "gold_events": [],
            "article_notes": "",
            "annotation_status": "unlabelled",
        },
    }


def build_packets(
    manifest_path: Path,
    bundle_path: Path,
    output_dir: Path,
    *,
    seed: int,
) -> dict[str, Any]:
    manifest = _read(manifest_path)
    bundle_bytes = bundle_path.read_bytes()
    bundle = json.loads(bundle_bytes)
    if manifest.get("status") != "frozen_unlabelled":
        raise ValueError("Gold packets require a frozen_unlabelled manifest")
    expected_bundle_sha = str(manifest.get("bundle_sha256") or "")
    if sha256(bundle_bytes).hexdigest() != expected_bundle_sha:
        raise ValueError("source bundle hash does not match manifest")
    formal_keys = {
        str(case["key"])
        for case in manifest.get("cases") or []
        if case.get("split") == "formal"
    }
    articles = {
        str(row["key"]): _article(dict(row["article"]))
        for row in bundle.get("articles") or []
        if row.get("split") == "formal"
    }
    if set(articles) != formal_keys:
        raise ValueError("formal manifest and bundle keys differ")
    base_cases = [_packet_case(key, articles[key]) for key in sorted(formal_keys)]
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    for offset, annotator in enumerate(("annotator-a", "annotator-b")):
        cases = list(base_cases)
        random.Random(seed + offset).shuffle(cases)
        payload = {
            "schema_version": 1,
            "dataset_version": manifest["dataset_version"],
            "annotation_role": "independent_primary",
            "annotator_id": annotator,
            "instructions": "docs/semantic-event-gold-labeling-guide.md",
            "source_manifest_sha256": sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
            "source_bundle_sha256": expected_bundle_sha,
            "cases": cases,
        }
        path = output_dir / f"{annotator}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        outputs[annotator] = str(path)
    adjudication = {
        "schema_version": 1,
        "dataset_version": manifest["dataset_version"],
        "annotation_role": "independent_adjudicator",
        "annotator_id": "adjudicator",
        "inputs": outputs,
        "cases": [],
        "status": "awaiting_primary_annotations",
    }
    adjudication_path = output_dir / "adjudication.json"
    adjudication_path.write_text(
        json.dumps(adjudication, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    outputs["adjudication"] = str(adjudication_path)
    return {"case_count": len(base_cases), "outputs": outputs}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260801)
    args = parser.parse_args()
    result = build_packets(
        args.manifest,
        args.bundle,
        args.output_dir,
        seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
