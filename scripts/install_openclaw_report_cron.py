#!/usr/bin/env python3
"""Idempotently install the two-time OpenClaw Lead Rader reconciliation cron."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

NAME = "lead-radar-daily-report-reconcile"
SCHEDULE = "50 5,6 * * *"
TIMEZONE = "Asia/Shanghai"
SERVER_PYTHON = "/home/admin/.pyenv/versions/3.11.14/bin/python3"
OPENCLAW_BIN = "/home/admin/.local/share/pnpm/openclaw"


def reconcile_message() -> str:
    command = (
        f"{SERVER_PYTHON} {ROOT / 'scripts' / 'openclaw_daily_report.py'} "
        f"--state-db {ROOT / 'data' / 'talent-pool.sqlite'} "
        f"wake --source scheduled-reconcile --openclaw-bin {OPENCLAW_BIN} "
        "--sessions-file /home/admin/.openclaw/agents/main/sessions/sessions.json"
    )
    return (
        "Lead Rader deterministic reconciliation. Run the command between the "
        "markers exactly once with the exec tool and return only its status.\n"
        "BEGIN_COMMAND\n"
        f"{command}\n"
        "END_COMMAND\n"
        "Do not inspect evidence, summarize a report, or approve/publish anything "
        "in this isolated cron session; the command wakes the current main Feishu session."
    )


def _json_from_output(output: str) -> dict[str, Any]:
    start = output.find("{")
    if start < 0:
        raise ValueError("OpenClaw cron list did not return JSON")
    value, _ = json.JSONDecoder().raw_decode(output[start:])
    return value


def desired_command(openclaw_bin: str, job_id: str = "") -> list[str]:
    command = (
        [openclaw_bin, "cron", "edit", job_id]
        if job_id
        else [openclaw_bin, "cron", "add"]
    )
    command += [
        "--name",
        NAME,
        "--description",
        "Lead Rader pending report check at 05:50 and 06:50 only",
        "--cron",
        SCHEDULE,
        "--tz",
        TIMEZONE,
        "--session",
        "isolated",
        "--message",
        reconcile_message(),
        "--no-deliver",
        "--wake",
        "now",
    ]
    if job_id:
        command.append("--enable")
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openclaw-bin", default=OPENCLAW_BIN)
    args = parser.parse_args(argv)
    listed = subprocess.run(
        [args.openclaw_bin, "cron", "list", "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        print(listed.stderr or listed.stdout, file=sys.stderr)
        return 74
    jobs = [
        item
        for item in _json_from_output(listed.stdout).get("jobs", [])
        if item.get("name") == NAME
    ]
    if len(jobs) > 1:
        print(
            f"refusing to edit {len(jobs)} duplicate jobs named {NAME}", file=sys.stderr
        )
        return 74
    job_id = str(jobs[0].get("id") or "") if jobs else ""
    completed = subprocess.run(
        desired_command(args.openclaw_bin, job_id),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        print(completed.stderr or completed.stdout, file=sys.stderr)
        return 74
    print(completed.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
