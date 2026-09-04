import importlib.util
import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from ht_lead_radar.talent_pool import generate_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore
from test_talent_pool import sample_report


SCRIPT = Path(__file__).parents[1] / "scripts" / "generate_talent_pool_drafts.py"
spec = importlib.util.spec_from_file_location("generate_talent_pool_drafts", SCRIPT)
assert spec and spec.loader
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def test_fatal_generation_persists_zero_draft_completion_and_safe_diagnostics(
    tmp_path, monkeypatch, capsys
):
    report = sample_report()
    report_path = tmp_path / "lead-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    health_path = tmp_path / "health.json"
    health_path.write_text(
        json.dumps(
            {
                "status": "critical",
                "issues": [
                    {
                        "code": "adapter_latest_nonhealthy",
                        "severity": "critical",
                        "message": "An adapter failed.",
                        "details": {"source_id": "36kr-finance"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    secret = "Bearer abcdefghijklmnop"

    def fail_generation(*_args, **_kwargs):
        raise RuntimeError(f"provider response authorization={secret}")

    monkeypatch.setattr(generator, "generate_direct_talent_bundle", fail_generation)
    database = tmp_path / "talent.sqlite"
    exit_code = generator.main(
        [
            "--direction",
            report["manifest"]["direction"],
            "--run-date",
            report["manifest"]["as_of"],
            "--report",
            str(report_path),
            "--state-db",
            str(database),
            "--output-dir",
            str(tmp_path / "talent-output"),
            "--analysis-status",
            "completed",
            "--health-report",
            str(health_path),
            "--disable-cooldown",
        ]
    )

    assert exit_code == 71
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert "RuntimeError" in captured.err
    bundle = TalentPoolStore(database).current_bundle(
        report["manifest"]["as_of"], report["manifest"]["direction"]
    )
    assert bundle is not None
    assert bundle["drafts"] == []
    assert len(bundle["final_report_opportunities"]) == len(report["leads"])
    assert bundle["completion_status"] == {
        "analysis_status": "completed",
        "draft_generation_status": "failed",
        "notification_status": "pending",
        "source_health_status": "critical",
        "critical_health_issues": [
            {
                "source_id": "36kr-finance",
                "status": "critical",
                "error_class": "adapter_latest_nonhealthy",
                "detail": "An adapter failed.",
            }
        ],
        "source_warnings": [
            {
                "source_id": "36kr-finance",
                "status": "critical",
                "error_class": "adapter_latest_nonhealthy",
                "detail": "An adapter failed.",
            }
        ],
    }
    assert bundle["analysis_report"]["path"] == report_path.name
    assert len(bundle["analysis_report"]["sha256"]) == 64
    assert secret.encode() not in database.read_bytes()


def test_portfolio_timeout_persists_analysis_failure_without_stale_report(tmp_path):
    database = tmp_path / "talent.sqlite"
    output_dir = tmp_path / "talent-output"

    exit_code = generator.main(
        [
            "--direction",
            "硬科技组合",
            "--run-date",
            "2026-08-31",
            "--report-dir",
            str(tmp_path / "missing-reports"),
            "--state-db",
            str(database),
            "--output-dir",
            str(output_dir),
            "--record-analysis-failure",
            "--analysis-error-class",
            "PortfolioWallClockTimeout",
        ]
    )

    assert exit_code == 0
    bundle = TalentPoolStore(database).current_bundle(
        "2026-08-31", "硬科技组合"
    )
    assert bundle is not None
    assert bundle["drafts"] == []
    assert bundle["analysis_error_class"] == "PortfolioWallClockTimeout"
    assert bundle["completion_status"]["analysis_status"] == "failed"
    assert bundle["completion_status"]["draft_generation_status"] == "not_run"
    assert bundle["completion_status"]["source_health_status"] == "critical"
    assert bundle["analysis_report"] == {"path": "", "sha256": ""}
    assert list(output_dir.glob("talent-pool-*.json"))


def test_outer_draft_timeout_persists_completed_analysis_and_failed_drafts(tmp_path):
    report = sample_report()
    report_path = tmp_path / "lead-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    database = tmp_path / "talent.sqlite"

    exit_code = generator.main(
        [
            "--direction",
            report["manifest"]["direction"],
            "--run-date",
            report["manifest"]["as_of"],
            "--report",
            str(report_path),
            "--state-db",
            str(database),
            "--output-dir",
            str(tmp_path / "talent-output"),
            "--analysis-status",
            "completed",
            "--record-draft-failure",
            "--draft-error-class",
            "DraftGenerationWallClockTimeout",
        ]
    )

    assert exit_code == 0
    bundle = TalentPoolStore(database).current_bundle(
        report["manifest"]["as_of"], report["manifest"]["direction"]
    )
    assert bundle is not None
    assert bundle["drafts"] == []
    assert bundle["completion_status"]["analysis_status"] == "completed"
    assert bundle["completion_status"]["draft_generation_status"] == "failed"
    assert bundle["generation_error"].startswith(
        "DraftGenerationWallClockTimeout:"
    )
    assert len(bundle["final_report_opportunities"]) == len(report["leads"])


def test_inner_adapter_partial_and_error_map_to_truthful_completion_health():
    report = sample_report()
    report["manifest"]["source_summary"] = {
        "runs": [
            {
                "provider": "reusable-source-packs",
                "status": "partial",
                "run_summary": {
                    "sources": {
                        "industry-source": {
                            "status": "partial",
                            "error": "AdapterWarning: token=must-be-redacted",
                        }
                    },
                    "dedicated_aggregate": {"sources": {}},
                },
            }
        ],
        "failures": [],
    }
    partial = generator._completion_status(report, draft_status="complete")
    assert partial["source_health_status"] == "warning"
    assert partial["critical_health_issues"] == []
    assert partial["source_warnings"][0]["status"] == "partial"
    assert "must-be-redacted" not in partial["source_warnings"][0]["detail"]

    report["manifest"]["source_summary"]["runs"][0]["status"] = "error"
    report["manifest"]["source_summary"]["runs"][0]["run_summary"]["sources"][
        "industry-source"
    ]["status"] = "error"
    failed = generator._completion_status(report, draft_status="complete")
    assert failed["source_health_status"] == "critical"
    assert {
        item["source_id"] for item in failed["critical_health_issues"]
    } >= {"reusable-source-packs", "industry-source"}


@pytest.mark.parametrize(
    ("report_status", "expected"),
    [
        ("healthy", "healthy"),
        ("warning", "warning"),
        ("critical", "critical"),
        ("unavailable", "unavailable"),
    ],
)
def test_completion_health_ignores_monitor_umbrella_status(
    report_status,
    expected,
):
    report = deepcopy(sample_report())
    source_summary = {"runs": [], "failures": []}
    if report_status == "healthy":
        source_summary["runs"] = [{"provider": "source", "status": "ok"}]
    elif report_status != "unavailable":
        adapter_status = "partial" if report_status == "warning" else "error"
        source_summary["runs"] = [
            {
                "provider": "source-pack",
                "status": adapter_status,
                "run_summary": {
                    "sources": {"adapter": {"status": adapter_status}},
                },
            }
        ]
    report["manifest"]["source_summary"] = source_summary

    completion = generator._completion_status(
        report,
        draft_status="complete",
        health_report={
            "status": "critical",
            "issues": [
                {
                    "code": "latest_run_failed",
                    "severity": "critical",
                    "message": "A historical runtime checkpoint failed.",
                    "details": {"run_id": "old-run"},
                },
                {
                    "code": "metaso_budget_exhausted",
                    "severity": "critical",
                    "message": "Budget exhausted.",
                    "details": {},
                },
            ],
        },
    )

    assert completion["source_health_status"] == expected
    assert completion["source_warnings"] == (
        []
        if report_status in {"healthy", "unavailable"}
        else completion["source_warnings"]
    )


def test_source_monitor_issue_still_controls_source_health():
    report = deepcopy(sample_report())
    report["manifest"]["source_summary"] = {
        "runs": [{"provider": "source", "status": "ok"}],
        "failures": [],
    }

    completion = generator._completion_status(
        report,
        draft_status="complete",
        health_report={
            "status": "critical",
            "issues": [
                {
                    "code": "adapter_latest_nonhealthy",
                    "severity": "critical",
                    "message": "Current adapter failed.",
                    "details": {"source_id": "36kr-finance"},
                }
            ],
        },
    )

    assert completion["source_health_status"] == "critical"
    assert completion["critical_health_issues"][0]["source_id"] == "36kr-finance"


def test_partial_adapter_failure_diagnostic_does_not_escalate_to_critical():
    report = deepcopy(sample_report())
    report["manifest"]["source_summary"] = {
        "runs": [
            {
                "provider": "legacy-fixed-sources",
                "status": "partial",
                "run_summary": {
                    "sources": {
                        "industry-source": {
                            "status": "partial",
                            "error": "DetailFetchError: Cookie: sid=private-value",
                        }
                    }
                },
            }
        ],
        "failures": ["industry-source: one detail failed"],
    }

    completion = generator._completion_status(report, draft_status="complete")

    assert completion["source_health_status"] == "warning"
    assert completion["critical_health_issues"] == []
    assert all(
        item["status"] in {"warning", "partial"}
        for item in completion["source_warnings"]
    )
    assert "private-value" not in json.dumps(completion, ensure_ascii=False)


def test_exact_source_run_is_reused_unless_force_regenerate(
    tmp_path,
    monkeypatch,
):
    report = sample_report()
    report_path = tmp_path / "lead-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    database = tmp_path / "talent.sqlite"
    calls = []

    def generate_once(value, *, target_count):
        calls.append((value["manifest"]["run_id"], target_count))
        return generate_draft_bundle(value, target_count=target_count)

    monkeypatch.setattr(generator, "generate_direct_talent_bundle", generate_once)
    arguments = [
        "--direction",
        report["manifest"]["direction"],
        "--run-date",
        report["manifest"]["as_of"],
        "--report",
        str(report_path),
        "--state-db",
        str(database),
        "--output-dir",
        str(tmp_path / "talent-output"),
        "--disable-cooldown",
    ]

    assert generator.main(arguments) == 0
    first = TalentPoolStore(database).current_bundle(
        report["manifest"]["as_of"], report["manifest"]["direction"]
    )
    assert first is not None
    with sqlite3.connect(database) as connection:
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM talent_pool_bundle_snapshots"
        ).fetchone()[0]
        report_count = connection.execute(
            "SELECT COUNT(*) FROM talent_pool_openclaw_reports"
        ).fetchone()[0]
    assert generator.main(arguments) == 0
    second = TalentPoolStore(database).current_bundle(
        report["manifest"]["as_of"], report["manifest"]["direction"]
    )
    assert second is not None
    assert second["_snapshot_id"] == first["_snapshot_id"]
    assert len(calls) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM talent_pool_bundle_snapshots"
        ).fetchone()[0] == snapshot_count
        assert connection.execute(
            "SELECT COUNT(*) FROM talent_pool_openclaw_reports"
        ).fetchone()[0] == report_count

    assert generator.main([*arguments, "--force-regenerate"]) == 0
    assert len(calls) == 2


def test_stale_source_run_a_b_a_is_rejected_without_llm_or_pointer_rollback(tmp_path, monkeypatch):
    report = sample_report()
    report_path = tmp_path / "report.json"
    database = tmp_path / "talent.sqlite"
    calls = []

    def generate(value, *, target_count):
        calls.append(value["manifest"]["run_id"])
        return generate_draft_bundle(value, target_count=target_count)

    monkeypatch.setattr(generator, "generate_direct_talent_bundle", generate)
    args = ["--direction", report["manifest"]["direction"], "--run-date", report["manifest"]["as_of"], "--report", str(report_path), "--state-db", str(database), "--output-dir", str(tmp_path / "out"), "--disable-cooldown"]
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    assert generator.main(args) == 0
    run_a = report["manifest"]["run_id"]
    report["manifest"]["run_id"] = "run-b"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    assert generator.main(args) == 0
    report["manifest"]["run_id"] = run_a
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    assert generator.main(args) == 74
    assert calls == [run_a, "run-b"]
    current = TalentPoolStore(database).current_bundle(report["manifest"]["as_of"], report["manifest"]["direction"])
    assert current["source_run_id"] == "run-b"
    assert generator.main([*args, "--force-regenerate"]) == 0
    assert calls == [run_a, "run-b", run_a]
