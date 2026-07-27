#!/usr/bin/env python3
"""Apply an OpenClaw-interpreted action and optionally publish serially."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ht_lead_radar.liepin_bridge import (
    ExternalLiepinPublisher,
    FakePublisher,
    publish_approved_serially,
)
from ht_lead_radar.talent_pool_store import TalentPoolStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--action", choices=("view", "publish", "reject"), required=True
    )
    parser.add_argument(
        "--indexes",
        required=True,
        help="Displayed draft indexes chosen by OpenClaw, e.g. 1,3; use all explicitly",
    )
    parser.add_argument(
        "--user-message",
        required=True,
        help="Original inbound user text retained only for the approval audit",
    )
    parser.add_argument("--actor", required=True)
    parser.add_argument("--direction", required=True)
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--state-db", default="data/talent-pool.sqlite")
    parser.add_argument(
        "--context-snapshot-id",
        required=True,
        help=argparse.SUPPRESS,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fake-publish", action="store_true")
    mode.add_argument("--execute-real", action="store_true")
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--liepin-root")
    return parser


def _selected_indexes(raw: str, *, draft_count: int) -> tuple[int, ...]:
    if raw.strip().casefold() == "all":
        return tuple(range(1, draft_count + 1))
    if not raw.strip():
        raise ValueError("--indexes is required; use all explicitly for every draft")
    parts = [item for item in re.split(r"[,，\s]+", raw.strip()) if item]
    if not parts or any(not item.isdigit() for item in parts):
        raise ValueError("--indexes must contain displayed positive integers")
    indexes = tuple(int(item) for item in parts)
    if len(set(indexes)) != len(indexes) or any(
        index < 1 or index > draft_count for index in indexes
    ):
        raise ValueError("--indexes contains a duplicate or missing displayed draft")
    return indexes


def _canonical_command(action: str, indexes: tuple[int, ...], draft_count: int) -> str:
    selected = ",".join(str(index) for index in indexes)
    if action == "publish":
        return f"发布 {selected}"
    if action == "reject":
        return "跳过全部" if len(indexes) == draft_count else f"跳过 {selected}"
    raise ValueError(f"unsupported mutating action: {action}")


def _current_context(store: TalentPoolStore, args: argparse.Namespace) -> dict:
    current = store.latest_openclaw_context()
    if current is None:
        raise RuntimeError("no current Lead Radar report")
    if current["status"] != "reported":
        raise RuntimeError("current Lead Radar report has not been fully delivered")
    if (
        current["snapshot_id"] != args.context_snapshot_id
        or current["run_date"] != args.run_date
        or current["direction"] != args.direction
    ):
        raise RuntimeError("Lead Radar report context changed; show the latest report")
    return current


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = TalentPoolStore(args.state_db)
    try:
        current = _current_context(store, args)
        drafts = list(current["bundle"].get("drafts") or [])
        indexes = _selected_indexes(args.indexes, draft_count=len(drafts))
        if args.action == "view":
            result = {
                "action": "view",
                "snapshot_id": current["snapshot_id"],
                "drafts": [
                    {
                        "index": index,
                        "draft_id": drafts[index - 1]["draft_id"],
                        "recommended_title": drafts[index - 1]["recommended_title"],
                        "target_companies": [
                            lead.get("company")
                            for lead in drafts[index - 1].get("source_leads") or []
                            if lead.get("company")
                        ],
                        "job_posting_json": drafts[index - 1]["public_payload"],
                    }
                    for index in indexes
                ],
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        canonical = _canonical_command(args.action, indexes, len(drafts))
        command_result = store.apply_command(
            run_date=args.run_date,
            direction=args.direction,
            command=canonical,
            recorded_command=args.user_message,
            actor=args.actor,
            expected_snapshot_id=args.context_snapshot_id,
        )
        output: dict = {
            "interpreted_action": {
                "action": args.action,
                "indexes": list(indexes),
                "user_message": args.user_message,
            },
            "command": command_result,
            "publication": [],
        }
        if args.action == "publish" and (args.fake_publish or args.execute_real):
            if args.fake_publish:
                publisher = FakePublisher()
            else:
                if not args.liepin_root:
                    raise ValueError("--liepin-root is required with --execute-real")
                root = Path(args.liepin_root)
                publisher = ExternalLiepinPublisher(
                    python_bin=args.python_bin,
                    publish_script=(
                        root / "liepin-job-posting" / "scripts" / "publish_job.py"
                    ),
                    posting_runtime_file=(
                        root / "liepin-job-posting" / "runtime" / "job_postings.json"
                    ),
                    orchestrate_script=(
                        root / "liepin-full-pipeline" / "scripts" / "orchestrate.py"
                    ),
                    execution_enabled=True,
                )
            output["publication"] = publish_approved_serially(
                store,
                run_date=args.run_date,
                direction=args.direction,
                publisher=publisher,
                draft_ids=command_result["draft_ids"],
            )
        elif args.action == "publish":
            output["note"] = (
                "drafts approved only; no publisher was invoked. "
                "Use --execute-real only after OpenClaw identifies user approval."
            )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(
            f"talent-pool action failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 72


if __name__ == "__main__":
    raise SystemExit(main())
