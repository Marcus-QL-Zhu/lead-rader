import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INNER = PROJECT_ROOT / "scripts" / "run_daily_fixed_sources_inner.sh"


pytestmark = pytest.mark.skipif(os.name == "nt", reason="requires /bin/sh")


def _prepare_launcher(tmp_path: Path) -> tuple[Path, Path, Path]:
    app = tmp_path / "hardtech-lead-radar"
    (app / "scripts").mkdir(parents=True)
    log = tmp_path / "calls.jsonl"
    python_wrapper = tmp_path / "test-python"
    python_wrapper.write_text(
        f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys
import time

args = sys.argv[1:]
name = Path(args[0]).name if args else ""
with open(os.environ["LAUNCHER_CALL_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps({{"name": name, "args": args}}, ensure_ascii=False) + "\\n")
if name == "run_daily_hardtech_portfolio.py":
    time.sleep(float(os.environ.get("PORTFOLIO_TEST_SLEEP", "0")))
if name == "generate_talent_pool_drafts.py" and "--record-draft-failure" not in args:
    time.sleep(float(os.environ.get("DRAFT_TEST_SLEEP", "0")))
if name == "send_daily_feishu_summary.py" and "--record-fallback-failure" not in args:
    time.sleep(float(os.environ.get("FALLBACK_TEST_SLEEP", "0")))
if args and args[0] == "-c":
    raise SystemExit(0)
codes = {{
    "run_daily_hardtech_portfolio.py": "ANALYSIS_EXIT",
    "run_lead_radar_v2.py": "MONITOR_EXIT",
    "generate_talent_pool_drafts.py": "DRAFT_EXIT",
    "openclaw_daily_report.py": "HOOK_EXIT",
    "send_daily_feishu_summary.py": "FALLBACK_EXIT",
}}
raise SystemExit(int(os.environ.get(codes.get(name, ""), "0")))
""",
        encoding="utf-8",
    )
    python_wrapper.chmod(0o700)
    # Instrument a private copy of the production inner launcher.  The real
    # launcher intentionally has no ambient PYTHON_BIN/HT override; replacing
    # its compile-time interpreter and capability consumer in this fixture is
    # test instrumentation, not a runtime configuration path.
    inner_text = INNER.read_text(encoding="utf-8")
    inner_text = inner_text.replace(
        'SERVER_PYTHON="/home/admin/.pyenv/versions/3.11.14/bin/python3"',
        f'SERVER_PYTHON="{python_wrapper}"',
    ).replace(
        '"$PYTHON_BIN" "$APP_DIR/deployment/consume_runtime_capability.py" || exit 64',
        ': # capability boundary is independently covered by secret-boundary tests',
    ).replace(
        'case "$APP_DIR" in\n  /home/admin/.openclaw/workspace/skills/hardtech-lead-radar) ;;',
        'case "$APP_DIR" in\n  */hardtech-lead-radar) ;;',
    )
    (app / "scripts" / INNER.name).write_text(inner_text, encoding="utf-8")
    (app / "scripts" / INNER.name).chmod(0o700)
    openclaw = tmp_path / "openclaw"
    openclaw.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    openclaw.chmod(0o700)
    return app, python_wrapper, log


@pytest.mark.parametrize(
    (
        "analysis_exit",
        "draft_exit",
        "hook_exit",
        "fallback_exit",
        "expected_exit",
        "expected_calls",
    ),
    [
        (0, 0, 0, 0, 0, {"portfolio", "monitor", "draft", "hook"}),
        (2, 0, 0, 0, 0, {"portfolio", "monitor", "draft", "hook"}),
        (0, 71, 0, 0, 71, {"portfolio", "monitor", "draft", "hook"}),
        (0, 0, 73, 0, 0, {"portfolio", "monitor", "draft", "hook", "fallback"}),
        (0, 72, 73, 0, 72, {"portfolio", "monitor", "draft", "hook", "fallback"}),
        (1, 0, 0, 0, 1, {"portfolio", "monitor", "draft", "hook"}),
    ],
)
def test_inner_launcher_completion_matrix(
    tmp_path,
    analysis_exit,
    draft_exit,
    hook_exit,
    fallback_exit,
    expected_exit,
    expected_calls,
):
    app, python_wrapper, log = _prepare_launcher(tmp_path)
    environment = dict(os.environ)
    environment.update(
        {
            "OPENCLAW_BIN": str(tmp_path / "openclaw"),
            "LAUNCHER_CALL_LOG": str(log),
            "ANALYSIS_EXIT": str(analysis_exit),
            "MONITOR_EXIT": "0",
            "DRAFT_EXIT": str(draft_exit),
            "HOOK_EXIT": str(hook_exit),
            "FALLBACK_EXIT": str(fallback_exit),
        }
    )

    completed = subprocess.run(
        ["/bin/sh", str(app / "scripts" / INNER.name)],
        cwd=app,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == expected_exit, completed.stderr
    names = [
        json.loads(line)["name"]
        for line in log.read_text(encoding="utf-8").splitlines()
    ]
    aliases = {
        "run_daily_hardtech_portfolio.py": "portfolio",
        "run_lead_radar_v2.py": "monitor",
        "generate_talent_pool_drafts.py": "draft",
        "openclaw_daily_report.py": "hook",
        "send_daily_feishu_summary.py": "fallback",
    }
    assert {aliases[name] for name in names if name in aliases} == expected_calls


def test_missing_openclaw_binary_records_preflight_failure_before_fallback(tmp_path):
    app, _python_wrapper, log = _prepare_launcher(tmp_path)
    missing_openclaw = tmp_path / "missing-openclaw"
    environment = dict(os.environ)
    environment.update(
        {
            "OPENCLAW_BIN": str(missing_openclaw),
            "LAUNCHER_CALL_LOG": str(log),
            "ANALYSIS_EXIT": "0",
            "MONITOR_EXIT": "0",
            "DRAFT_EXIT": "0",
            "FALLBACK_EXIT": "0",
        }
    )

    completed = subprocess.run(
        ["/bin/sh", str(app / "scripts" / INNER.name)],
        cwd=app,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    calls = [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
    ]
    bridge_calls = [
        call for call in calls if call["name"] == "openclaw_daily_report.py"
    ]
    assert len(bridge_calls) == 1
    assert "record-hook-preflight-failure" in bridge_calls[0]["args"]
    assert "OpenClawBinaryUnavailable" in bridge_calls[0]["args"]
    assert any(call["name"] == "send_daily_feishu_summary.py" for call in calls)


def test_portfolio_wall_clock_timeout_persists_completion_and_runs_hook(tmp_path):
    app, _python_wrapper, log = _prepare_launcher(tmp_path)
    inner = app / "scripts" / INNER.name
    text = inner.read_text(encoding="utf-8").replace(
        "PORTFOLIO_WALLCLOCK_SECONDS=1800",
        "PORTFOLIO_WALLCLOCK_SECONDS=0.05",
    ).replace(
        "PORTFOLIO_KILL_GRACE_SECONDS=15",
        "PORTFOLIO_KILL_GRACE_SECONDS=0.05",
    )
    inner.write_text(text, encoding="utf-8")
    environment = dict(os.environ)
    environment.update(
        {
            "OPENCLAW_BIN": str(tmp_path / "openclaw"),
            "LAUNCHER_CALL_LOG": str(log),
            "PORTFOLIO_TEST_SLEEP": "1",
            "MONITOR_EXIT": "0",
            "DRAFT_EXIT": "0",
            "HOOK_EXIT": "0",
            "FALLBACK_EXIT": "0",
        }
    )

    started = time.monotonic()
    completed = subprocess.run(
        ["/bin/sh", str(inner)],
        cwd=app,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )

    assert time.monotonic() - started < 1
    assert completed.returncode == 124, completed.stderr
    calls = [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
    ]
    names = [call["name"] for call in calls]
    assert names == [
        "run_daily_hardtech_portfolio.py",
        "run_lead_radar_v2.py",
        "generate_talent_pool_drafts.py",
        "openclaw_daily_report.py",
    ]
    failure_call = calls[2]["args"]
    assert "--record-analysis-failure" in failure_call
    assert "PortfolioWallClockTimeout" in failure_call


def test_draft_wall_clock_timeout_persists_failed_draft_completion(tmp_path):
    app, _python_wrapper, log = _prepare_launcher(tmp_path)
    inner = app / "scripts" / INNER.name
    text = inner.read_text(encoding="utf-8").replace(
        "DRAFT_WALLCLOCK_SECONDS=600",
        "DRAFT_WALLCLOCK_SECONDS=0.05",
    ).replace(
        "DRAFT_KILL_GRACE_SECONDS=15",
        "DRAFT_KILL_GRACE_SECONDS=0.05",
    )
    inner.write_text(text, encoding="utf-8")
    environment = dict(os.environ)
    environment.update(
        {
            "OPENCLAW_BIN": str(tmp_path / "openclaw"),
            "LAUNCHER_CALL_LOG": str(log),
            "DRAFT_TEST_SLEEP": "1",
            "ANALYSIS_EXIT": "0",
            "MONITOR_EXIT": "0",
            "DRAFT_EXIT": "0",
            "HOOK_EXIT": "0",
            "FALLBACK_EXIT": "0",
        }
    )

    started = time.monotonic()
    completed = subprocess.run(
        ["/bin/sh", str(inner)],
        cwd=app,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )

    assert time.monotonic() - started < 1
    assert completed.returncode == 71, completed.stderr
    calls = [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
    ]
    names = [call["name"] for call in calls]
    assert names == [
        "run_daily_hardtech_portfolio.py",
        "run_lead_radar_v2.py",
        "generate_talent_pool_drafts.py",
        "generate_talent_pool_drafts.py",
        "openclaw_daily_report.py",
    ]
    recovery_call = calls[3]["args"]
    assert "--record-draft-failure" in recovery_call
    assert "DraftGenerationWallClockTimeout" in recovery_call


def test_fallback_watchdog_records_failure_and_releases_daily_lock(tmp_path):
    app, _python_wrapper, log = _prepare_launcher(tmp_path)
    inner = app / "scripts" / INNER.name
    text = inner.read_text(encoding="utf-8").replace(
        "FALLBACK_WALLCLOCK_SECONDS=60",
        "FALLBACK_WALLCLOCK_SECONDS=0.05",
    ).replace(
        "FALLBACK_KILL_GRACE_SECONDS=5",
        "FALLBACK_KILL_GRACE_SECONDS=0.05",
    )
    inner.write_text(text, encoding="utf-8")
    environment = dict(os.environ)
    environment.update(
        {
            "OPENCLAW_BIN": str(tmp_path / "openclaw"),
            "LAUNCHER_CALL_LOG": str(log),
            "ANALYSIS_EXIT": "0",
            "MONITOR_EXIT": "0",
            "DRAFT_EXIT": "0",
            "HOOK_EXIT": "73",
            "FALLBACK_EXIT": "0",
            "FALLBACK_TEST_SLEEP": "1",
        }
    )

    first = subprocess.run(
        ["/bin/sh", str(inner)],
        cwd=app,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )
    assert first.returncode == 124, first.stderr

    environment["FALLBACK_TEST_SLEEP"] = "0"
    second = subprocess.run(
        ["/bin/sh", str(inner)],
        cwd=app,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )
    assert second.returncode == 0, second.stderr
    calls = [
        json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
    ]
    record_calls = [
        call
        for call in calls
        if call["name"] == "send_daily_feishu_summary.py"
        and "--record-fallback-failure" in call["args"]
    ]
    assert len(record_calls) == 1
    assert "FeishuFallbackWallClockTimeout" in record_calls[0]["args"]
