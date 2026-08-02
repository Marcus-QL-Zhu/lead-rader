#!/usr/bin/env python3
"""Run one isolated V27 prompt-loop variant or a frozen holdout case."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from ht_lead_radar.aggregate_adapters.models import SourceChannel
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor
from scripts.run_semantic_v26_shadow import EVENT_TYPES, _clean_article, _runner
from scripts.run_semantic_v27_development import (
    CONTRACT_FILES,
    _output_lock,
    _summary,
)
from scripts.semantic_v27_prompt_variants import MAX_PROMPT_ROUNDS, build_variant


ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object:{path}")
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _contract_sha256() -> str:
    digest = sha256()
    for path in CONTRACT_FILES:
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _keys(args: argparse.Namespace, split: dict[str, Any]) -> list[str]:
    if args.key:
        return list(dict.fromkeys(args.key))
    for row in split.get("rounds") or []:
        if int(row["round"]) == args.round:
            return [str(value) for value in row["training_keys"]]
    raise ValueError(f"round not found in split:{args.round}")


def _prompt_config(args: argparse.Namespace) -> dict[str, Any]:
    if args.resolved_config:
        config = _load(args.resolved_config)
        return dict(config.get("prompt_config") or config)
    parent = _load(args.parent_config) if args.parent_config else None
    if parent and "prompt_config" in parent:
        parent = dict(parent["prompt_config"])
    return build_variant(
        round_number=args.round,
        variant=args.variant,
        parent=parent,
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    bundle = _load(args.bundle)
    gold = _load(args.gold)
    split = _load(args.split)
    keys = _keys(args, split)
    article_by_key = {
        str(row["key"]): row for row in bundle.get("articles") or []
    }
    gold_keys = {str(case["key"]) for case in gold.get("cases") or []}
    missing = sorted(set(keys) - set(article_by_key))
    if missing or not set(keys) <= gold_keys:
        raise ValueError(f"selected keys are not frozen in bundle/Gold:{missing}")
    prompt_config = _prompt_config(args)
    purpose = (
        "opened-semantic-v27-prompt-loop-holdout"
        if args.key
        else "opened-semantic-v27-prompt-loop-training"
    )
    runner = _runner(
        args.env_file,
        args.timeout,
        openclaw_config=args.openclaw_config,
        openclaw_models=args.openclaw_models,
    )
    results: list[dict[str, Any]] = []
    for position, key in enumerate(keys, start=1):
        article = _clean_article(dict(article_by_key[key]["article"]))
        processor = MiniMaxSemanticProcessor(
            runner,
            strict_claim_contract=True,
            claim_centric_v27=True,
            claim_prompt_config={
                key: prompt_config[key]
                for key in (
                    "system_prompt",
                    "few_shot",
                    "prompt_version",
                    "contract_version",
                )
            },
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
        results.append(
            {
                "key": key,
                "position": position,
                "events": [event.to_dict() for event in events],
                "audit": processor.last_audit,
            }
        )
        print(
            json.dumps(
                {
                    "position": position,
                    "key": key,
                    "status": processor.last_audit.get("status"),
                    "events": len(events),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    summary = _summary(results)
    has_infrastructure_failure = any(
        row.get("audit", {}).get("infrastructure_errors")
        for row in results
    )
    payload = {
        "schema_version": 1,
        "dataset_version": str(gold.get("dataset_version") or ""),
        "purpose": purpose,
        "status": (
            "infrastructure_failed"
            if has_infrastructure_failure
            else "failed"
            if summary["failed_claim_count"]
            else "complete"
        ),
        "model": "minimax/MiniMax-M3",
        "round": args.round,
        "maximum_rounds": MAX_PROMPT_ROUNDS,
        "variant": args.variant,
        "selected_keys": keys,
        "prompt_config": prompt_config,
        "prompt_config_sha256": sha256(
            json.dumps(
                prompt_config,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "code_contract_sha256": _contract_sha256(),
        "source_bundle_sha256": sha256(args.bundle.read_bytes()).hexdigest(),
        "source_gold_sha256": sha256(args.gold.read_bytes()).hexdigest(),
        "source_split_sha256": sha256(args.split.read_bytes()).hexdigest(),
        "results": results,
        "summary": summary,
    }
    _write(args.output, payload)
    if args.save_config:
        _write(args.save_config, {"prompt_config": prompt_config})
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--openclaw-config", type=Path)
    parser.add_argument("--openclaw-models", type=Path)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument(
        "--round",
        type=int,
        choices=range(1, MAX_PROMPT_ROUNDS + 1),
        required=True,
    )
    parser.add_argument("--variant", choices=("a", "b", "c"), required=True)
    parser.add_argument("--parent-config", type=Path)
    parser.add_argument("--resolved-config", type=Path)
    parser.add_argument("--save-config", type=Path)
    parser.add_argument("--key", action="append")
    return parser


def main() -> int:
    args = _parser().parse_args()
    with _output_lock(args.output):
        result = run(args)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0 if result["summary"]["failed_claim_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
