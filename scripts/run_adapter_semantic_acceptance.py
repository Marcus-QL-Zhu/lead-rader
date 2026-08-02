#!/usr/bin/env python3
"""Replay archived dedicated-source articles through strict MiniMax semantics."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import fields
import json
import os
from pathlib import Path
from typing import Any, Iterator

from ht_lead_radar.aggregate_adapters.models import CleanArticle, SemanticEvent
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor
from scripts.run_semantic_v26_shadow import _clean_article, _runner
from scripts.run_semantic_v27_development import _summary


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _event(payload: dict[str, Any]) -> SemanticEvent:
    allowed = {field.name for field in fields(SemanticEvent)}
    values = {key: value for key, value in payload.items() if key in allowed}
    for key in (
        "company_mentions",
        "industry_tags",
        "investors",
        "evidence_quotes",
        "ambiguities",
        "claim_ids",
        "span_ids",
    ):
        values[key] = tuple(values.get(key) or ())
    return SemanticEvent(**values)


def load_archive(
    archive_root: Path,
    source_ids: list[str],
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for source_id in source_ids:
        source_dir = archive_root / source_id
        if not source_dir.is_dir():
            raise ValueError(f"archive source directory is missing: {source_id}")
        for path in sorted(source_dir.glob("semantic-*.json")):
            payload = _read(path)
            article_payload = payload.get("article")
            if not isinstance(article_payload, dict):
                raise ValueError(f"archive has no article payload: {path}")
            article = _clean_article(article_payload)
            key = f"{article.index.source_id}:{article.index.source_article_id}"
            if article.index.source_id != source_id:
                raise ValueError(f"archive source mismatch: {path}")
            incumbent = records.get(key)
            if incumbent is not None:
                prior: CleanArticle = incumbent["article"]
                if prior.content_hash != article.content_hash:
                    raise ValueError(f"archive duplicate content mismatch: {key}")
                incumbent["archive_files"].append(str(path))
                continue
            records[key] = {
                "key": key,
                "article": article,
                "rule_events": [
                    _event(dict(item))
                    for item in payload.get("events") or []
                    if isinstance(item, dict)
                ],
                "archive_files": [str(path)],
            }
    return records


@contextmanager
def _output_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def _write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    registry = DedicatedAdapterRegistry.defaults()
    unknown = sorted(set(args.source_id) - registry.source_ids)
    if unknown:
        raise ValueError(f"unknown dedicated source ids: {unknown}")
    records = load_archive(args.archive_root, list(dict.fromkeys(args.source_id)))
    keys: list[str] = []
    for source_id in dict.fromkeys(args.source_id):
        source_keys = sorted(
            (key for key in records if key.startswith(f"{source_id}:")),
            key=lambda key: (
                records[key]["article"].index.published_at,
                key,
            ),
            reverse=True,
        )
        if args.limit_per_source:
            source_keys = source_keys[: args.limit_per_source]
        keys.extend(source_keys)
    if args.key:
        requested = set(args.key)
        unknown_keys = sorted(requested - set(keys))
        if unknown_keys:
            raise ValueError(f"unknown or excluded archive keys: {unknown_keys}")
        keys = [key for key in keys if key in requested]

    prior = _read(args.output) if args.output.exists() else {}
    result_by_key = {
        str(item["key"]): item
        for item in prior.get("results") or []
        if isinstance(item, dict) and item.get("key") in keys
    }
    runner = _runner(
        args.env_file,
        args.timeout,
        openclaw_config=args.openclaw_config,
        openclaw_models=args.openclaw_models,
    )
    with _output_lock(args.output):
        for position, key in enumerate(keys, start=1):
            if key in result_by_key and not args.force:
                continue
            record = records[key]
            article: CleanArticle = record["article"]
            channel = registry.for_source(article.index.source_id).channel_for(
                article.index.source_id
            )
            processor = MiniMaxSemanticProcessor(
                runner,
                strict_claim_contract=True,
                claim_centric_v27=True,
            )
            events = processor.process(channel, article, record["rule_events"])
            result_by_key[key] = {
                "key": key,
                "position": position,
                "source_id": article.index.source_id,
                "source_article_id": article.index.source_article_id,
                "title": article.index.title,
                "canonical_url": article.index.canonical_url,
                "published_at": article.index.published_at,
                "article_content_hash": article.content_hash,
                "input_archive_files": record["archive_files"],
                "events": [event.to_dict() for event in events],
                "audit": processor.last_audit,
            }
            results = [result_by_key[value] for value in keys if value in result_by_key]
            payload = {
                "schema_version": 1,
                "purpose": "dedicated-adapter-semantic-v27-acceptance",
                "model": f"{runner.config.provider}/{runner.config.model}",
                "strict_claim_contract": True,
                "claim_centric_v27": True,
                "archive_root": str(args.archive_root),
                "source_ids": list(dict.fromkeys(args.source_id)),
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
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return _read(args.output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--source-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--openclaw-config", type=Path)
    parser.add_argument("--openclaw-models", type=Path)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--limit-per-source", type=int)
    parser.add_argument("--key", action="append")
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    result = run(_parser().parse_args())
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0 if result["summary"]["failed_claim_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
