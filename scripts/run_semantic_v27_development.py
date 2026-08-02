#!/usr/bin/env python3
"""Run V27 claim-centric extraction on an explicitly opened development Gold set."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from ht_lead_radar.aggregate_adapters.claim_adjudication import PROMPT_VERSION
from ht_lead_radar.aggregate_adapters.models import SourceChannel
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor
from scripts.run_semantic_v26_shadow import EVENT_TYPES, _clean_article, _runner


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FILES = (
    ROOT / "src" / "ht_lead_radar" / "aggregate_adapters" / "action_span_ledger.py",
    ROOT / "src" / "ht_lead_radar" / "aggregate_adapters" / "claim_adjudication.py",
    ROOT / "src" / "ht_lead_radar" / "aggregate_adapters" / "document_router.py",
    ROOT / "src" / "ht_lead_radar" / "aggregate_adapters" / "entity_ledger.py",
    ROOT / "src" / "ht_lead_radar" / "aggregate_adapters" / "entities.py",
    ROOT / "src" / "ht_lead_radar" / "aggregate_adapters" / "models.py",
    ROOT / "src" / "ht_lead_radar" / "aggregate_adapters" / "semantic.py",
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
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


@contextmanager
def _output_lock(output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_suffix(f"{output.suffix}.lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
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


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    audits = [result["audit"] for result in results]
    return {
        "article_count": len(results),
        "event_count": sum(len(result.get("events") or []) for result in results),
        "claim_count": sum(int(audit.get("candidate_count", 0)) for audit in audits),
        "accepted_claim_count": sum(
            len(audit.get("accepted_claim_ids") or []) for audit in audits
        ),
        "rejected_claim_count": sum(
            len(audit.get("rejected_claim_ids") or []) for audit in audits
        ),
        "failed_claim_count": sum(
            len(audit.get("failed_claim_ids") or []) for audit in audits
        ),
        "strict_ready_article_count": sum(
            bool(audit.get("strict_claim_contract_ready")) for audit in audits
        ),
        "partial_article_count": sum(
            audit.get("status") == "partial" for audit in audits
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    bundle = _load(args.bundle)
    gold = _load(args.gold)
    if gold.get("status") != "complete":
        raise ValueError("development runner requires completed adjudicated Gold")
    if "adjudicat" not in str(gold.get("annotation_role") or ""):
        raise ValueError("development runner requires the third-party Gold packet")
    articles = {str(row["key"]): row for row in bundle.get("articles") or []}
    keys = [str(case["key"]) for case in gold.get("cases") or []]
    if not keys or any(key not in articles for key in keys):
        raise ValueError("Gold and bundle keys differ")
    if args.key:
        unknown = sorted(set(args.key) - set(keys))
        if unknown:
            raise ValueError(f"unknown key(s):{unknown}")
        requested = set(args.key)
        keys = [key for key in keys if key in requested]

    purpose = str(
        getattr(
            args,
            "purpose",
            "opened-formal-v1-development-error-set-only",
        )
    )
    bundle_sha256 = sha256(args.bundle.read_bytes()).hexdigest()
    gold_sha256 = sha256(args.gold.read_bytes()).hexdigest()
    prior = _load(args.output) if args.output.exists() else {}
    if prior and (
        prior.get("purpose") != purpose
        or prior.get("source_bundle_sha256") != bundle_sha256
        or prior.get("source_gold_sha256") != gold_sha256
    ):
        if not args.force:
            raise ValueError("development output belongs to a different frozen input")
        prior = {}
    if prior and prior.get("code_contract_sha256") != _contract_sha256():
        if not args.force:
            raise ValueError("V27 contract changed; use a new output or --force")
        prior = {}
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
    for position, key in enumerate(keys, start=1):
        if key in result_by_key and not args.force:
            continue
        article = _clean_article(dict(articles[key]["article"]))
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
        results = [result_by_key[value] for value in keys if value in result_by_key]
        payload = {
            "schema_version": 1,
            "dataset_version": str(gold.get("dataset_version") or ""),
            "purpose": purpose,
            "status": "running",
            "model": "minimax/MiniMax-M3",
            "prompt_version": PROMPT_VERSION,
            "code_contract_sha256": _contract_sha256(),
            "source_bundle_sha256": bundle_sha256,
            "source_gold_sha256": gold_sha256,
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
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--openclaw-config", type=Path)
    parser.add_argument("--openclaw-models", type=Path)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument(
        "--purpose",
        choices=(
            "opened-formal-v1-development-error-set-only",
            "opened-semantic-v27-development-v2",
        ),
        default="opened-formal-v1-development-error-set-only",
    )
    parser.add_argument("--key", action="append")
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
