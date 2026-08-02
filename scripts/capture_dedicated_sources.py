#!/usr/bin/env python3
"""Capture an explicit subset of dedicated sources into a shared state database."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ht_lead_radar.aggregate_adapters.coordinator import (
    DedicatedAggregateCoordinator,
    PublicHttpFetcher,
)
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.collectors import load_env_file
from ht_lead_radar.daily_topics import DEFAULT_DIRECTIONS
from ht_lead_radar.openclaw_llm import (
    OpenClawConfiguredLLMRunner,
    OpenClawLLMConfig,
    load_openclaw_llm_config,
)


def _llm_runner(args: argparse.Namespace) -> OpenClawConfiguredLLMRunner | None:
    if not args.use_openclaw_llm:
        return None
    active_env = dict(os.environ)
    if args.env_file:
        active_env.update(load_env_file(args.env_file))
    active_env["LEAD_RADAR_LLM_MODEL"] = args.model
    endpoint = str(active_env.get("MINIMAX_REASONING_BASE_URL") or "").rstrip("/")
    api_key = str(active_env.get("MINIMAX_API_KEY") or "").strip()
    provider, model = args.model.split("/", 1)
    if endpoint and api_key and provider.casefold() == "minimax":
        suffix = "/chat/completions"
        base_url = endpoint[: -len(suffix)] if endpoint.endswith(suffix) else endpoint
        config = OpenClawLLMConfig(
            provider=provider,
            model=model,
            base_url=base_url,
            api_kind="openai-completions",
            api_key=api_key,
        )
    else:
        config = load_openclaw_llm_config(
            env=active_env,
            config_path=args.openclaw_config,
            models_path=args.openclaw_models,
        )
    thinking_mode = (
        "disabled"
        if config.provider.casefold() == "minimax"
        and config.model.casefold() == "minimax-m3"
        else None
    )
    return OpenClawConfiguredLLMRunner(
        config=config,
        timeout_seconds=args.llm_timeout,
        max_completion_tokens=args.max_completion_tokens,
        thinking_mode=thinking_mode,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--source-id", action="append", required=True)
    parser.add_argument("--topic", default="|".join(DEFAULT_DIRECTIONS))
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument(
        "--full-visible-window",
        action="store_true",
        help=(
            "capture every item on each source's current finite listing page; "
            "intended for audited bootstrap/evaluation, not the daily overlap window"
        ),
    )
    parser.add_argument("--acceptance-dir", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--use-openclaw-llm", action="store_true")
    parser.add_argument("--model", default="minimax/MiniMax-M3")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--openclaw-config", type=Path)
    parser.add_argument("--openclaw-models", type=Path)
    parser.add_argument("--llm-timeout", type=float, default=180.0)
    parser.add_argument("--max-completion-tokens", type=int, default=4096)
    parser.add_argument("--semantic-workers", type=int)
    parser.add_argument("--strict-claims", action="store_true")
    parser.add_argument(
        "--claim-centric-v27",
        action="store_true",
        help="run the V27 Entity/Action/Claim adjudication path",
    )
    args = parser.parse_args()

    if "/" not in args.model:
        raise ValueError("--model must use provider/model format")

    registry = DedicatedAdapterRegistry.defaults()
    unknown = sorted(set(args.source_id) - registry.source_ids)
    if unknown:
        raise ValueError(f"unknown dedicated source ids: {unknown}")
    coordinator = DedicatedAggregateCoordinator(
        state_db=args.state_db,
        registry=registry,
        fetch=PublicHttpFetcher(timeout=args.timeout),
        llm_runner=_llm_runner(args),
        acceptance_dir=args.acceptance_dir,
        semantic_workers=args.semantic_workers,
        strict_claim_contract=args.strict_claims,
        claim_centric_v27=args.claim_centric_v27,
        capture_full_visible_window=args.full_visible_window,
    )
    results = []
    for source_id in dict.fromkeys(args.source_id):
        result = coordinator.collect_source(source_id, args.topic)
        results.append(
            {
                "source_id": source_id,
                "run": result.run.to_dict(),
                "evidence_count": len(result.evidence),
            }
        )
        print(
            json.dumps(
                {
                    "source_id": source_id,
                    "status": result.run.status,
                    "listing_count": result.run.listing_count,
                    "detail_success_count": result.run.detail_success_count,
                    "detail_failure_count": result.run.detail_failure_count,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    payload = {
        "schema_version": 1,
        "purpose": "targeted-dedicated-source-capture",
        "topic": args.topic,
        "full_visible_window": args.full_visible_window,
        "semantic_mode": "minimax" if args.use_openclaw_llm else "rules_only",
        "model": args.model if args.use_openclaw_llm else "rules-only",
        "strict_claim_contract": args.strict_claims,
        "claim_centric_v27": args.claim_centric_v27,
        "sources": results,
        "health": coordinator.health(),
    }
    args.output_summary.parent.mkdir(parents=True, exist_ok=True)
    args.output_summary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if all(item["run"]["status"] == "ok" for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
