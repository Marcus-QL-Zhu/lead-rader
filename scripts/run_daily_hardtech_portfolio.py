#!/usr/bin/env python3
"""Run one broad fixed-source scan across all configured hard-tech topics."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ht_lead_radar.daily_topics import (  # noqa: E402
    DEFAULT_DIRECTIONS,
    DEFAULT_PORTFOLIO_DIRECTION,
)
from ht_lead_radar.feishu_notify import find_report  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--directions",
        default="|".join(DEFAULT_DIRECTIONS),
        help="pipe-separated topics collected in one pass",
    )
    parser.add_argument("--portfolio-direction", default=DEFAULT_PORTFOLIO_DIRECTION)
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--output-dir", default="reports-daily")
    parser.add_argument("--target-count", type=int, default=20)
    parser.add_argument(
        "--candidate-count",
        type=int,
        default=60,
        help="oversupply before delivery-cooldown selects the final Top 20",
    )
    parser.add_argument("--talent-state-db", default="data/talent-pool.sqlite")
    parser.add_argument("--cooldown-days", type=int, default=7)
    parser.add_argument("--fixed-sources", default="config/fixed-sources.json")
    parser.add_argument("--source-packs", default="config/source-packs.json")
    parser.add_argument("--source-state-db", default="data/fixed-sources.sqlite")
    parser.add_argument("--fact-db", default="data/facts.sqlite")
    parser.add_argument("--runtime-db", default="data/runtime.sqlite")
    parser.add_argument("--relationship-db", default="data/relationships.sqlite")
    parser.add_argument("--budget-db", default="data/search-budget.sqlite")
    parser.add_argument("--feishu-state-db", default="data/feishu-projection.sqlite")
    parser.add_argument("--audit-db", default="data/audit.sqlite")
    parser.add_argument("--ops-metrics-db", default="data/ops-metrics.sqlite")
    parser.add_argument(
        "--env-file",
        help="optional local dotenv; production injects a protected environment",
    )
    parser.add_argument("--josint-db", required=True)
    parser.add_argument("--suppressions")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="force a same-day refresh; cron leaves this disabled",
    )
    parser.add_argument("--metaso-verify-limit", type=int, default=3)
    parser.add_argument("--metaso-daily-point-budget", type=int, default=30)
    parser.add_argument("--metaso-provider-daily-limit", type=int, default=500)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    today = date.today().isoformat()
    if args.run_date != today:
        raise ValueError("--run-date only supports today's date")
    topics = tuple(
        dict.fromkeys(
            item.strip() for item in args.directions.split("|") if item.strip()
        )
    )
    if not topics:
        raise ValueError("--directions must contain at least one topic")
    if args.candidate_count < args.target_count:
        raise ValueError("--candidate-count must be at least --target-count")

    command = [
        sys.executable,
        str(Path(__file__).with_name("run_lead_radar_v2.py")),
        "run",
        "--direction",
        args.portfolio_direction,
        "--source-topics",
        "|".join(topics),
        "--provider",
        "fixed",
        "--fixed-sources",
        args.fixed_sources,
        "--source-packs",
        args.source_packs,
        "--source-state-db",
        args.source_state_db,
        "--fact-db",
        args.fact_db,
        "--runtime-db",
        args.runtime_db,
        "--relationship-db",
        args.relationship_db,
        "--budget-db",
        args.budget_db,
        "--feishu-state-db",
        args.feishu_state_db,
        "--audit-db",
        args.audit_db,
        "--ops-metrics-db",
        args.ops_metrics_db,
        "--josint-db",
        args.josint_db,
        "--output-dir",
        args.output_dir,
        "--minimum-score",
        "0",
        "--top",
        str(args.target_count),
        "--candidate-pool-size",
        str(args.candidate_count),
        "--daily-cooldown",
        "--cooldown-days",
        str(args.cooldown_days),
        "--talent-state-db",
        args.talent_state_db,
        "--metaso-verify-limit",
        str(args.metaso_verify_limit),
        "--metaso-daily-point-budget",
        str(args.metaso_daily_point_budget),
        "--metaso-provider-daily-limit",
        str(args.metaso_provider_daily_limit),
    ]
    if args.env_file:
        command.extend(["--env-file", args.env_file])
    if args.suppressions:
        command.extend(["--suppressions", args.suppressions])
    if args.refresh:
        command.append("--refresh")

    completed = subprocess.run(command, check=False)
    if completed.returncode not in {0, 2}:
        return completed.returncode
    report_path, report = find_report(
        args.output_dir,
        run_date=args.run_date,
        direction=args.portfolio_direction,
    )
    if report_path is None or report is None:
        raise FileNotFoundError(
            f"broad daily report not found: {args.portfolio_direction}"
        )
    print(
        json.dumps(
            {
                # Exit 2 is the application contract for a valid analysis with
                # zero selected companies, not a degraded/failed run.
                "status": "completed",
                "portfolio_report": str(report_path),
                "source_topics": list(topics),
                "company_count": len(report.get("leads") or ()),
                "candidate_pool_size": args.candidate_count,
                "collection_strategy": "single_pass_multi_topic",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
