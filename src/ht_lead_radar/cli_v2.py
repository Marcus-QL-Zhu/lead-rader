"""Production command line and OpenClaw entry point."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
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
from .sanitization import safe_error_class
from .source_packs import load_source_packs


PRODUCTION_MATERIAL_DATABASES = (
    "data/fixed-sources.sqlite",
    "data/facts.sqlite",
    "data/runtime.sqlite",
    "data/search-budget.sqlite",
    "data/feishu-projection.sqlite",
    "data/audit.sqlite",
    "data/ops-metrics.sqlite",
    "data/talent-pool.sqlite",
    "data/feishu-notifications.sqlite",
)
# Deep-research state is created lazily and is therefore legitimately absent
# on an installation that has only run the daily workflow.  Discovery still
# includes it (and every other SQLite file) whenever it exists.
PRODUCTION_OPTIONAL_DATABASES = ("data/relationships.sqlite",)
PRODUCTION_SOURCE_MANIFESTS = (
    "config/fixed-sources.json",
    "config/source-packs.json",
    "config/openclaw-report-cron.json",
)
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


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

    backup = subparsers.add_parser(
        "backup",
        help="创建并验证部署前生产状态备份（SQLite 与来源配置清单）",
    )
    backup.add_argument("--backup-dir", default="backups")
    backup.add_argument(
        "--git-sha",
        help="部署目标 commit；省略时仅从当前 Git checkout 的 HEAD 安全解析",
    )
    backup.add_argument(
        "--databases",
        nargs="+",
        default=list(PRODUCTION_MATERIAL_DATABASES),
    )
    backup.add_argument(
        "--manifests",
        nargs="+",
        default=list(PRODUCTION_SOURCE_MANIFESTS),
    )
    backup.add_argument(
        "--discover-data-dir",
        default="data",
        help="额外备份该目录下所有 .db/.sqlite/.sqlite3；不替代必需清单",
    )
    backup.add_argument(
        "--nonproduction-allow-missing",
        action="store_true",
        help="仅限隔离测试环境：允许明确列出的生产关键项缺失",
    )

    verify_backup = subparsers.add_parser(
        "verify-backup", help="离线复核备份清单、哈希及临时恢复完整性"
    )
    verify_backup.add_argument("--manifest", required=True)
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
    parser.add_argument(
        "--candidate-pool-size",
        type=int,
        default=20,
        help="score this many candidates before applying the final report limit",
    )
    parser.add_argument("--daily-cooldown", action="store_true")
    parser.add_argument("--cooldown-days", type=int, default=7)
    parser.add_argument("--talent-state-db", default="data/talent-pool.sqlite")
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
        if args.command == "verify-backup":
            return _verify_backup_command(args)
        raise ValueError(f"unknown command: {args.command}")
    except Exception as error:
        print(f"运行失败：{safe_error_class(error)}", file=sys.stderr)
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
    git_sha = _resolve_backup_git_sha(args.git_sha)
    root = _prepare_backup_root(args.backup_dir)
    required_databases = _resolve_required_backup_inputs(
        args.databases,
        kind="SQLite database",
        allow_missing=bool(args.nonproduction_allow_missing),
    )
    required_manifests = _resolve_required_backup_inputs(
        args.manifests,
        kind="source/config manifest",
        allow_missing=bool(args.nonproduction_allow_missing),
    )
    discovered = _discover_material_databases(args.discover_data_dir)
    databases = _deduplicate_paths([*required_databases, *discovered])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    final = root / f"production-predeploy-{stamp}-{git_sha[:12]}"
    if final.exists() or final.is_symlink():
        raise FileExistsError("backup set target already exists")
    staging: Path | None = Path(
        tempfile.mkdtemp(prefix=".production-predeploy-", dir=root)
    )
    if staging.parent != root:
        raise ValueError("backup staging escaped backup root")
    try:
        os.chmod(staging, 0o700)
        sqlite_dir = staging / "sqlite"
        manifest_dir = staging / "source-manifests"
        if databases:
            sqlite_dir.mkdir(mode=0o700)
        if required_manifests:
            manifest_dir.mkdir(mode=0o700)
        items: list[dict[str, object]] = []
        for index, source in enumerate(databases, 1):
            target = sqlite_dir / f"{index:02d}-{source.name}"
            result = backup_sqlite(source, target, allowed_root=sqlite_dir)
            os.chmod(target, 0o600)
            items.append(
                _backup_item(
                    kind="sqlite",
                    source=source,
                    target=target,
                    staging=staging,
                    sqlite_integrity_check=result.integrity_check,
                )
            )
        for index, source in enumerate(required_manifests, 1):
            target = manifest_dir / f"{index:02d}-{source.name}"
            with source.open("rb") as source_stream, target.open("xb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            os.chmod(target, 0o600)
            items.append(
                _backup_item(
                    kind="source_manifest",
                    source=source,
                    target=target,
                    staging=staging,
                    sqlite_integrity_check=None,
                )
            )

        manifest: dict[str, object] = {
            "schema_version": 1,
            "backup_kind": "production-predeploy",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "git_sha": git_sha,
            "allow_missing_nonproduction": bool(args.nonproduction_allow_missing),
            "items": items,
        }
        manifest["manifest_sha256"] = _canonical_digest(manifest)
        manifest_path = staging / "manifest.json"
        with manifest_path.open("xb") as stream:
            stream.write(_canonical_json_bytes(manifest) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(manifest_path, 0o600)
        verified = _verify_backup_manifest(manifest_path)
        os.replace(staging, final)
        staging = None
        verified = _verify_backup_manifest(final / "manifest.json")
    except Exception:
        if staging is not None and staging.parent == root and staging.exists():
            shutil.rmtree(staging)
        raise
    _print_json(
        {
            "backup_manifest": str(final / "manifest.json"),
            "manifest_sha256": verified["manifest_sha256"],
            "item_count": len(verified["items"]),
            "verified": True,
        }
    )
    return 0


def _resolve_backup_git_sha(explicit: str | None) -> str:
    if explicit is not None:
        value = str(explicit).strip()
        if not _GIT_SHA.fullmatch(value):
            raise ValueError("--git-sha must be a lowercase 40-hex commit")
        return value

    checkout = Path.cwd().resolve(strict=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "--no-replace-objects",
                "rev-parse",
                "--verify",
                "HEAD^{commit}",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("unable to resolve backup commit from Git HEAD") from error
    value = completed.stdout.strip()
    if completed.returncode != 0 or not _GIT_SHA.fullmatch(value):
        raise ValueError("unable to resolve backup commit from Git HEAD")
    return value


def _verify_backup_command(args: argparse.Namespace) -> int:
    manifest = _verify_backup_manifest(Path(args.manifest))
    _print_json(
        {
            "backup_manifest": str(Path(args.manifest).resolve(strict=True)),
            "manifest_sha256": manifest["manifest_sha256"],
            "item_count": len(manifest["items"]),
            "verified": True,
        }
    )
    return 0


def _prepare_backup_root(raw: str | Path) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.exists() and candidate.is_symlink():
        raise ValueError("backup root may not be a symlink")
    candidate.mkdir(parents=True, exist_ok=True)
    root = candidate.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("backup root must be a directory")
    os.chmod(root, 0o700)
    return root


def _safe_backup_source(raw: str | Path, kind: str) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.is_symlink():
        raise ValueError(f"{kind} may not be a symlink")
    source = candidate.resolve(strict=True)
    metadata = source.stat()
    if not source.is_file() or metadata.st_nlink != 1:
        raise ValueError(f"{kind} must be a single-link regular file")
    return source


def _resolve_required_backup_inputs(
    values: list[str], *, kind: str, allow_missing: bool
) -> list[Path]:
    resolved: list[Path] = []
    missing = 0
    for raw in values:
        candidate = Path(raw).expanduser()
        if not candidate.exists():
            missing += 1
            continue
        resolved.append(_safe_backup_source(candidate, kind))
    if missing and not allow_missing:
        raise FileNotFoundError(f"{missing} required {kind} input(s) are missing")
    return resolved


def _discover_material_databases(raw: str | Path) -> list[Path]:
    candidate = Path(raw).expanduser()
    if not candidate.exists():
        return []
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError("database discovery root must be a non-link directory")
    root = candidate.resolve(strict=True)
    discovered: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if child.suffix.casefold() not in {".db", ".sqlite", ".sqlite3"}:
            continue
        discovered.append(_safe_backup_source(child, "discovered SQLite database"))
    return discovered


def _deduplicate_paths(values: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _backup_item(
    *,
    kind: str,
    source: Path,
    target: Path,
    staging: Path,
    sqlite_integrity_check: str | None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "source_path": str(source),
        "backup_path": target.relative_to(staging).as_posix(),
        "source_size": source.stat().st_size,
        "source_sha256": _sha256_file(source),
        "backup_size": target.stat().st_size,
        "backup_sha256": _sha256_file(target),
        "sqlite_integrity_check": sqlite_integrity_check,
        "restore_integrity_check": (
            _temporary_restore_integrity(target)
            if kind == "sqlite"
            else "sha256-match"
        ),
    }


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_digest(value: dict[str, object]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "manifest_sha256"}
    return sha256(_canonical_json_bytes(unsigned)).hexdigest()


def _integrity_check(path: Path) -> str:
    connection = sqlite3.connect(
        f"file:{path.resolve(strict=True).as_posix()}?mode=ro&immutable=1",
        uri=True,
        timeout=30,
    )
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()


def _temporary_restore_integrity(path: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="lead-radar-restore-check-") as temporary:
        restored = Path(temporary) / "restored.sqlite"
        shutil.copyfile(path, restored)
        result = _integrity_check(restored)
    if result.casefold() != "ok":
        raise sqlite3.DatabaseError("temporary SQLite restore failed integrity check")
    return "ok"


def _verify_backup_manifest(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise ValueError("backup manifest may not be a symlink")
    manifest_path = path.resolve(strict=True)
    if not manifest_path.is_file() or manifest_path.stat().st_nlink != 1:
        raise ValueError("backup manifest must be a single-link regular file")
    if os.name != "nt" and manifest_path.stat().st_mode & 0o077:
        raise ValueError("backup manifest must have mode 0600")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if raw != _canonical_json_bytes(manifest) + b"\n":
        raise ValueError("backup manifest must be canonical JSON")
    expected_keys = {
        "schema_version",
        "backup_kind",
        "created_at",
        "git_sha",
        "allow_missing_nonproduction",
        "items",
        "manifest_sha256",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_keys:
        raise ValueError("backup manifest keys are invalid")
    if (
        manifest["schema_version"] != 1
        or manifest["backup_kind"] != "production-predeploy"
        or not _GIT_SHA.fullmatch(str(manifest["git_sha"]))
        or not isinstance(manifest["allow_missing_nonproduction"], bool)
        or manifest["manifest_sha256"] != _canonical_digest(manifest)
    ):
        raise ValueError("backup manifest metadata is invalid")
    created_at = datetime.fromisoformat(str(manifest["created_at"]))
    if created_at.tzinfo is None or created_at.utcoffset() != timezone.utc.utcoffset(
        created_at
    ):
        raise ValueError("backup timestamp must be UTC")
    items = manifest["items"]
    if not isinstance(items, list) or not items:
        raise ValueError("backup manifest must contain at least one item")

    root = manifest_path.parent.resolve(strict=True)
    expected_files = {Path("manifest.json")}
    seen_sources: set[str] = set()
    seen_backups: set[Path] = set()
    required_item_keys = {
        "kind",
        "source_path",
        "backup_path",
        "source_size",
        "source_sha256",
        "backup_size",
        "backup_sha256",
        "sqlite_integrity_check",
        "restore_integrity_check",
    }
    with tempfile.TemporaryDirectory(prefix="lead-radar-restore-check-") as temporary:
        restore_root = Path(temporary)
        for index, item in enumerate(items):
            if not isinstance(item, dict) or set(item) != required_item_keys:
                raise ValueError("backup item keys are invalid")
            kind = item["kind"]
            if kind not in {"sqlite", "source_manifest"}:
                raise ValueError("backup item kind is invalid")
            relative = Path(str(item["backup_path"]))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("backup item path escaped backup set")
            if relative in seen_backups or str(item["source_path"]) in seen_sources:
                raise ValueError("backup manifest contains a duplicate item")
            seen_backups.add(relative)
            seen_sources.add(str(item["source_path"]))
            target = root / relative
            if target.is_symlink():
                raise ValueError("backup item may not be a symlink")
            resolved = target.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as error:
                raise ValueError("backup item path escaped backup set") from error
            metadata = resolved.stat()
            if not resolved.is_file() or metadata.st_nlink != 1:
                raise ValueError("backup item must be a single-link regular file")
            if os.name != "nt" and metadata.st_mode & 0o077:
                raise ValueError("backup items must have mode 0600")
            if metadata.st_size != item["backup_size"]:
                raise ValueError("backup item size mismatch")
            if _sha256_file(resolved) != item["backup_sha256"]:
                raise ValueError("backup item hash mismatch")
            if not isinstance(item["source_path"], str) or not Path(
                item["source_path"]
            ).is_absolute():
                raise ValueError("backup source path must be absolute")
            if type(item["source_size"]) is not int or item["source_size"] < 0:
                raise ValueError("backup source size is invalid")
            if type(item["backup_size"]) is not int or item["backup_size"] < 0:
                raise ValueError("backup size is invalid")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item["source_sha256"])):
                raise ValueError("backup source digest is invalid")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item["backup_sha256"])):
                raise ValueError("backup digest is invalid")
            # Source manifests are copied byte-for-byte.  Recording both sides
            # without comparing them would let a torn or substituted copy be
            # blessed as verified merely because the backup is self-consistent.
            # SQLite uses the online backup API and can legitimately have a
            # different physical layout, so its equivalence is established by
            # the integrity and temporary-restore checks below instead.
            if kind == "source_manifest" and (
                item["source_size"] != item["backup_size"]
                or item["source_sha256"] != item["backup_sha256"]
            ):
                raise ValueError("source-manifest backup does not match source")
            restored = restore_root / f"{index:03d}-{resolved.name}"
            shutil.copyfile(resolved, restored)
            if kind == "sqlite":
                if item["sqlite_integrity_check"] != "ok":
                    raise ValueError("SQLite backup was not integrity checked")
                if item["restore_integrity_check"] != "ok":
                    raise ValueError("SQLite backup was not restore checked")
                if _integrity_check(restored).casefold() != "ok":
                    raise ValueError("temporary SQLite restore failed integrity check")
            else:
                if item["sqlite_integrity_check"] is not None:
                    raise ValueError("source manifest has SQLite integrity metadata")
                if _sha256_file(restored) != item["backup_sha256"]:
                    raise ValueError("temporary source-manifest restore hash mismatch")
                if item["restore_integrity_check"] != "sha256-match":
                    raise ValueError("source-manifest restore marker is invalid")
            expected_files.add(relative)

    if not manifest["allow_missing_nonproduction"]:
        sqlite_names = {
            Path(str(item["source_path"])).name
            for item in items
            if item["kind"] == "sqlite"
        }
        source_manifest_names = {
            Path(str(item["source_path"])).name
            for item in items
            if item["kind"] == "source_manifest"
        }
        required_sqlite_names = {
            Path(value).name for value in PRODUCTION_MATERIAL_DATABASES
        }
        required_manifest_names = {
            Path(value).name for value in PRODUCTION_SOURCE_MANIFESTS
        }
        if not required_sqlite_names.issubset(sqlite_names) or not (
            required_manifest_names.issubset(source_manifest_names)
        ):
            raise ValueError("production backup manifest is incomplete")

    actual_files = {
        child.relative_to(root)
        for child in root.rglob("*")
        if child.is_file() or child.is_symlink()
    }
    if actual_files != expected_files:
        raise ValueError("backup set contains missing or extra files")
    expected_directories = {Path(".")}
    for relative in expected_files:
        expected_directories.update(relative.parents)
    actual_directories = {
        Path("."),
        *(
            child.relative_to(root)
            for child in root.rglob("*")
            if child.is_dir() and not child.is_symlink()
        ),
    }
    if actual_directories != expected_directories:
        raise ValueError("backup set contains missing or extra directories")
    if os.name != "nt":
        for relative in actual_directories:
            directory = root if relative == Path(".") else root / relative
            if directory.stat().st_mode & 0o077:
                raise ValueError("backup directories must have mode 0700")
    return manifest


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
