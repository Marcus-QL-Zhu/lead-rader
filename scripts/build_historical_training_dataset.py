#!/usr/bin/env python3
"""Build and calibrate the historical company-month training dataset."""

from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

from ht_lead_radar.backtest import HistoricalJob
from ht_lead_radar.historical_training import (
    CompanyPartition,
    CoverageAudit,
    HistoricalTrainingRow,
    RuleRoleModel,
    build_company_month_rows,
    deterministic_company_split,
    evaluate_role_ranker,
    fit_logistic_regression,
    make_dataset,
    stable_company_id,
    validate_company_partitions,
    validate_rows,
    write_dataset,
)
from ht_lead_radar.models import Evidence


EVIDENCE_FIELDS = {item.name for item in fields(Evidence)}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _month_ends(start: str, end: str) -> tuple[str, ...]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    current = start_date.replace(day=1)
    values: list[str] = []
    while current <= end_date:
        if current.month == 12:
            next_month = date(current.year + 1, 1, 1)
        else:
            next_month = date(current.year, current.month + 1, 1)
        month_end = min(date.fromordinal(next_month.toordinal() - 1), end_date)
        if month_end >= start_date:
            values.append(month_end.isoformat())
        current = next_month
    return tuple(values)


def _evidence_values(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        values = payload.get("evidence") or payload.get("items") or []
        return [item for item in values if isinstance(item, Mapping)]
    return []


def _jobs_values(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        values = payload.get("jobs") or []
        return [item for item in values if isinstance(item, Mapping)]
    return []


def _load_corpus(
    evaluation_dir: Path,
) -> tuple[
    list[Evidence],
    list[HistoricalJob],
    list[CoverageAudit],
    dict[str, str],
    dict[str, set[str]],
]:
    evidence: list[Evidence] = []
    jobs: list[HistoricalJob] = []
    audits: list[CoverageAudit] = []
    source_hashes: dict[str, str] = {}
    origins: dict[str, set[str]] = {}
    for directory in sorted(evaluation_dir.glob("holdout-v*")):
        evidence_path = directory / "evidence.json"
        if evidence_path.exists():
            payload = _read_json(evidence_path)
            source_hashes[str(evidence_path)] = _sha256(evidence_path)
            for raw in _evidence_values(payload):
                values = {
                    key: raw[key]
                    for key in EVIDENCE_FIELDS
                    if key in raw
                }
                for tuple_field in (
                    "people",
                    "organizations",
                    "statement_ids",
                ):
                    if tuple_field in values:
                        values[tuple_field] = tuple(values[tuple_field] or ())
                item = Evidence(**values)
                evidence.append(item)
                origins.setdefault(item.company, set()).add(directory.name)
        jobs_path = directory / "jobs.json"
        if not jobs_path.exists():
            continue
        payload = _read_json(jobs_path)
        source_hashes[str(jobs_path)] = _sha256(jobs_path)
        jobs.extend(
            HistoricalJob.from_dict(raw)
            for raw in _jobs_values(payload)
        )
        for raw in payload.get("audits", ()) if isinstance(payload, Mapping) else ():
            searches = raw.get("searches") or ()
            audits.append(
                CoverageAudit(
                    company=str(raw.get("company") or ""),
                    window_start=str(raw.get("window_start") or ""),
                    window_end_exclusive=str(
                        raw.get("window_end_exclusive") or ""
                    ),
                    channels_completed=tuple(
                        str(search.get("channel") or "")
                        for search in searches
                        if isinstance(search, Mapping)
                    ),
                    searched_at=str(raw.get("searched_at") or ""),
                    notes="Historical search summary only; no replayable artifacts.",
                )
            )
    return evidence, jobs, audits, source_hashes, origins


def _candidate_company_records(
    evidence: Iterable[Evidence],
    jobs: Iterable[HistoricalJob],
    origins: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    job_companies = {item.company for item in jobs}
    by_company: dict[str, dict[str, Any]] = {}
    for item in evidence:
        entry = by_company.setdefault(
            item.company,
            {
                "company": item.company,
                "company_id": stable_company_id(item.company),
                "company_type": item.company_type or "unknown",
                "aliases": [],
                "priority": 0,
                "origins": sorted(origins.get(item.company, ())),
            },
        )
        if entry["company_type"] == "unknown" and item.company_type:
            entry["company_type"] = item.company_type
    for company, entry in by_company.items():
        entry["priority"] = int(company in job_companies)
    return list(by_company.values())


def _pool_to_json(
    companies: Iterable[CompanyPartition],
    *,
    seed: str,
    excluded_holdouts: Iterable[str],
) -> dict[str, Any]:
    values = list(companies)
    return {
        "schema_version": 1,
        "seed": seed,
        "policy": {
            "company_level_isolation": True,
            "train_and_calibration_are_development_only": True,
            "test_labels_open_after_model_freeze": True,
            "excluded_from_development": list(excluded_holdouts),
        },
        "companies": [
            {
                "company_id": item.company_id,
                "company": item.company,
                "company_type": item.company_type,
                "split": item.split,
                "aliases": list(item.aliases),
            }
            for item in values
        ],
        "counts": {
            split: sum(item.split == split for item in values)
            for split in ("train", "calibration", "test")
        },
    }


def _load_pool(path: Path) -> tuple[CompanyPartition, ...]:
    payload = _read_json(path)
    result = tuple(
        CompanyPartition(
            company_id=str(item["company_id"]),
            company=str(item["company"]),
            company_type=str(item.get("company_type") or "unknown"),
            split=str(item["split"]),
            aliases=tuple(item.get("aliases") or ()),
        )
        for item in payload["companies"]
    )
    validate_company_partitions(result)
    return result


def init_pool(args: argparse.Namespace) -> int:
    evaluation_dir = Path(args.evaluation_dir)
    evidence, jobs, _audits, _hashes, origins = _load_corpus(evaluation_dir)
    candidates = _candidate_company_records(evidence, jobs, origins)
    v15_names = {
        item.company
        for item in evidence
        if "holdout-v15" in origins.get(item.company, set())
    }
    excluded_names = {
        item.company
        for item in evidence
        if "holdout-v14" in origins.get(item.company, set())
    }
    development_candidates = [
        item for item in candidates if item["company"] not in excluded_names
    ]
    partitions = deterministic_company_split(
        development_candidates,
        train_count=args.train_count,
        calibration_count=args.calibration_count,
        test_count=args.test_count,
        seed=args.seed,
        forced_test=sorted(v15_names),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            _pool_to_json(
                partitions,
                seed=args.seed,
                excluded_holdouts=("holdout-v14",),
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output.resolve())
    return 0


def build_dataset(args: argparse.Namespace) -> int:
    evaluation_dir = Path(args.evaluation_dir)
    evidence, jobs, audits, source_hashes, _origins = _load_corpus(evaluation_dir)
    pool_path = Path(args.pool)
    source_hashes[str(pool_path)] = _sha256(pool_path)
    companies = _load_pool(pool_path)
    cutoffs = _month_ends(args.start_cutoff, args.end_cutoff)
    explicit = {item.company_id: cutoffs for item in companies}
    rows = build_company_month_rows(
        companies=companies,
        evidence=evidence,
        jobs=jobs,
        coverage_audits=audits,
        explicit_cutoffs=explicit,
        months_back_from_job=args.months_back,
        lookback_days=args.lookback_days,
        horizon_days=args.horizon_days,
        contrastive_weight=args.contrastive_weight,
    )
    dataset = make_dataset(
        companies=companies,
        rows=rows,
        source_hashes=source_hashes,
    )
    write_dataset(args.output, dataset)
    print(Path(args.output).resolve())
    print(json.dumps(dataset.summary, ensure_ascii=False, indent=2))
    return 0


def _load_rows(path: Path) -> tuple[HistoricalTrainingRow, ...]:
    payload = _read_json(path)
    rows = tuple(
        HistoricalTrainingRow(
            sample_id=str(item["sample_id"]),
            company_id=str(item["company_id"]),
            company=str(item["company"]),
            company_type=str(item["company_type"]),
            split=str(item["split"]),
            cutoff=str(item["cutoff"]),
            horizon_end=str(item["horizon_end"]),
            role_family=str(item["role_family"]),
            label=str(item["label"]),
            label_weight=float(item["label_weight"]),
            observability=str(item["observability"]),
            evidence_ids=tuple(item.get("evidence_ids") or ()),
            matched_job_ids=tuple(item.get("matched_job_ids") or ()),
            features={
                str(key): float(value)
                for key, value in (item.get("features") or {}).items()
            },
            row_sha256=str(item["row_sha256"]),
        )
        for item in payload["rows"]
    )
    validate_rows(rows)
    return rows


def train_model(args: argparse.Namespace) -> int:
    dataset_path = Path(args.dataset)
    rows = _load_rows(dataset_path)
    model = fit_logistic_regression(
        rows,
        l2=args.l2,
        learning_rate=args.learning_rate,
        iterations=args.iterations,
    )
    rule_model = RuleRoleModel()
    metrics = {
        "dataset_sha256": _sha256(dataset_path),
        "model_kind": "weighted_logistic_role_ranker",
        "rule_baseline": {
            "calibration": evaluate_role_ranker(
                rule_model,
                rows,
                split="calibration",
                top_k=args.top_k,
            ),
            "test": evaluate_role_ranker(
                rule_model,
                rows,
                split="test",
                top_k=args.top_k,
            ),
        },
        "propensity_model_status": (
            "not_trained_without_replayable_negatives"
            if not any(row.label == "negative" for row in rows)
            else "eligible_for_separate_training"
        ),
        "calibration": evaluate_role_ranker(
            model,
            rows,
            split="calibration",
            top_k=args.top_k,
        ),
        "test": evaluate_role_ranker(
            model,
            rows,
            split="test",
            top_k=args.top_k,
        ),
    }
    output = {
        "model": model.to_dict(),
        "metrics": metrics,
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(target.resolve())
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers(dest="command", required=True)

    pool = subparsers.add_parser("init-pool")
    pool.add_argument("--evaluation-dir", default="evaluation")
    pool.add_argument("--output", required=True)
    pool.add_argument("--train-count", type=int, default=36)
    pool.add_argument("--calibration-count", type=int, default=9)
    pool.add_argument("--test-count", type=int, default=18)
    pool.add_argument("--seed", default="historical-training-v1")
    pool.set_defaults(handler=init_pool)

    build = subparsers.add_parser("build")
    build.add_argument("--evaluation-dir", default="evaluation")
    build.add_argument("--pool", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--start-cutoff", default="2025-01-31")
    build.add_argument("--end-cutoff", default="2026-03-31")
    build.add_argument("--months-back", type=int, default=4)
    build.add_argument("--lookback-days", type=int, default=180)
    build.add_argument("--horizon-days", type=int, default=90)
    build.add_argument("--contrastive-weight", type=float, default=0.25)
    build.set_defaults(handler=build_dataset)

    train = subparsers.add_parser("train")
    train.add_argument("--dataset", required=True)
    train.add_argument("--output", required=True)
    train.add_argument("--l2", type=float, default=0.1)
    train.add_argument("--learning-rate", type=float, default=0.05)
    train.add_argument("--iterations", type=int, default=800)
    train.add_argument("--top-k", type=int, default=5)
    train.set_defaults(handler=train_model)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.handler(args))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"historical dataset error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
