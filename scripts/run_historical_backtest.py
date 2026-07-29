#!/usr/bin/env python3
"""Run leakage-safe historical Lead Radar prediction and validation."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from ht_lead_radar.backtest import (
    BacktestConfig,
    load_evidence,
    load_jobs,
    run_historical_predictions,
    validate_predictions,
    write_frozen_snapshot,
)
from ht_lead_radar.openclaw_llm import (
    LLMConfigurationError,
    OpenClawConfiguredLLMRunner,
    OpenClawLLMConfig,
)
from ht_lead_radar.historical_label_quality import verify_historical_job_labels


def _read_env_file(path: str | Path) -> dict[str, str]:
    """Read a simple dotenv file without mutating process-wide environment."""

    values: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _build_runner(env_file: str | None) -> OpenClawConfiguredLLMRunner:
    if not env_file:
        return OpenClawConfiguredLLMRunner()
    values = {**os.environ, **_read_env_file(env_file)}
    api_key = values.get("MINIMAX_API_KEY", "").strip()
    base_url = values.get("MINIMAX_REASONING_BASE_URL", "").strip().rstrip("/")
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")].rstrip("/")
    model = values.get("MINIMAX_REASONING_MODEL", "").strip()
    if "/" in model:
        provider, model = model.split("/", 1)
    else:
        provider = "minimax"
    if not api_key or not base_url or not model:
        raise LLMConfigurationError(
            "env file must provide MINIMAX_API_KEY, "
            "MINIMAX_REASONING_BASE_URL and MINIMAX_REASONING_MODEL"
        )
    return OpenClawConfiguredLLMRunner(
        config=OpenClawLLMConfig(
            provider=provider,
            model=model,
            base_url=base_url,
            api_kind="openai-completions",
            api_key=api_key,
        )
    )


def _verify_uniform_label_audit(
    path: str | Path,
    candidates: list[str],
    *,
    window_start: str,
    window_end_exclusive: str,
    eligible_job_companies: set[str],
) -> bool:
    value: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("search_protocol_version") != (
        "uniform-director-plus-v1"
    ):
        raise ValueError("label audit must use uniform-director-plus-v1")
    audits = value.get("audits")
    if not isinstance(audits, list):
        raise ValueError("label audit must contain an audits list")
    by_company = {
        str(item.get("company") or ""): item
        for item in audits
        if isinstance(item, dict)
    }
    if set(by_company) != set(candidates) or len(audits) != len(candidates):
        raise ValueError("label audit must contain exactly one record per candidate")
    required_channels = {"official_careers", "public_web_search"}
    allowed_results = {"matched", "no_eligible_job"}

    def parse_timestamp(raw: Any, *, field: str, company: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"label audit {field} invalid: {company}") from error
        if parsed.tzinfo is None:
            raise ValueError(f"label audit {field} lacks timezone: {company}")
        return parsed

    for company in candidates:
        item = by_company[company]
        if item.get("window_start") != window_start or item.get(
            "window_end_exclusive"
        ) != window_end_exclusive:
            raise ValueError(f"label audit window mismatch: {company}")
        searches = item.get("searches")
        if not isinstance(searches, list):
            raise ValueError(f"label audit search protocol incomplete: {company}")
        by_channel = {
            str(search.get("channel") or ""): search
            for search in searches
            if isinstance(search, dict)
        }
        if set(by_channel) != required_channels or len(searches) != len(
            required_channels
        ):
            raise ValueError(f"label audit search protocol incomplete: {company}")
        for channel, search in by_channel.items():
            query = str(search.get("query") or "").strip()
            if company.casefold() not in query.casefold():
                raise ValueError(
                    f"label audit query lacks candidate for {channel}: {company}"
                )
            parse_timestamp(
                search.get("executed_at"),
                field=f"{channel}.executed_at",
                company=company,
            )
            if not str(search.get("outcome_summary") or "").strip():
                raise ValueError(
                    f"label audit outcome missing for {channel}: {company}"
                )
            artifact_path = str(search.get("artifact_path") or "").strip()
            artifact_sha = str(search.get("artifact_sha256") or "").strip()
            result_urls = search.get("result_urls")
            if not artifact_path or not artifact_sha or not isinstance(result_urls, list):
                raise ValueError(
                    f"label audit lacks replayable artifact for {channel}: {company}"
                )
            artifact = Path(path).resolve().parents[2] / artifact_path
            if not artifact.is_file():
                raise ValueError(
                    f"label audit artifact missing for {channel}: {company}"
                )
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != artifact_sha:
                raise ValueError(
                    f"label audit artifact hash mismatch for {channel}: {company}"
                )
        if item.get("result") not in allowed_results:
            raise ValueError(f"label audit result missing: {company}")
        parse_timestamp(item.get("searched_at"), field="searched_at", company=company)
        expected_result = (
            "matched" if company in eligible_job_companies else "no_eligible_job"
        )
        if item.get("result") != expected_result:
            raise ValueError(f"label audit result/jobs mismatch: {company}")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Use only pre-cutoff non-recruiting evidence to predict Director+ "
            "roles, then validate against jobs published in the next three months."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    predict = subparsers.add_parser("predict")
    predict.add_argument("--evidence-json", required=True)
    predict.add_argument("--cutoff", required=True)
    predict.add_argument("--output", required=True)
    predict.add_argument("--horizon-months", type=int, default=3)
    predict.add_argument(
        "--max-roles-per-company",
        type=int,
        default=5,
        choices=range(1, 6),
    )
    predict.add_argument(
        "--prompt-version",
        default="historical-demand-v8-anonymized",
    )
    predict.add_argument(
        "--experiment-id",
        default="",
        help="Unique iteration id used to isolate provider request sessions.",
    )
    predict.add_argument(
        "--env-file",
        help=(
            "Optional local dotenv containing MiniMax reasoning credentials. "
            "Values are loaded in memory and are never written to snapshots."
        ),
    )
    predict.add_argument(
        "--include-workforce-precursors",
        action="store_true",
        help=(
            "Include manager/expert/engineer job clusters. The acceptance "
            "backtest intentionally leaves this disabled."
        ),
    )

    validate = subparsers.add_parser("validate")
    validate.add_argument("--snapshot", required=True)
    validate.add_argument("--jobs-json", required=True)
    validate.add_argument(
        "--label-audit",
        help="Structured uniform-search JSON audit bundle.",
    )
    validate.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "predict":
        config = BacktestConfig(
            cutoff=date.fromisoformat(args.cutoff),
            horizon_months=args.horizon_months,
            include_workforce_precursors=args.include_workforce_precursors,
            max_roles_per_company=args.max_roles_per_company,
            prompt_version=args.prompt_version,
            experiment_id=args.experiment_id,
        )
        snapshot = run_historical_predictions(
            load_evidence(args.evidence_json),
            config,
            _build_runner(args.env_file),
        )
        path = write_frozen_snapshot(snapshot, args.output)
        print(path.resolve())
        return 0

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    report = validate_predictions(snapshot, load_jobs(args.jobs_json))
    job_bundle = json.loads(Path(args.jobs_json).read_text(encoding="utf-8"))
    label_quality_verified = False
    if job_bundle.get("label_quality_protocol"):
        try:
            label_quality_verified = verify_historical_job_labels(
                job_bundle,
                window_start=report["manifest"]["validation_start"],
                window_end_exclusive=report["manifest"]["validation_end_exclusive"],
                artifact_root=Path(args.jobs_json).resolve().parents[2],
            )
        except (OSError, ValueError):
            label_quality_verified = False
    audit_verified = False
    if args.label_audit:
        try:
            audit_verified = _verify_uniform_label_audit(
                args.label_audit,
                list(report["manifest"].get("candidate_companies") or ()),
                window_start=report["manifest"]["validation_start"],
                window_end_exclusive=report["manifest"]["validation_end_exclusive"],
                eligible_job_companies={
                    str(job.get("company") or "")
                    for job in report.get("validation_jobs") or ()
                },
            )
        except (OSError, ValueError):
            audit_verified = False
    report["manifest"]["uniform_label_search_verified"] = audit_verified
    report["manifest"]["label_quality_verified"] = label_quality_verified
    report["manifest"]["label_quality_protocol"] = str(
        job_bundle.get("label_quality_protocol") or ""
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(target.resolve())
    counts = report["counts"]
    print(
        "预测 {predictions} 条；岗位命中 {role_matches} 条；"
        "不同岗位 {distinct_predicted_titles} 种".format(**counts)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
