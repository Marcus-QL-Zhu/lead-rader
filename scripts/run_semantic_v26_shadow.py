#!/usr/bin/env python3
"""Run the v26 Claim/Span shadow contract on diagnostic calibration articles."""

from __future__ import annotations

import argparse
from dataclasses import fields
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.semantic import MiniMaxSemanticProcessor
from ht_lead_radar.collectors import load_env_file
from ht_lead_radar.openclaw_llm import (
    OpenClawConfiguredLLMRunner,
    OpenClawLLMConfig,
    load_openclaw_llm_config,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    ROOT / "evaluation" / "semantic-v25" / "shadow-diagnostic-manifest.json"
)
DEFAULT_BUNDLE = (
    ROOT / ".acceptance" / "semantic-v25" / "shadow-source-bundle.json"
)
DEFAULT_DATABASE = ROOT / ".acceptance" / "server-v23-live.sqlite"
DEFAULT_OUTPUT = ROOT / ".acceptance" / "semantic-v25" / "v26-shadow-run.json"
DEFAULT_ENV = ROOT.parent / "personal development app" / ".env"
EVENT_TYPES = (
    "funding",
    "executive_change",
    "factory_or_capacity",
    "major_order",
    "partnership",
    "technical_milestone",
    "new_site_or_entity",
    "regulatory_or_clinical",
    "policy_or_standard",
    "procurement_tender",
    "customer_validation",
    "merger_acquisition",
    "ipo_or_listing",
    "enterprise_system",
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _runner(
    env_file: Path,
    timeout: float,
    *,
    openclaw_config: Path | None = None,
    openclaw_models: Path | None = None,
) -> OpenClawConfiguredLLMRunner:
    try:
        env = load_env_file(env_file)
    except FileNotFoundError:
        env = {}
    endpoint = str(env.get("MINIMAX_REASONING_BASE_URL") or "").rstrip("/")
    api_key = str(env.get("MINIMAX_API_KEY") or "").strip()
    if endpoint and api_key:
        suffix = "/chat/completions"
        base_url = endpoint[: -len(suffix)] if endpoint.endswith(suffix) else endpoint
        config = OpenClawLLMConfig(
            provider="minimax",
            model="MiniMax-M3",
            base_url=base_url,
            api_kind="openai-completions",
            api_key=api_key,
        )
    else:
        active_env = dict(os.environ)
        active_env["LEAD_RADAR_LLM_MODEL"] = "minimax/MiniMax-M3"
        config = load_openclaw_llm_config(
            env=active_env,
            config_path=openclaw_config,
            models_path=openclaw_models,
        )
    return OpenClawConfiguredLLMRunner(
        config=config,
        timeout_seconds=timeout,
        max_completion_tokens=4096,
        thinking_mode="disabled",
    )


def _clean_article(payload: dict[str, Any]) -> CleanArticle:
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


def _semantic_event(payload: dict[str, Any]) -> SemanticEvent:
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


def _rules(
    connection: sqlite3.Connection,
    source_id: str,
    article_id: str,
) -> list[SemanticEvent]:
    rows = connection.execute(
        """
        SELECT event_json FROM aggregate_semantic_events
        WHERE source_id=? AND source_article_id=? AND processor LIKE 'rules%'
        ORDER BY event_key
        """,
        (source_id, article_id),
    ).fetchall()
    return [_semantic_event(json.loads(str(row[0]))) for row in rows]


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    audits = [result["audit"] for result in results]
    return {
        "article_count": len(results),
        "raw_model_event_count": sum(
            int(audit.get("raw_model_event_count", 0)) for audit in audits
        ),
        "cited_model_event_count": sum(
            int(audit.get("cited_model_event_count", 0)) for audit in audits
        ),
        "uncited_model_event_count": sum(
            int(audit.get("uncited_model_event_count", 0)) for audit in audits
        ),
        "bad_claim_pair_event_count": sum(
            int(audit.get("bad_claim_pair_event_count", 0)) for audit in audits
        ),
        "strict_ready_article_count": sum(
            bool(audit.get("strict_claim_contract_ready")) for audit in audits
        ),
        "fallback_article_count": sum(
            audit.get("status") == "fallback_to_rules" for audit in audits
        ),
        "partial_article_count": sum(
            "partial" in str(audit.get("status") or "") for audit in audits
        ),
        "unmapped_candidate_count": sum(
            int(audit.get("unmapped_candidate_count", 0)) for audit in audits
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _read_json(args.manifest)
    if manifest.get("status") != "diagnostic_not_final":
        raise ValueError("shadow runner requires the diagnostic manifest")
    bundle = _read_json(args.bundle)
    articles = {
        str(item["key"]): item
        for item in bundle.get("articles") or []
        if isinstance(item, dict) and item.get("split") == "calibration"
    }
    keys = [
        str(case["key"])
        for case in manifest.get("cases") or []
        if case.get("split") == "calibration"
    ]
    if set(keys) != set(articles):
        raise ValueError("manifest and source bundle calibration keys differ")
    if args.key:
        unknown = sorted(set(args.key) - set(keys))
        if unknown:
            raise ValueError(f"unknown calibration key(s): {unknown}")
        selected = set(args.key)
        keys = [key for key in keys if key in selected]
    prior: dict[str, Any] = {}
    if args.output.exists():
        prior = _read_json(args.output)
    result_by_key = {
        str(item["key"]): item
        for item in prior.get("results") or []
        if isinstance(item, dict) and item.get("key")
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
            if key in result_by_key and not args.force:
                continue
            raw = articles[key]
            article = _clean_article(dict(raw["article"]))
            source_id, article_id = key.split(":", 1)
            processor = MiniMaxSemanticProcessor(
                runner,
                strict_claim_contract=True,
            )
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
                "purpose": "claim-contract-shadow-only",
                "model": "minimax/MiniMax-M3",
                "results": results,
                "summary": _summary(results),
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
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
    finally:
        connection.close()
    return _read_json(args.output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--openclaw-config", type=Path)
    parser.add_argument("--openclaw-models", type=Path)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument(
        "--key",
        action="append",
        help="run only one calibration article key; may be repeated",
    )
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    result = run(_parser().parse_args())
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
