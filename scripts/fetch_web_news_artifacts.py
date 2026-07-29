#!/usr/bin/env python3
"""Capture high-quality news candidates and mark only defensible evidence ready."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import time
from typing import Any

from ht_lead_radar.collectors import _event_date_from_text

try:
    from scripts.collect_snapshot_news_candidates import (
        _fetch_artifact,
        _mentions_company,
    )
except ModuleNotFoundError:
    from collect_snapshot_news_candidates import _fetch_artifact, _mentions_company


def _date_confirmed(body: str, candidate: str) -> bool:
    try:
        parsed = date.fromisoformat(candidate[:10])
    except ValueError:
        return False
    needles = (
        parsed.isoformat(),
        f"{parsed.year}/{parsed.month}/{parsed.day}",
        f"{parsed.year}.{parsed.month}.{parsed.day}",
        f"{parsed.year}年{parsed.month}月{parsed.day}日",
    )
    if any(needle in body for needle in needles):
        return True
    return _event_date_from_text(body, "") == parsed.isoformat()


def assess_verification(
    *,
    company: str,
    title: str,
    body_text: str,
    event_date_candidate: str,
    source_grade: str,
    window_start: date,
    window_end: date,
) -> dict[str, Any]:
    company_confirmed = _mentions_company(company, title, body_text)
    date_confirmed = _date_confirmed(body_text, event_date_candidate)
    try:
        event_date = date.fromisoformat(event_date_candidate[:10])
    except ValueError:
        event_date = None
    in_window = bool(event_date and window_start <= event_date <= window_end)
    strict_ready = bool(
        company_confirmed
        and date_confirmed
        and in_window
        and source_grade in {"A", "B"}
    )
    return {
        "company_confirmed_in_artifact": company_confirmed,
        "publication_date_confirmed_in_artifact": date_confirmed,
        "within_window": in_window,
        "strict_evidence_ready": strict_ready,
        "verification_status": (
            "strict_evidence_ready" if strict_ready else "artifact_needs_review"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--max-artifacts", type=int, default=0)
    parser.add_argument("--grades", default="A,B")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    start = date.fromisoformat(payload["window_start"])
    end = date.fromisoformat(payload["window_end_inclusive"])
    grades = {value.strip() for value in args.grades.split(",") if value.strip()}
    attempted = 0
    for company_row in payload["companies"]:
        company = company_row["company"]
        for item in company_row["results"]:
            if item.get("verification_status") in {
                "strict_evidence_ready",
                "artifact_needs_review",
            }:
                continue
            if not item.get("within_window") or item.get("source_grade") not in grades:
                continue
            if args.max_artifacts and attempted >= args.max_artifacts:
                break
            attempted += 1
            try:
                artifact = _fetch_artifact(
                    item["source_url"],
                    artifact_dir=args.artifact_dir,
                    timeout_seconds=args.timeout_seconds,
                )
                body_text = str(artifact.pop("body_text"))
                item.update(artifact)
                item.update(
                    assess_verification(
                        company=company,
                        title=item["title"],
                        body_text=body_text,
                        event_date_candidate=item["event_date_candidate"],
                        source_grade=item["source_grade"],
                        window_start=start,
                        window_end=end,
                    )
                )
                item["artifact_captured_at"] = (
                    datetime.now().astimezone().isoformat(timespec="seconds")
                )
            except Exception as exc:  # noqa: BLE001 - retain per-source failure
                item["verification_status"] = "artifact_fetch_failed"
                item["artifact_error"] = f"{type(exc).__name__}: {exc}"
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if args.delay_seconds:
                time.sleep(args.delay_seconds)
        if args.max_artifacts and attempted >= args.max_artifacts:
            break
    print(
        json.dumps(
            {
                "attempted": attempted,
                "captured": sum(
                    bool(item.get("content_sha256"))
                    for row in payload["companies"]
                    for item in row["results"]
                ),
                "strict_ready": sum(
                    bool(item.get("strict_evidence_ready"))
                    for row in payload["companies"]
                    for item in row["results"]
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
