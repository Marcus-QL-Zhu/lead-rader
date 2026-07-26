#!/usr/bin/env python3
"""Apply an explicit approval command and optionally run serial publication."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ht_lead_radar.liepin_bridge import (
    ExternalLiepinPublisher,
    FakePublisher,
    publish_approved_serially,
)
from ht_lead_radar.talent_pool_store import TalentPoolStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--direction", required=True)
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--state-db", default="data/talent-pool.sqlite")
    parser.add_argument(
        "--context-snapshot-id",
        default="",
        help=argparse.SUPPRESS,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fake-publish", action="store_true")
    mode.add_argument("--execute-real", action="store_true")
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--liepin-root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = TalentPoolStore(args.state_db)
    try:
        command_result = store.apply_command(
            run_date=args.run_date,
            direction=args.direction,
            command=args.command,
            actor=args.actor,
            expected_snapshot_id=args.context_snapshot_id,
        )
        if command_result["action"] == "view":
            print(
                json.dumps(
                    command_result["draft"]["public_payload"],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        output: dict = {"command": command_result, "publication": []}
        if command_result["action"] == "publish" and (
            args.fake_publish or args.execute_real
        ):
            if args.fake_publish:
                publisher = FakePublisher()
            else:
                if not args.liepin_root:
                    raise ValueError("--liepin-root is required with --execute-real")
                root = Path(args.liepin_root)
                publisher = ExternalLiepinPublisher(
                    python_bin=args.python_bin,
                    publish_script=root
                    / "liepin-job-posting"
                    / "scripts"
                    / "publish_job.py",
                    posting_runtime_file=root
                    / "liepin-job-posting"
                    / "runtime"
                    / "job_postings.json",
                    orchestrate_script=root
                    / "liepin-full-pipeline"
                    / "scripts"
                    / "orchestrate.py",
                    execution_enabled=True,
                )
            output["publication"] = publish_approved_serially(
                store,
                run_date=args.run_date,
                direction=args.direction,
                publisher=publisher,
                draft_ids=command_result["draft_ids"],
            )
        elif command_result["action"] == "publish":
            output["note"] = (
                "drafts approved only; no publisher was invoked. "
                "Use --execute-real only after explicit user approval."
            )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(
            f"talent-pool command failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 72


if __name__ == "__main__":
    raise SystemExit(main())
