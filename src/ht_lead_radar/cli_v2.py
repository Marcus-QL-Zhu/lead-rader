"""Production command line and OpenClaw entry point."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .application import (
    DEFAULTS,
    FallbackSearchProvider,
    LeadRadarApplication,
    default_idempotency_key,
)
from .collectors import BingRSSCollector, SearXNGCollector, load_env_file
from .costs import SearchBudgetLedger
from .ops import backup_sqlite, build_daily_monitoring_report
from .relationships import DeepResearchEngine, RelationshipStore
from .requests import OpportunityMode, plan_opportunity_request
from .runtime import RunStore
from .source_packs import load_source_packs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ht-lead-radar",
        description="从招聘广告之前的公开信号识别总监级以上招聘机会。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="兼容入口：按行业/技术方向执行 Market Scan")
    _add_pipeline_arguments(run)
    run.add_argument("--direction", required=True)

    ask = subparsers.add_parser("ask", help="自然语言 Market Scan 或自动意图识别")
    _add_pipeline_arguments(ask)
    ask.add_argument("--question", required=True)

    float_parser = subparsers.add_parser(
        "float", help="用临时候选人描述反向寻找公司；候选人画像不持久化"
    )
    _add_pipeline_arguments(float_parser)
    float_parser.add_argument("--candidate", required=True)
    float_parser.add_argument("--direction")

    deep = subparsers.add_parser(
        "deep-research", help="按需深挖投资人、Hiring Manager、HR和创始团队"
    )
    deep.add_argument("--company", required=True)
    deep.add_argument("--direction", required=True)
    deep.add_argument("--provider", choices=["auto", "searxng", "bing"], default="auto")
    deep.add_argument("--env-file")
    deep.add_argument("--relationship-db", default=DEFAULTS["relationship_db"])
    deep.add_argument("--output-dir", default=DEFAULTS["output_dir"])
    deep.add_argument("--refresh", action="store_true")

    health = subparsers.add_parser("source-health", help="查看来源包和最近采集健康状态")
    health.add_argument("--direction", default="generic")
    health.add_argument("--source-packs", default=DEFAULTS["source_packs"])
    health.add_argument("--source-state-db", default=DEFAULTS["source_state_db"])
    health.add_argument("--include-disabled", action="store_true")

    status = subparsers.add_parser("run-status", help="读取某次分阶段运行状态")
    status.add_argument("--runtime-db", default=DEFAULTS["runtime_db"])
    status.add_argument("--run-id", required=True)

    resume = subparsers.add_parser("resume", help="从失败 checkpoint 续跑")
    resume.add_argument("--runtime-db", default=DEFAULTS["runtime_db"])
    resume.add_argument("--run-id", required=True)

    replay = subparsers.add_parser(
        "replay-run", help="重算低成本阶段并复用昂贵 checkpoint"
    )
    replay.add_argument("--runtime-db", default=DEFAULTS["runtime_db"])
    replay.add_argument("--run-id", required=True)
    replay.add_argument(
        "--from-stage",
        choices=[
            "collect",
            "normalize",
            "eventize",
            "score",
            "basic_research",
            "publish",
        ],
        default="normalize",
    )
    replay.add_argument(
        "--repeat-costly",
        action="store_true",
        help="明确重新执行昂贵阶段；默认复用以避免重复搜索/Metaso消耗",
    )

    monitor = subparsers.add_parser("monitor", help="生成机器可读日常健康检查")
    monitor.add_argument("--runtime-db", default=DEFAULTS["runtime_db"])
    monitor.add_argument("--source-health-db", default=DEFAULTS["source_state_db"])
    monitor.add_argument("--ops-metrics-db", default="data/ops-metrics.sqlite")
    monitor.add_argument("--budget-db", default=DEFAULTS["budget_db"])
    monitor.add_argument("--cron-text-file")

    backup = subparsers.add_parser("backup", help="在线一致性备份本项目 SQLite 数据")
    backup.add_argument("--backup-dir", default="backups")
    backup.add_argument(
        "--databases",
        nargs="+",
        default=[
            DEFAULTS["source_state_db"],
            DEFAULTS["fact_db"],
            DEFAULTS["runtime_db"],
            DEFAULTS["relationship_db"],
            DEFAULTS["budget_db"],
            DEFAULTS["feishu_state_db"],
            DEFAULTS["audit_db"],
            DEFAULTS["ops_metrics_db"],
        ],
    )
    return parser


def _add_pipeline_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--replay-json")
    parser.add_argument(
        "--provider",
        choices=["auto", "fixed", "searxng", "bing"],
        default=DEFAULTS["provider"],
    )
    parser.add_argument("--fixed-sources", default=DEFAULTS["fixed_sources"])
    parser.add_argument("--source-packs", default=DEFAULTS["source_packs"])
    parser.add_argument(
        "--source-topics",
        help="pipe-separated discovery topics; omitted means the requested direction",
    )
    parser.add_argument("--source-state-db", default=DEFAULTS["source_state_db"])
    parser.add_argument("--fact-db", default=DEFAULTS["fact_db"])
    parser.add_argument("--runtime-db", default=DEFAULTS["runtime_db"])
    parser.add_argument("--relationship-db", default=DEFAULTS["relationship_db"])
    parser.add_argument("--budget-db", default=DEFAULTS["budget_db"])
    parser.add_argument("--feishu-state-db", default=DEFAULTS["feishu_state_db"])
    parser.add_argument("--audit-db", default=DEFAULTS["audit_db"])
    parser.add_argument("--ops-metrics-db", default="data/ops-metrics.sqlite")
    parser.add_argument("--suppressions")
    parser.add_argument("--env-file")
    parser.add_argument("--josint-db")
    parser.add_argument("--output-dir", default=DEFAULTS["output_dir"])
    parser.add_argument("--minimum-score", type=float, default=0.0)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--limit-per-query", type=int, default=8)
    parser.add_argument("--metaso-verify-limit", type=int, default=3)
    parser.add_argument("--metaso-daily-point-budget", type=int, default=30)
    parser.add_argument("--metaso-provider-daily-limit", type=int, default=500)
    parser.add_argument("--metaso-points-per-search", type=int, default=6)
    parser.add_argument("--deep-research", action="store_true")
    parser.add_argument("--refresh-deep-research", action="store_true")
    parser.add_argument("--feishu-app-token")
    parser.add_argument("--feishu-table-id")
    parser.add_argument("--feishu-dry-run-path")
    parser.add_argument(
        "--skip-feishu-projection",
        action="store_true",
        help="跳过飞书多维表格投影；用于组合任务的子扫描",
    )
    parser.add_argument("--actor", default="openclaw")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--refresh", action="store_true")


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_console()
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"run", "ask", "float"}:
            return _run_pipeline(args)
        if args.command == "deep-research":
            return _deep_research(args)
        if args.command == "source-health":
            return _source_health(args)
        if args.command == "run-status":
            _print_json(RunStore(args.runtime_db).status(args.run_id))
            return 0
        if args.command == "resume":
            result = LeadRadarApplication(args.runtime_db).resume(args.run_id)
            return _print_application_result(result)
        if args.command == "replay-run":
            result = LeadRadarApplication(args.runtime_db).replay(
                args.run_id,
                from_stage=args.from_stage,
                reuse_costly=not args.repeat_costly,
            )
            return _print_application_result(result)
        if args.command == "monitor":
            return _monitor(args)
        if args.command == "backup":
            return _backup(args)
        raise ValueError(f"unknown command: {args.command}")
    except Exception as error:
        print(f"运行失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 1


def _run_pipeline(args: argparse.Namespace) -> int:
    if args.demo and args.replay_json:
        raise ValueError("--demo 与 --replay-json 不能同时使用")
    if args.command == "run":
        raw_request = f"最近{args.direction}行业有哪些公司可能要招总监以上职位？"
        plan = plan_opportunity_request(
            raw_request,
            deep_research=args.deep_research,
        )
        direction = args.direction
    elif args.command == "ask":
        plan = plan_opportunity_request(
            args.question,
            deep_research=args.deep_research,
        )
        if plan.request.mode is OpportunityMode.CANDIDATE_FLOAT:
            raise ValueError(
                "问题被识别为 Candidate Float；请使用 float --candidate，"
                "以确保候选人画像只存在于本次运行内。"
            )
        direction = plan.request.industry_topic or ""
    else:
        raw_request = (
            f"我有一位候选人：{args.candidate}。请反向分析哪些公司可能需要这位候选人。"
        )
        if args.direction:
            raw_request += f"重点分析{args.direction}行业。"
        plan = plan_opportunity_request(
            raw_request,
            candidate_context=args.candidate,
            deep_research=True,
        )
        direction = args.direction or plan.request.industry_topic or ""
    if not direction:
        question = (
            plan.clarification.next_question.prompt
            if plan.clarification.next_question
            else "请补充行业或技术方向。"
        )
        raise ValueError(question)

    payload = vars(args).copy()
    # The candidate description is task-local and must never enter runtime
    # checkpoints, manifests, projection state, or downstream fact stores.
    payload.pop("candidate", None)
    payload.update(
        {
            "direction": direction,
            "request_plan": plan.to_dict(),
        }
    )
    app = LeadRadarApplication(args.runtime_db)
    key = args.idempotency_key or default_idempotency_key(
        payload,
        refresh=args.refresh,
    )
    result = app.run(payload, key)
    if plan.clarification.next_question:
        print(
            "非阻塞补问（本次已按显式默认值继续）："
            + plan.clarification.next_question.prompt
        )
    return _print_application_result(result)


def _print_application_result(result) -> int:
    output = result.output
    print(f"Run ID: {result.runtime.run_id}")
    print(f"状态: {result.runtime.status}")
    print(f"Top队列: {result.lead_count} 家")
    if output.get("markdown_path"):
        print(f"Markdown: {output['markdown_path']}")
    if output.get("json_path"):
        print(f"JSON: {output['json_path']}")
    feishu = output.get("feishu") or {}
    if feishu:
        print(
            f"飞书投影: {feishu.get('mode', 'unknown')} / "
            f"{feishu.get('change_count', 0)} 个变化"
        )
    return 0 if result.lead_count else 2


def _deep_research(args: argparse.Namespace) -> int:
    env = load_env_file(args.env_file)
    providers = []
    if args.provider in {"auto", "searxng"}:
        providers.append(
            SearXNGCollector(base_url=env.get("SEARXNG_URL", "http://localhost:8080"))
        )
    if args.provider in {"auto", "bing"}:
        providers.append(BingRSSCollector())
    report = DeepResearchEngine(
        FallbackSearchProvider(providers),
        RelationshipStore(args.relationship_db),
    ).research(args.company, args.direction, refresh=args.refresh)
    target = Path(args.output_dir)
    target.mkdir(parents=True, exist_ok=True)
    slug = (
        re.sub(r"[^0-9A-Za-z\u4e00-\u9fff-]+", "-", args.company).strip("-")
        or "company"
    )
    path = target / f"deep-research-{slug}-{date.today().isoformat()}.json"
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"深度研究: {path.resolve()}")
    print(
        f"机构 {len(report.institutions)} / 投资人 {len(report.investors)} / "
        f"Hiring Manager {len(report.hiring_managers)} / HR {len(report.hr_people)} / "
        f"创始团队 {len(report.founders)}"
    )
    return 0


def _source_health(args: argparse.Namespace) -> int:
    registry = load_source_packs(args.source_packs)
    selection = registry.select(
        args.direction,
        include_disabled=args.include_disabled,
    )
    payload = {
        "selection": selection.to_dict(),
        "state_database": str(Path(args.source_state_db).resolve()),
        "collector_health": {},
    }
    try:
        from .source_pack_collector import SourcePackCollector

        with SourcePackCollector(
            registry_path=args.source_packs,
            state_db=args.source_state_db,
        ) as collector:
            payload["collector_health"] = collector.source_health_summary()
    except (ImportError, FileNotFoundError):
        payload["collector_health"] = {"status": "not_initialized"}
    _print_json(payload)
    return 0


def _monitor(args: argparse.Namespace) -> int:
    cron_entries = ()
    if args.cron_text_file:
        cron_entries = (
            Path(args.cron_text_file).read_text(encoding="utf-8").splitlines()
        )
    report = build_daily_monitoring_report(
        runtime_db=args.runtime_db,
        source_health_db=args.source_health_db,
        ops_metrics_db=args.ops_metrics_db,
        cron_entries=cron_entries,
        cron_command_marker=(
            "run_daily_fixed_sources.sh" if args.cron_text_file else None
        ),
    )
    payload = report.to_dict()
    payload["metaso_budget"] = SearchBudgetLedger(args.budget_db).status().to_dict()
    _print_json(payload)
    return report.suggested_exit_code


def _backup(args: argparse.Namespace) -> int:
    root = Path(args.backup_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve(strict=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = []
    for raw in args.databases:
        source = Path(raw)
        if not source.exists():
            continue
        target = root / f"{source.stem}-{stamp}{source.suffix or '.sqlite'}"
        results.append(
            backup_sqlite(
                source,
                target,
                allowed_root=root,
            ).to_dict()
        )
    _print_json({"backups": results})
    return 0


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
