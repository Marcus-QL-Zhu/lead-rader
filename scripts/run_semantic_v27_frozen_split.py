#!/usr/bin/env python3
"""Run a frozen V27 split once without reading Gold labels."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from ht_lead_radar.aggregate_adapters.claim_adjudication import PROMPT_VERSION
from ht_lead_radar.aggregate_adapters.models import SourceChannel
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor
from scripts.run_semantic_v26_shadow import EVENT_TYPES, _clean_article, _runner
from scripts.run_semantic_v27_development import (
    _contract_sha256,
    _load,
    _output_lock,
    _summary,
    _write,
)


ALLOWED_PURPOSES = frozenset(
    {
        "reserve-v1-one-time-prevalidation",
        "final-v2-one-time-acceptance",
    }
)


def _selection_sha256(rows: list[dict[str, Any]]) -> str:
    keys = [str(row["key"]) for row in rows]
    return sha256(
        json.dumps(keys, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.purpose not in ALLOWED_PURPOSES:
        raise ValueError(f"unsupported frozen purpose:{args.purpose}")
    bundle = _load(args.bundle)
    rows = [
        dict(row)
        for row in bundle.get("articles") or []
        if str(row.get("split") or "") == args.split
    ]
    if len(rows) != args.expected_count:
        raise ValueError(
            f"frozen split count differs:expected={args.expected_count},actual={len(rows)}"
        )
    bundle_sha = sha256(args.bundle.read_bytes()).hexdigest()
    selection_sha = _selection_sha256(rows)
    contract_sha = _contract_sha256()
    prior = _load(args.output) if args.output.exists() else {}
    if prior:
        immutable = {
            "purpose": args.purpose,
            "dataset_version": args.dataset_version,
            "split": args.split,
            "code_contract_sha256": contract_sha,
            "source_bundle_sha256": bundle_sha,
            "selection_sha256": selection_sha,
        }
        for field, expected in immutable.items():
            if prior.get(field) != expected:
                raise ValueError(f"frozen output mismatch:{field}")
        if prior.get("status") == "complete":
            return prior

    result_by_key = {
        str(row["key"]): row
        for row in prior.get("results") or []
        if isinstance(row, dict) and row.get("key")
    }
    runner = _runner(
        args.env_file,
        args.timeout,
        openclaw_config=args.openclaw_config,
        openclaw_models=args.openclaw_models,
    )
    for position, row in enumerate(rows, start=1):
        key = str(row["key"])
        if key in result_by_key:
            continue
        article = _clean_article(dict(row["article"]))
        processor = MiniMaxSemanticProcessor(
            runner,
            strict_claim_contract=True,
            claim_centric_v27=True,
        )
        events = processor.process(
            SourceChannel(
                source_id=article.index.source_id,
                name=article.index.source_id,
                url=article.index.listing_page,
                source_grade="B",
                event_prior=EVENT_TYPES,
                allowed_hosts=(),
            ),
            article,
            [],
        )
        result_by_key[key] = {
            "key": key,
            "position": position,
            "events": [event.to_dict() for event in events],
            "audit": processor.last_audit,
        }
        results = [
            result_by_key[str(value["key"])]
            for value in rows
            if str(value["key"]) in result_by_key
        ]
        payload = {
            "schema_version": 1,
            "dataset_version": args.dataset_version,
            "purpose": args.purpose,
            "split": args.split,
            "status": "running",
            "model": "minimax/MiniMax-M3",
            "prompt_version": PROMPT_VERSION,
            "code_contract_sha256": contract_sha,
            "source_bundle_sha256": bundle_sha,
            "selection_sha256": selection_sha,
            "results": results,
            "summary": _summary(results),
        }
        _write(args.output, payload)
        print(
            json.dumps(
                {
                    "position": position,
                    "key": key,
                    "status": processor.last_audit.get("status"),
                    "strict_ready": processor.last_audit.get(
                        "strict_claim_contract_ready"
                    ),
                    "events": len(events),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    payload = _load(args.output)
    payload["status"] = "complete"
    payload["summary"] = _summary(list(payload.get("results") or []))
    _write(args.output, payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--expected-count", type=int, required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--purpose", choices=sorted(ALLOWED_PURPOSES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--openclaw-config", type=Path)
    parser.add_argument("--openclaw-models", type=Path)
    parser.add_argument("--timeout", type=float, default=240.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    with _output_lock(args.output):
        result = run(args)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
