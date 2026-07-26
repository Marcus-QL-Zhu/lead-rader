#!/usr/bin/env python3
"""Generate and persist today's talent-pool drafts from a Lead report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ht_lead_radar.feishu_notify import find_report
from ht_lead_radar.openclaw_talent_generator import generate_openclaw_draft_bundle
from ht_lead_radar.talent_pool import generate_draft_bundle, write_draft_bundle
from ht_lead_radar.talent_pool_store import TalentPoolStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direction", required=True)
    parser.add_argument("--run-date", default=date.today().isoformat())
    parser.add_argument("--report-dir", default="reports-daily")
    parser.add_argument("--report")
    parser.add_argument("--output-dir", default="reports-daily/talent-pool")
    parser.add_argument("--state-db", default="data/talent-pool.sqlite")
    parser.add_argument("--target-count", type=int, default=5)
    parser.add_argument(
        "--generator",
        choices=("openclaw", "template"),
        default="openclaw",
        help="openclaw uses the configured main Agent API; template is offline fallback",
    )
    parser.add_argument(
        "--allow-template-fallback",
        action="store_true",
        help="explicitly allow deterministic fallback when OpenClaw generation fails",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
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
        try:
            if args.generator == "openclaw":
                bundle = generate_openclaw_draft_bundle(
                    report,
                    target_count=args.target_count,
                )
            else:
                bundle = generate_draft_bundle(report, target_count=args.target_count)
        except Exception:
            if args.generator != "openclaw" or not args.allow_template_fallback:
                raise
            bundle = generate_draft_bundle(report, target_count=args.target_count)
        direction_key = "".join(
            character if character.isalnum() else "-"
            for character in args.direction
        ).strip("-")
        output = (
            Path(args.output_dir)
            / f"talent-pool-{args.run_date}-{direction_key}.json"
        )
        write_draft_bundle(bundle, output)
        TalentPoolStore(args.state_db).save_bundle(bundle.to_dict())
        print(
            json.dumps(
                {
                    "status": "ok",
                    "draft_count": len(bundle.drafts),
                    "output": str(output),
                    "source_report": str(report_path),
                    "generation_provider": bundle.generation_provider,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as error:
        print(
            f"talent-pool generation failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 71


if __name__ == "__main__":
    raise SystemExit(main())
