#!/usr/bin/env python3
"""Capture a fresh, rules-only source snapshot for unseen semantic acceptance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ht_lead_radar.daily_topics import DEFAULT_DIRECTIONS
from ht_lead_radar.source_pack_collector import SourcePackCollector
from ht_lead_radar.source_packs import SourcePack, SourcePackError, SourcePackRegistry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=Path("config/source-packs.json"))
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--topic", action="append", default=[])
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--limit-per-source", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--output-summary", type=Path, required=True)
    return parser


def _filtered_registry(path: Path, source_ids: list[str]) -> SourcePackRegistry:
    registry = SourcePackRegistry.load(path)
    if not source_ids:
        return registry
    requested = tuple(dict.fromkeys(item.strip() for item in source_ids if item.strip()))
    unknown = [source_id for source_id in requested if source_id not in {
        source.id for source in registry.sources
    }]
    if unknown:
        raise SourcePackError(f"unknown source ids: {', '.join(unknown)}")
    sources = tuple(registry.get_source(source_id) for source_id in requested)
    generic = registry.get_pack("generic-cn")
    pack = SourcePack(
        id=generic.id,
        name=f"{generic.name} (filtered)",
        aliases=generic.aliases,
        industry_tags=generic.industry_tags,
        source_ids=requested,
    )
    return SourcePackRegistry(
        version=registry.version,
        verified_on=registry.verified_on,
        policy=registry.policy,
        sources=sources,
        packs=(pack,),
    )


def main() -> int:
    args = _parser().parse_args()
    topics = args.topic or ["|".join(DEFAULT_DIRECTIONS)]
    registry = _filtered_registry(args.registry, args.source_id)
    summaries = []
    with SourcePackCollector(
        registry=registry,
        state_db=args.state_db,
        timeout=args.timeout,
        detail_fetch=True,
        dedicated_llm_runner=False,
    ) as collector:
        for topic in topics:
            evidence = collector.collect(
                topic,
                limit_per_query=args.limit_per_source,
            )
            summaries.append(
                {
                    "topic": topic,
                    "evidence_count": len(evidence),
                    "run": collector.last_run_summary,
                }
            )
    payload = {
        "schema_version": 1,
        "purpose": "post-freeze-rules-only-semantic-source-capture",
        "source_ids": [source.id for source in registry.sources],
        "topics": summaries,
    }
    args.output_summary.parent.mkdir(parents=True, exist_ok=True)
    args.output_summary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "topics": len(summaries),
                "evidence": sum(item["evidence_count"] for item in summaries),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
