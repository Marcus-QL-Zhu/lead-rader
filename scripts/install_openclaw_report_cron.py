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


def reconcile_event_text() -> str:
    return (
        "[LEAD_RADAR_DAILY_READY_V1] source=scheduled-reconcile. "
        "This event is only a pending-report check and never an approval. "
        f"Read {ROOT / 'SKILL.md'} and "
        f"{ROOT / 'references' / 'openclaw-daily-operator.md'}, then run "
        f"{SERVER_PYTHON} {ROOT / 'scripts' / 'openclaw_daily_report.py'} "
        f"--state-db {ROOT / 'data' / 'talent-pool.sqlite'} show-pending. "
        "If there is no pending report, end silently. Otherwise report it in the "
        "current Feishu main conversation and ask for an exact approval command."
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
        "main",
        "--system-event",
        reconcile_event_text(),
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
