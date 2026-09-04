#!/usr/bin/env python3
"""Generate and persist today's talent-pool drafts from a Lead report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ht_lead_radar.feishu_notify import find_report
from ht_lead_radar.direct_talent_generator import generate_direct_talent_bundle
from ht_lead_radar.daily_opportunity_selection import select_daily_opportunities
from ht_lead_radar.talent_pool import generate_draft_bundle, write_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore
from ht_lead_radar.sanitization import (
    safe_error,
    safe_error_class,
    sanitize_text,
    sanitize_tree,
)


def _failure_bundle(
    report: dict,
    error: Exception,
    *,
    report_path: Path | None = None,
    analysis_status: str = "completed",
    health_report: dict | None = None,
    error_class: str | None = None,
) -> dict:
    """A zero-draft snapshot is still a completed, readable daily outcome."""

    manifest = report.get("manifest") or {}
    return {
        "schema_version": 1,
        "run_date": str(manifest.get("as_of") or ""),
        "direction": str(manifest.get("direction") or ""),
        "source_run_id": str(manifest.get("run_id") or ""),
        "drafts": [],
        # Persist the actionable class, never an arbitrary provider response.
        "generation_error": (
            f"{safe_error_class(error_class or error)}: draft generation failed"
        ),
        "generation_provider": "direct-llm",
        "generation_model": "",
        "company_demand_analysis": [],
        "talent_themes": [],
        "selection_summary": dict(report.get("daily_opportunity_segments") or {}),
        "final_report_opportunities": _final_report_opportunities(report),
        "completion_status": _completion_status(
            report,
            draft_status="failed",
            analysis_status=analysis_status,
            health_report=health_report,
        ),
        "analysis_report": _analysis_reference(report_path),
    }


def _analysis_failure_bundle(
    *,
    run_date: str,
    direction: str,
    error_class: str,
) -> dict:
    """Persist a reset-safe completion when analysis never produced a report."""

    bounded_class = safe_error_class(error_class)
    issue = {
        "source_id": "pipeline",
        "status": "critical",
        "error_class": bounded_class,
        "detail": (
            "daily analysis exceeded its fixed wall-clock budget"
            if bounded_class == "PortfolioWallClockTimeout"
            else "daily analysis did not complete"
        ),
    }
    return {
        "schema_version": 1,
        "run_date": run_date,
        "direction": direction,
        "source_run_id": f"analysis-failure:{run_date}:{direction}",
        "drafts": [],
        "generation_error": "",
        "generation_provider": "",
        "generation_model": "",
        "analysis_error_class": bounded_class,
        "company_demand_analysis": [],
        "talent_themes": [],
        "selection_summary": {},
        "final_report_opportunities": [],
        "completion_status": {
            "analysis_status": "failed",
            "draft_generation_status": "not_run",
            "notification_status": "pending",
            "source_health_status": "critical",
            "critical_health_issues": [issue],
            "source_warnings": [issue],
        },
        "analysis_report": {"path": "", "sha256": ""},
    }


def _source_health_status(report: dict) -> str:
    source_summary = (report.get("manifest") or {}).get("source_summary") or {}
    failures = source_summary.get("failures") or []
    structured_runs = [
        run for run in source_summary.get("runs") or [] if isinstance(run, dict)
    ]
    exact_statuses: list[str] = []
    dedicated: list[dict] = []
    for run in structured_runs:
        outer_status = str(run.get("status") or "")
        if outer_status:
            exact_statuses.append(outer_status)
        run_summary = run.get("run_summary") or {}
        aggregate = run_summary.get("dedicated_aggregate") or {}
        dedicated.append(aggregate)
        for collection in (
            run_summary.get("sources") or {},
            aggregate.get("sources") or {},
        ):
            exact_statuses.extend(
                str(item.get("status") or "")
                for item in collection.values()
                if isinstance(item, dict)
            )
    if "error" in exact_statuses:
        return "critical"
    if "partial" in exact_statuses:
        return "warning"
    if any(
        int(item.get("failed_count") or 0) and not item.get("sources")
        for item in dedicated
    ):
        return "critical"
    if any(int(item.get("open_dead_letter_count") or 0) for item in dedicated):
        return "warning"
    # Free-form aggregate failure strings are only authoritative when no
    # structured source result survived.  When adapters reported exact
    # statuses, those statuses own severity: a partial adapter often also adds
    # a bounded diagnostic to ``failures`` and must remain a warning.
    if failures:
        return "warning" if structured_runs else "critical"
    return (
        "healthy"
        if any(status in {"ok", "not_modified"} for status in exact_statuses)
        else "unavailable"
    )


def _monitor_issue_is_source_health(issue: dict) -> bool:
    """Project only current source/adapter/dead-letter monitor findings.

    The monitor also reports cron, runtime-checkpoint, result-count and Metaso
    budget health.  Those remain valuable operational diagnostics, but they are
    not evidence that today's *sources* are critical.
    """

    code = str(issue.get("code") or "").strip().casefold()
    return (
        code.startswith("source_")
        or code.startswith("adapter_")
        or "dead_letter" in code
    )


def _bounded_generation_error(value: object) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if not text:
        return ""
    error_class = text.split(":", 1)[0].strip() or "DraftGenerationError"
    return f"{error_class[:80]}: draft generation was incomplete"


def _completion_status(
    report: dict,
    *,
    draft_status: str,
    analysis_status: str = "completed",
    health_report: dict | None = None,
) -> dict:
    severity = {"healthy": 0, "unavailable": 1, "warning": 2, "critical": 3}

    def normalized_health(value: object) -> str | None:
        status = str(value or "").strip().casefold()
        if status in {"ok", "not_modified", "healthy"}:
            return "healthy"
        if status in {"error", "critical"}:
            return "critical"
        if status in {"partial", "warning"}:
            return "warning"
        return status if status in severity else None

    source_summary = (report.get("manifest") or {}).get("source_summary") or {}
    warnings: list[dict] = []
    structured_runs = [
        run for run in source_summary.get("runs") or [] if isinstance(run, dict)
    ]
    failure_status = "warning" if structured_runs else "critical"
    for raw in source_summary.get("failures") or []:
        warnings.append(
            {"source_id": "pipeline", "status": failure_status, **safe_error(raw)}
        )
    for run in structured_runs:
        provider = str(run.get("source_id") or run.get("provider") or "source")
        outer_status = str(run.get("status") or "unavailable")
        if outer_status not in {"ok", "not_modified"}:
            raw_error = run.get("error")
            diagnostic = (
                safe_error(raw_error)
                if raw_error
                else {
                    "error_class": "SourceStatus",
                    "detail": f"source reported {outer_status}"[:240],
                }
            )
            warnings.append(
                {
                    "source_id": provider,
                    "status": outer_status,
                    **diagnostic,
                }
            )
        run_summary = run.get("run_summary") or {}
        source_runs = dict(run_summary.get("sources") or {})
        aggregate = run_summary.get("dedicated_aggregate") or {}
        source_runs.update((aggregate.get("sources") or {}))
        for source_id, source_run in source_runs.items():
            if not isinstance(source_run, dict):
                continue
            status = str(source_run.get("status") or "unavailable")
            if status not in {"ok", "not_modified"}:
                warnings.append(
                    {
                        "source_id": str(source_id),
                        "status": status,
                        **safe_error(source_run.get("error")),
                    }
                )
        if int(aggregate.get("failed_count") or 0) and not aggregate.get("sources"):
            warnings.append(
                {
                    "source_id": f"{provider}:dedicated-aggregate",
                    "status": "critical",
                    "error_class": "AggregateSourceFailure",
                    "detail": "one or more aggregate sources failed",
                }
            )
        if int(aggregate.get("open_dead_letter_count") or 0):
            warnings.append(
                {
                    "source_id": f"{provider}:dead-letter",
                    "status": "warning",
                    "error_class": "OpenDeadLetters",
                    "detail": "aggregate extraction has unresolved dead letters",
                }
            )
    monitor = health_report or {}
    for issue in monitor.get("issues") or []:
        if not isinstance(issue, dict) or not _monitor_issue_is_source_health(issue):
            continue
        warning = {
            "source_id": str((issue.get("details") or {}).get("source_id") or "monitor"),
            "status": normalized_health(issue.get("severity")) or "warning",
            "error_class": str(issue.get("code") or "monitor_issue")[:80],
            "detail": sanitize_text(issue.get("message"), limit=240),
        }
        warnings.append(warning)
    report_health = _source_health_status(report)
    issue_health = max(
        (
            normalized_health(item.get("status")) or "healthy"
            for item in warnings
        ),
        key=severity.__getitem__,
        default="healthy",
    )
    health = max([report_health, issue_health], key=severity.__getitem__)
    critical_issues = [
        item
        for item in warnings
        if str(item.get("status") or "") in {"critical", "error"}
    ]
    return {
        "analysis_status": analysis_status,
        "draft_generation_status": draft_status,
        "notification_status": "pending",
        "source_health_status": health,
        "critical_health_issues": critical_issues if health == "critical" else [],
        "source_warnings": warnings,
    }


def _final_report_opportunities(report: dict) -> list[dict]:
    output: list[dict] = []
    for lead in report.get("leads") or []:
        if not isinstance(lead, dict):
            continue
        evidence_urls = [
            str(item.get("source_url") or "").strip()
            for item in lead.get("evidence") or []
            if isinstance(item, dict) and str(item.get("source_url") or "").strip()
        ]
        output.append(
            {
                "company": str(lead.get("company") or "").strip(),
                "score": float(lead.get("score") or 0),
                "role_hypotheses": [
                    str(item).strip()
                    for item in lead.get("target_roles") or []
                    if str(item).strip()
                ],
                "evidence_urls": list(dict.fromkeys(evidence_urls)),
            }
        )
    return output


def _sanitize_bundle_diagnostics(bundle: dict) -> dict:
    output = dict(bundle)
    output["generation_error"] = _bounded_generation_error(
        output.get("generation_error")
    )
    output["completion_status"] = sanitize_tree(
        output.get("completion_status") or {}
    )
    analyses = []
    for raw in output.get("company_demand_analysis") or []:
        item = dict(raw) if isinstance(raw, dict) else {}
        if item.get("analysis_error"):
            item["analysis_error"] = safe_error_class(item["analysis_error"])
        analyses.append(item)
    output["company_demand_analysis"] = analyses
    return output


def _load_health_report(path: str | None) -> dict:
    if not path:
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unavailable", "issues": []}
    return dict(payload) if isinstance(payload, dict) else {"status": "unavailable"}


def _analysis_reference(report_path: Path | None) -> dict[str, str]:
    if report_path is None or not report_path.is_file():
        return {"path": "", "sha256": ""}
    return {
        "path": report_path.name,
        "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direction", required=True)
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--report-dir", default="reports-daily")
    parser.add_argument("--report")
    parser.add_argument("--output-dir", default="reports-daily/talent-pool")
    parser.add_argument("--state-db", default="data/talent-pool.sqlite")
    parser.add_argument("--target-count", type=int, default=5)
    parser.add_argument("--cooldown-days", type=int, default=7)
    parser.add_argument(
        "--analysis-status",
        choices=("completed", "partial", "failed", "not_run"),
        default="completed",
    )
    parser.add_argument("--health-report")
    parser.add_argument(
        "--disable-cooldown",
        action="store_true",
        help="generate from every report lead; intended only for isolated tests",
    )
    parser.add_argument(
        "--generator",
        choices=("direct-llm", "openclaw", "template"),
        default="direct-llm",
        help=(
            "direct-llm calls the provider API with OpenClaw-owned credentials; "
            "openclaw is a legacy alias; template is offline fallback"
        ),
    )
    parser.add_argument(
        "--allow-template-fallback",
        action="store_true",
        help="explicitly allow deterministic fallback when direct LLM generation fails",
    )
    parser.add_argument(
        "--force-regenerate",
        action="store_true",
        help="regenerate even when this exact source_run_id is already current",
    )
    parser.add_argument(
        "--record-analysis-failure",
        action="store_true",
        help="persist a zero-draft completion when the portfolio produced no report",
    )
    parser.add_argument(
        "--analysis-error-class",
        choices=("PortfolioWallClockTimeout", "PortfolioRunFailed"),
        default="PortfolioRunFailed",
    )
    parser.add_argument(
        "--record-draft-failure",
        action="store_true",
        help="persist a zero-draft completion after an outer generation watchdog",
    )
    parser.add_argument(
        "--draft-error-class",
        choices=("DraftGenerationWallClockTimeout",),
        default="DraftGenerationWallClockTimeout",
    )
    return parser


def _output_path(output_dir: str, run_date: str, direction: str) -> Path:
    direction_key = "".join(
        character if character.isalnum() else "-" for character in direction
    ).strip("-")
    return Path(output_dir) / f"talent-pool-{run_date}-{direction_key}.json"


def _draft_exit_code(bundle: dict) -> int:
    draft_status = str(
        ((bundle.get("completion_status") or {}).get("draft_generation_status"))
        or ""
    )
    if draft_status == "failed":
        return 71
    if draft_status == "partial" or bundle.get("generation_error"):
        return 72
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.record_analysis_failure:
            # This path intentionally does not inspect a possibly stale report.
            # It records the analysis failure itself so the completion hook and
            # later 05:50/06:50 reconciliation both have a current snapshot.
            date.fromisoformat(args.run_date)
            store = TalentPoolStore(args.state_db)
            bundle_payload = _analysis_failure_bundle(
                run_date=args.run_date,
                direction=args.direction,
                error_class=args.analysis_error_class,
            )
            output = _output_path(args.output_dir, args.run_date, args.direction)
            temporary_output = output.with_name(f".{output.name}.tmp-{os.getpid()}")
            temporary_output.parent.mkdir(parents=True, exist_ok=True)
            write_draft_bundle(bundle_payload, temporary_output)
            try:
                store.save_bundle(bundle_payload)
                os.replace(temporary_output, output)
            finally:
                temporary_output.unlink(missing_ok=True)
            print(
                json.dumps(
                    {
                        "status": "analysis_failed",
                        "error_class": args.analysis_error_class,
                        "draft_count": 0,
                        "completion_snapshot": "saved",
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.report:
            report_path = Path(args.report)
            report = json.loads(report_path.read_text(encoding="utf-8"))
        else:
            report_path, report = find_report(
                args.report_dir,
                run_date=args.run_date,
                direction=args.direction,
            )
            if report_path is None or report is None:
                raise FileNotFoundError("current Lead report not found")
        manifest = report.get("manifest") or {}
        if str(manifest.get("as_of") or "") != args.run_date:
            raise ValueError("report date does not match --run-date")
        if str(manifest.get("direction") or "") != args.direction:
            raise ValueError("report direction does not match --direction")
        if not str(manifest.get("run_id") or ""):
            raise ValueError("report manifest requires run_id for audit")
        store = TalentPoolStore(args.state_db)
        output = _output_path(args.output_dir, args.run_date, args.direction)
        if args.record_draft_failure:
            health_report = _load_health_report(args.health_report)
            failed = _failure_bundle(
                report,
                RuntimeError("draft generation watchdog expired"),
                report_path=report_path,
                analysis_status=args.analysis_status,
                health_report=health_report,
                error_class=args.draft_error_class,
            )
            temporary_output = output.with_name(f".{output.name}.tmp-{os.getpid()}")
            temporary_output.parent.mkdir(parents=True, exist_ok=True)
            write_draft_bundle(failed, temporary_output)
            try:
                store.save_bundle(failed)
                os.replace(temporary_output, output)
            finally:
                temporary_output.unlink(missing_ok=True)
            print(
                json.dumps(
                    {
                        "status": "draft_failed",
                        "error_class": args.draft_error_class,
                        "draft_count": 0,
                        "completion_snapshot": "saved",
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        existing = store.current_bundle(
            args.run_date,
            args.direction,
            source_run_id=str(manifest["run_id"]),
        )
        historical = store.historical_bundle_for_source_run(
            args.run_date, args.direction, str(manifest["run_id"])
        )
        if existing is None and historical is not None and not args.force_regenerate:
            print(
                "StaleSourceRunError: source_run_id was already generated but is no longer current; "
                "use --force-regenerate for an explicit replacement",
                file=sys.stderr,
            )
            return 74
        if existing is not None and not args.force_regenerate:
            snapshot_id = str(existing.pop("_snapshot_id", ""))
            temporary_output = output.with_name(f".{output.name}.tmp-{os.getpid()}")
            temporary_output.parent.mkdir(parents=True, exist_ok=True)
            try:
                write_draft_bundle(existing, temporary_output)
                os.replace(temporary_output, output)
            finally:
                temporary_output.unlink(missing_ok=True)
            exit_code = _draft_exit_code(existing)
            print(
                json.dumps(
                    {
                        "status": "reused",
                        "draft_count": len(existing.get("drafts") or []),
                        "output": str(output),
                        "source_report": str(report_path),
                        "snapshot_id": snapshot_id,
                    },
                    ensure_ascii=False,
                )
            )
            return exit_code
        health_report = _load_health_report(args.health_report)
        if not args.disable_cooldown and not bool(manifest.get("daily_cooldown_applied")):
            report = select_daily_opportunities(
                report,
                history_database=store.database,
                cooldown_days=args.cooldown_days,
            )
        try:
            if args.generator in {"direct-llm", "openclaw"}:
                bundle = generate_direct_talent_bundle(
                    report,
                    target_count=args.target_count,
                )
            else:
                bundle = generate_draft_bundle(report, target_count=args.target_count)
        except Exception:
            if args.generator not in {"direct-llm", "openclaw"} or not args.allow_template_fallback:
                raise
            bundle = generate_draft_bundle(report, target_count=args.target_count)
        bundle_payload = bundle.to_dict()
        bundle_payload["generation_error"] = _bounded_generation_error(
            bundle_payload.get("generation_error")
        )
        bundle_payload["completion_status"] = _completion_status(
            report,
            draft_status="partial" if bundle.generation_error else "complete",
            analysis_status=args.analysis_status,
            health_report=health_report,
        )
        bundle_payload["final_report_opportunities"] = _final_report_opportunities(report)
        bundle_payload["analysis_report"] = _analysis_reference(report_path)
        temporary_output = output.with_name(f".{output.name}.tmp-{os.getpid()}")
        temporary_output.parent.mkdir(parents=True, exist_ok=True)
        bundle_payload = _sanitize_bundle_diagnostics(bundle_payload)
        write_draft_bundle(bundle_payload, temporary_output)
        try:
            store.save_bundle(bundle_payload)
            os.replace(temporary_output, output)
        finally:
            temporary_output.unlink(missing_ok=True)
        print(
            json.dumps(
                {
                    "status": "partial" if bundle.generation_error else "ok",
                    "draft_count": len(bundle.drafts),
                    "output": str(output),
                    "source_report": str(report_path),
                    "generation_provider": bundle.generation_provider,
                    "generation_model": bundle.generation_model,
                    "generation_error": bundle_payload["generation_error"],
                },
                ensure_ascii=False,
            )
        )
        return 72 if bundle.generation_error else 0
    except Exception as error:
        # If analysis completed, do not turn a draft failure into an invisible
        # daily run.  Save a zero-draft completion snapshot for the hook/cron.
        if "report" in locals() and "store" in locals():
            try:
                failed = _failure_bundle(
                    report,
                    error,
                    report_path=locals().get("report_path"),
                    analysis_status=getattr(args, "analysis_status", "completed"),
                    health_report=locals().get("health_report", {}),
                )
                store.save_bundle(failed)
                print(
                    json.dumps(
                        sanitize_tree(
                            {
                                "status": "failed",
                                "error_class": safe_error_class(error),
                                "draft_count": 0,
                                "completion_snapshot": "saved",
                            }
                        ),
                        ensure_ascii=False,
                    )
                )
            except Exception:
                pass
        print(f"talent-pool generation failed: {safe_error_class(error)}", file=sys.stderr)
        return 71


if __name__ == "__main__":
    raise SystemExit(main())
