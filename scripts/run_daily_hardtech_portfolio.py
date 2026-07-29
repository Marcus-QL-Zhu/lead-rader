#!/usr/bin/env python3
"""Run fixed-source scans by sector and write one balanced portfolio report."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ht_lead_radar.daily_portfolio import (
    DEFAULT_DIRECTIONS,
    DEFAULT_PORTFOLIO_DIRECTION,
    combine_sector_reports,
)
from ht_lead_radar.collectors import load_env_file
from ht_lead_radar.feishu import FeishuBitableClient, ProjectionState, sync_leads
from ht_lead_radar.feishu_notify import find_report
from ht_lead_radar.serde import lead_from_dict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--directions",
        default="|".join(DEFAULT_DIRECTIONS),
        help="pipe-separated sector directions",
    )
    parser.add_argument("--portfolio-direction", default=DEFAULT_PORTFOLIO_DIRECTION)
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--output-dir", default="reports-daily")
    parser.add_argument("--target-count", type=int, default=20)
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
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--josint-db", required=True)
    parser.add_argument("--suppressions")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="强制子扫描重新采集；cron 默认不启用",
    )
    parser.add_argument("--metaso-verify-limit", type=int, default=3)
    parser.add_argument("--metaso-daily-point-budget", type=int, default=30)
    parser.add_argument("--metaso-provider-daily-limit", type=int, default=500)
    return parser


def sync_portfolio_projection(portfolio: dict, args: argparse.Namespace) -> dict:
    """Project the combined company universe exactly once."""

    env = load_env_file(args.env_file)
    app_id = str(env.get("FEISHU_APP_ID") or "")
    app_secret = str(env.get("FEISHU_APP_SECRET") or "")
    app_token = str(env.get("FEISHU_BITABLE_APP_TOKEN") or "")
    table_id = str(env.get("FEISHU_BITABLE_TABLE_ID") or "")
    dry_run_path = Path(args.feishu_state_db).with_name("feishu-change-set.json")
    client = None
    if all((app_id, app_secret, app_token, table_id)):
        client = FeishuBitableClient(app_id, app_secret, app_token, table_id)
    try:
        changes = sync_leads(
            [lead_from_dict(item) for item in portfolio.get("leads") or ()],
            ProjectionState(args.feishu_state_db),
            client=client,
            dry_run_path=dry_run_path,
        )
        return {
            "mode": "live" if client else "dry_run",
            "change_count": len(changes),
            "change_set": str(dry_run_path.resolve()),
            "blocked_reason": (
                ""
                if client
                else "缺少飞书多维表格凭证；已生成组合结果的增量变更集，未发送。"
            ),
        }
    except Exception as error:
        return {
            "mode": "error",
            "error": f"{type(error).__name__}: {error}",
            "change_set": str(dry_run_path.resolve()),
        }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    today = date.today().isoformat()
    if args.run_date != today:
        raise ValueError("--run-date only supports today's date")
    directions = [item.strip() for item in args.directions.split("|") if item.strip()]
    if not directions:
        raise ValueError("--directions must contain at least one sector")
    reports = []
    soft_status = 0
    runner = Path(__file__).with_name("run_lead_radar_v2.py")
    for direction in directions:
        command = [
            sys.executable,
            str(runner),
            "run",
            "--direction",
            direction,
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
            "--skip-feishu-projection",
            "--env-file",
            args.env_file,
            "--josint-db",
            args.josint_db,
            "--output-dir",
            args.output_dir,
            "--minimum-score",
            "0",
            "--top",
            "20",
            "--metaso-verify-limit",
            str(args.metaso_verify_limit),
            "--metaso-daily-point-budget",
            str(args.metaso_daily_point_budget),
            "--metaso-provider-daily-limit",
            str(args.metaso_provider_daily_limit),
        ]
        if args.suppressions:
            command.extend(["--suppressions", args.suppressions])
        if args.refresh:
            command.append("--refresh")
        completed = subprocess.run(command, check=False)
        if completed.returncode not in {0, 2}:
            return completed.returncode
        soft_status = max(soft_status, completed.returncode)
        report_path, report = find_report(
            args.output_dir,
            run_date=args.run_date,
            direction=direction,
        )
        if report_path is None or report is None:
            raise FileNotFoundError(f"sector report not found: {direction}")
        reports.append(report)

    portfolio = combine_sector_reports(
        reports,
        direction=args.portfolio_direction,
        target_count=args.target_count,
    )
    portfolio["manifest"]["integration_status"]["feishu"] = (
        sync_portfolio_projection(portfolio, args)
    )
    output = (
        Path(args.output_dir)
        / f"lead-radar-{args.portfolio_direction}-{args.run_date}.json"
    )
    output.write_text(
        json.dumps(portfolio, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "completed" if soft_status == 0 else "partial",
                "portfolio_report": str(output),
                "directions": directions,
                "company_count": len(portfolio["leads"]),
            },
            ensure_ascii=False,
        )
    )
    return soft_status


if __name__ == "__main__":
    raise SystemExit(main())
