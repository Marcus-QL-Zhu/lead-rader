#!/usr/bin/env python3
"""Merge append-only snapshot discovery files without changing their evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def merge_news(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    companies: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        for row in payload["companies"]:
            target = companies.setdefault(
                row["company"],
                {key: value for key, value in row.items() if key != "results"},
            )
            existing = {
                item["source_url"]
                for item in target.setdefault("results", [])
            }
            target["results"].extend(
                item
                for item in row["results"]
                if item["source_url"] not in existing
            )
    return {
        "schema_version": 1,
        "window_start": payloads[0]["window_start"],
        "window_end_inclusive": payloads[0]["window_end_inclusive"],
        "merge_policy": "Deduplicate news candidates by company and source URL.",
        "companies": list(companies.values()),
    }


def merge_jobs(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    queue: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for payload in payloads:
        for item in payload["queue"]:
            key = (item["company"], item["url"])
            if key in seen:
                continue
            seen.add(key)
            queue.append(item)
    return {
        "schema_version": 1,
        "merge_policy": "Deduplicate job candidates by company and URL.",
        "queue": queue,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("news", "jobs"), required=True)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in args.input
    ]
    result = merge_news(payloads) if args.kind == "news" else merge_jobs(payloads)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    row_key = "companies" if args.kind == "news" else "queue"
    print(json.dumps({row_key: len(result[row_key])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
