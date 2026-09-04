#!/usr/bin/env python3
"""Idempotently install the two-time OpenClaw Lead Rader reconciliation cron."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT / "src"))

NAME = "lead-radar-daily-report-reconcile"
SCHEDULE = "50 5,6 * * *"
TIMEZONE = "Asia/Shanghai"
SERVER_PYTHON = "/home/admin/.pyenv/versions/3.11.14/bin/python3"
OPENCLAW_BIN = "/home/admin/.local/share/pnpm/openclaw"
PRODUCTION_STABLE_ROOT = Path(
    "/home/admin/.openclaw/workspace/skills/hardtech-lead-radar"
)
PRODUCTION_RELEASES_ROOT = Path(
    "/home/admin/.openclaw/workspace/skills/hardtech-lead-radar-releases"
)
_EXACT_SHA = re.compile(r"^[0-9a-f]{40}$")


def cron_project_root(
    script_file: str | Path = __file__,
    *,
    stable_root: str | Path = PRODUCTION_STABLE_ROOT,
    releases_root: str | Path = PRODUCTION_RELEASES_ROOT,
) -> Path:
    """Return a durable cron root without dereferencing the stable symlink.

    A path through either the production stable symlink or an exact-SHA release
    maps to the stable path.  A development checkout or legacy directory
    outside the versioned layout keeps its lexical root, for use only through
    the installer's explicit ``--project-root`` override.
    """

    script = Path(os.path.abspath(os.fspath(script_file)))
    lexical_root = script.parent.parent
    stable = Path(os.path.abspath(os.fspath(stable_root)))
    releases = Path(os.path.abspath(os.fspath(releases_root)))
    if lexical_root == stable:
        return stable
    if lexical_root.parent == releases and _EXACT_SHA.fullmatch(lexical_root.name):
        return stable

    # Some Python launch mechanisms canonicalize __file__ before this module
    # sees it.  Resolution is used only to classify a production release; the
    # resolved exact-SHA path is never written to cron.
    resolved_root = script.resolve().parent.parent
    if resolved_root.parent == releases and _EXACT_SHA.fullmatch(resolved_root.name):
        return stable
    return lexical_root


def reconcile_message(*, project_root: str | Path | None = None) -> str:
    root = (
        Path(project_root)
        if project_root is not None
        else PRODUCTION_STABLE_ROOT
    )
    command = " ".join(
        shlex.quote(os.fspath(part))
        for part in (
            SERVER_PYTHON,
            "-B",
            root / "scripts" / "openclaw_daily_report.py",
            "--state-db",
            root / "data" / "talent-pool.sqlite",
            "wake",
            "--source",
            "scheduled-reconcile",
            "--openclaw-bin",
            OPENCLAW_BIN,
            "--sessions-file",
            "/home/admin/.openclaw/agents/main/sessions/sessions.json",
        )
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


def desired_command(
    openclaw_bin: str,
    job_id: str = "",
    *,
    project_root: str | Path | None = None,
) -> list[str]:
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
        reconcile_message(project_root=project_root),
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
    parser.add_argument(
        "--project-root",
        type=Path,
        help=(
            "lexical checkout root for local/legacy installation; exact-SHA "
            "production release roots are always canonicalized to the stable symlink"
        ),
    )
    args = parser.parse_args(argv)
    project_root = (
        cron_project_root(args.project_root / "scripts" / Path(__file__).name)
        if args.project_root is not None
        else PRODUCTION_STABLE_ROOT
    )
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
        desired_command(args.openclaw_bin, job_id, project_root=project_root),
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
