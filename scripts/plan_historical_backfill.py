#!/usr/bin/env python3
"""Create a reproducible queue for historical job and precursor backfill."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from ht_lead_radar.backtest import HistoricalJob
from ht_lead_radar.historical_training import monthly_cutoffs_before


SIGNAL_QUERY_GROUPS = {
    "leadership_organization": (
        "任命 履新 换帅 高管 事业部 区域负责人",
        "appointed president director executive business unit",
    ),
    "capital_structure": (
        "融资 并购 合资 上市辅导 资金用途",
        "funding acquisition joint venture IPO",
    ),
    "operations_capacity": (
        "落户 工厂 产线 扩产 投产 环评 施工 设备",
        "new site factory capacity production permit",
    ),
    "customer_revenue": (
        "订单 定点 中标 客户验证 供应商准入 渠道",
        "order award customer validation supplier channel",
    ),
    "technology_regulation": (
        "产品发布 样机 流片 临床 注册证 认证 专利",
        "product launch prototype tapeout clinical approval patent",
    ),
    "enterprise_system": (
        "ERP MES PLM CRM 数字化 招标 上线",
        "ERP MES PLM CRM digital transformation tender",
    ),
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _job_values(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        return [
            item
            for item in payload.get("jobs", ())
            if isinstance(item, Mapping)
        ]
    return []


def _known_jobs(evaluation_dir: Path) -> dict[str, list[HistoricalJob]]:
    result: dict[str, list[HistoricalJob]] = {}
    for path in sorted(evaluation_dir.glob("holdout-v*/jobs.json")):
        if path.parent.name in {"holdout-v14", "holdout-v15"}:
            continue
        for raw in _job_values(_read(path)):
            item = HistoricalJob.from_dict(raw)
            if item.company and item.title and item.published_at:
                result.setdefault(item.company, []).append(item)
    return result


def _task_id(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "task_" + hashlib.sha256(encoded).hexdigest()[:20]


def _label_discovery_task(
    company: str,
    *,
    start: str,
    end: str,
) -> dict[str, Any]:
    task = {
        "kind": "historical_job_discovery",
        "company": company,
        "window_start": start,
        "window_end_exclusive": end,
        "seniority_scope": "Director+/VP/SVP/EVP/CxO/ownership Head",
        "exclude": (
            "manager, expert, engineer, ordinary Lead, Associate/Assistant/"
            "Deputy Director, AVP"
        ),
        "channels": [
            {
                "channel": "official_careers",
                "query": (
                    f"{company} official careers Director Head VP "
                    f"after:{start} before:{end}"
                ),
            },
            {
                "channel": "public_web_search",
                "query": (
                    f'"{company}" (总监 OR Director OR "Head of" OR VP) '
                    f"招聘 after:{start} before:{end}"
                ),
            },
            {
                "channel": "job_aggregators",
                "query": (
                    f'"{company}" (总监 OR Director OR Head OR VP) '
                    f"(猎聘 OR 职友集 OR LinkedIn)"
                ),
            },
        ],
        "required_artifacts": [
            "result URL",
            "captured_at with timezone",
            "raw page or result snapshot",
            "content_sha256",
            "exact published_at for every positive job",
        ],
        "status": "pending",
    }
    return {"task_id": _task_id(task), **task}


def _precursor_task(
    company: str,
    job: HistoricalJob,
    cutoff: date,
    *,
    lookback_days: int,
) -> dict[str, Any]:
    window_start = cutoff - timedelta(days=lookback_days)
    task = {
        "kind": "precursor_evidence_backfill",
        "company": company,
        "anchored_job": {
            "title": job.title,
            "published_at": job.published_at,
            "source_url": job.source_url,
        },
        "simulated_cutoff": cutoff.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end_inclusive": cutoff.isoformat(),
        "prediction_input_boundary": (
            "Only evidence publicly available on or before simulated_cutoff; "
            "never include the anchored job or later recruiting information."
        ),
        "query_groups": [
            {
                "group": group,
                "queries": [
                    f'"{company}" {terms} after:{window_start} before:{cutoff + timedelta(days=1)}'
                    for terms in term_groups
                ],
            }
            for group, term_groups in SIGNAL_QUERY_GROUPS.items()
        ],
        "required_evidence_fields": [
            "event_date",
            "published_at or observed_at",
            "event_type",
            "title",
            "fact",
            "source_url",
            "source_kind",
            "source_group",
            "source_grade",
            "content_sha256",
        ],
        "status": "pending",
    }
    return {"task_id": _task_id(task), **task}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", required=True)
    parser.add_argument("--evaluation-dir", default="evaluation")
    parser.add_argument("--output", required=True)
    parser.add_argument("--job-window-start", default="2024-01-01")
    parser.add_argument("--job-window-end", default="2026-07-01")
    parser.add_argument("--months-back", type=int, default=4)
    parser.add_argument("--lookback-days", type=int, default=180)
    args = parser.parse_args()

    pool = _read(Path(args.pool))
    jobs_by_company = _known_jobs(Path(args.evaluation_dir))
    tasks: list[dict[str, Any]] = []
    development_companies = [
        item
        for item in pool["companies"]
        if item["split"] in {"train", "calibration"}
    ]
    for item in development_companies:
        company = str(item["company"])
        known = jobs_by_company.get(company, [])
        if not known:
            tasks.append(
                _label_discovery_task(
                    company,
                    start=args.job_window_start,
                    end=args.job_window_end,
                )
            )
            continue
        for job in known:
            for cutoff in monthly_cutoffs_before(
                job.published_at,
                months_back=args.months_back,
            ):
                tasks.append(
                    _precursor_task(
                        company,
                        job,
                        cutoff,
                        lookback_days=args.lookback_days,
                    )
                )
    payload = {
        "schema_version": 1,
        "pool_sha256": hashlib.sha256(Path(args.pool).read_bytes()).hexdigest(),
        "test_partition_included": False,
        "rules": {
            "job_ads_are_labels_only": True,
            "josint_is_not_prediction_evidence": True,
            "negative_requires_replayable_artifacts": True,
            "same_company_never_crosses_partitions": True,
        },
        "counts": {
            "development_companies": len(development_companies),
            "companies_with_known_job_anchor": sum(
                bool(jobs_by_company.get(str(item["company"])))
                for item in development_companies
            ),
            "companies_requiring_job_discovery": sum(
                not jobs_by_company.get(str(item["company"]))
                for item in development_companies
            ),
            "tasks": len(tasks),
        },
        "tasks": tasks,
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(target.resolve())
    print(json.dumps(payload["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
