#!/usr/bin/env python3
"""Bounded MetaSo discovery for historical Director+ job candidates.

Results are candidate evidence only.  They are not promoted to labels until a
reviewer verifies the exact company, title, China scope, publication date and
captures the underlying page as a replayable artifact.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from ht_lead_radar.collectors import MetasoCollector, load_env_file
from ht_lead_radar.costs import (
    METASO_CONSERVATIVE_POINTS_PER_SEARCH,
    SearchBudgetLedger,
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _result_payload(result: Any) -> dict[str, str]:
    value = {
        "title": result.title,
        "url": result.url,
        "snippet": result.snippet,
        "published_at": result.published_at,
    }
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        **value,
        "result_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--budget-db", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--result-limit", type=int, default=10)
    parser.add_argument("--configured-point-budget", type=int, default=180)
    parser.add_argument("--pass-name", default="initial")
    parser.add_argument("--query-template")
    parser.add_argument("--exclude-company", action="append", default=[])
    args = parser.parse_args()

    tasks_path = Path(args.tasks)
    task_bundle = _read(tasks_path)
    tasks = [
        task
        for task in task_bundle["tasks"]
        if task.get("kind") == "historical_job_discovery"
    ]
    output_path = Path(args.output)
    if output_path.exists():
        payload = _read(output_path)
    else:
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tasks_sha256": hashlib.sha256(tasks_path.read_bytes()).hexdigest(),
            "method": (
                "One bounded MetaSo query per company. Results are discovery "
                "candidates only and require page-level verification."
            ),
            "samples": [],
        }
    completed = {
        (item["task_id"], item.get("pass_name", "initial"))
        for item in payload["samples"]
    }
    excluded = set(args.exclude_company)
    pending = [
        task for task in tasks
        if (task["task_id"], args.pass_name) not in completed
        and task["company"] not in excluded
    ]
    if args.limit >= 0:
        pending = pending[: args.limit]

    env = load_env_file(args.env_file)
    api_key = env.get("METASO_API_KEY", "")
    if not api_key:
        raise RuntimeError("METASO_API_KEY is missing")
    collector = MetasoCollector(
        api_key=api_key,
        base_url=env.get("METASO_BASE_URL", "https://metaso.cn"),
    )
    ledger = SearchBudgetLedger(args.budget_db)
    for task in pending:
        search = next(
            item
            for item in task["channels"]
            if item["channel"] == "public_web_search"
        )
        query = str(search["query"])
        if args.query_template:
            query = args.query_template.format(
                company=task["company"],
                start=task["window_start"],
                end=task["window_end_exclusive"],
            )
        operation_key = (
            "historical-job-" + args.pass_name + "-" + task["task_id"]
        )
        charged = ledger.charge(
            operation_key,
            METASO_CONSERVATIVE_POINTS_PER_SEARCH,
            configured_limit=args.configured_point_budget,
        )
        if not charged:
            raise RuntimeError(
                f"MetaSo budget unavailable or operation already charged: {operation_key}"
            )
        results = collector.search(query, limit=args.result_limit)
        payload["samples"].append(
            {
                "task_id": task["task_id"],
                "pass_name": args.pass_name,
                "company": task["company"],
                "query": query,
                "searched_at": datetime.now(timezone.utc).isoformat(),
                "status": "candidate_review_required",
                "results": [_result_payload(item) for item in results],
            }
        )
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["budget_after"] = asdict(
            ledger.status(
                configured_limit=args.configured_point_budget
            )
        )
        _write(output_path, payload)
        print(f"{task['company']}: {len(results)} candidates", flush=True)
    print(output_path.resolve())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"historical discovery error: {exc}", file=sys.stderr)
        raise SystemExit(2)
