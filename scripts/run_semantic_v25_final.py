#!/usr/bin/env python3
"""Run the frozen Semantic v25 cohort once, with crash-safe resume."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from ht_lead_radar.aggregate_adapters.models import SourceChannel
from ht_lead_radar.aggregate_adapters.semantic import (
    MiniMaxSemanticProcessor,
    PROMPT_VERSION,
)
from scripts.run_semantic_v26_shadow import (
    EVENT_TYPES,
    _clean_article,
    _read_json,
    _rules,
    _runner,
    _summary,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FILES = (
    ROOT / "src" / "ht_lead_radar" / "aggregate_adapters" / "document_router.py",
    ROOT / "src" / "ht_lead_radar" / "aggregate_adapters" / "models.py",
    ROOT / "src" / "ht_lead_radar" / "aggregate_adapters" / "semantic.py",
)


def _contract_sha256() -> str:
    digest = sha256()
    for path in CONTRACT_FILES:
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@contextmanager
def _output_lock(output: Path):
    """Prevent detached or concurrent resume processes from sharing one output."""

    lock = output.with_suffix(f"{output.suffix}.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(
            f"prediction output is already locked: {lock}; verify no runner is "
            "active before removing a stale lock"
        ) from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        lock.unlink(missing_ok=True)


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    bundle_bytes = args.bundle.read_bytes()
    bundle = json.loads(bundle_bytes)
    if manifest.get("status") != "frozen_unlabelled":
        raise ValueError("final runner requires a frozen_unlabelled manifest")
    if sha256(bundle_bytes).hexdigest() != manifest.get("bundle_sha256"):
        raise ValueError("bundle hash does not match frozen manifest")
    keys = [
        str(case["key"])
        for case in manifest.get("cases") or []
        if case.get("split") == args.split
    ]
    articles = {
        str(row["key"]): row
        for row in bundle.get("articles") or []
        if row.get("split") == args.split
    }
    if set(keys) != set(articles):
        raise ValueError("selected manifest and bundle keys differ")

    prior: dict[str, Any] = {}
    if args.output.exists():
        prior = _read_json(args.output)
        if prior.get("status") == "complete" and not args.force:
            raise ValueError("completed frozen prediction already exists")
        if args.split == "formal" and args.force:
            raise ValueError("formal prediction cannot be force-overwritten")
        if prior.get("manifest_sha256") != sha256(manifest_bytes).hexdigest():
            raise ValueError("existing prediction belongs to another manifest")
        if prior.get("code_contract_sha256") != _contract_sha256():
            raise ValueError("cannot resume after semantic contract code changed")
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
    connection = sqlite3.connect(args.database)
    try:
        for position, key in enumerate(keys, start=1):
            if key in result_by_key:
                continue
            raw = articles[key]
            article = _clean_article(dict(raw["article"]))
            source_id, article_id = key.split(":", 1)
            processor = MiniMaxSemanticProcessor(runner, strict_claim_contract=True)
            events = processor.process(
                SourceChannel(
                    source_id=source_id,
                    name=source_id,
                    url=article.index.listing_page,
                    source_grade="B",
                    event_prior=EVENT_TYPES,
                    allowed_hosts=(),
                ),
                article,
                _rules(connection, source_id, article_id),
            )
            result_by_key[key] = {
                "key": key,
                "position": position,
                "events": [event.to_dict() for event in events],
                "audit": processor.last_audit,
            }
            results = [result_by_key[value] for value in keys if value in result_by_key]
            payload = {
                "schema_version": 1,
                "dataset_version": manifest["dataset_version"],
                "split": args.split,
                "status": "running",
                "model": "minimax/MiniMax-M3",
                "prompt_version": PROMPT_VERSION,
                "manifest_sha256": sha256(manifest_bytes).hexdigest(),
                "source_bundle_sha256": sha256(bundle_bytes).hexdigest(),
                "code_contract_sha256": _contract_sha256(),
                "results": results,
                "summary": _summary(results),
            }
            _write(args.output, payload)
            print(
                json.dumps(
                    {
                        "position": position,
                        "key": key,
                        "strict_ready": processor.last_audit.get(
                            "strict_claim_contract_ready"
                        ),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        connection.close()
    payload = _read_json(args.output)
    payload["status"] = "complete"
    payload["summary"] = _summary(list(payload.get("results") or []))
    _write(args.output, payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--openclaw-config", type=Path)
    parser.add_argument("--openclaw-models", type=Path)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--split", choices=("formal", "reserve"), default="formal")
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    with _output_lock(args.output):
        result = run(args)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
