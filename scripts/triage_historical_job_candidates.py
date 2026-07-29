#!/usr/bin/env python3
"""Create a deterministic review queue from MetaSo discovery candidates."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from ht_lead_radar.historical_job_triage import triage_candidate


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--company-pool", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    input_path = Path(args.input)
    discovery = _read(input_path)
    pool = _read(Path(args.company_pool))
    aliases_by_company = {
        item["company"]: item.get("aliases", []) for item in pool["companies"]
    }
    queue: list[dict[str, Any]] = []
    for sample in discovery["samples"]:
        for result in sample["results"]:
            triage = triage_candidate(
                company=sample["company"], query=sample["query"],
                title=result["title"], snippet=result.get("snippet", ""),
                url=result["url"], published_at=result.get("published_at", ""),
                aliases=aliases_by_company.get(sample["company"], []),
            )
            queue.append({
                "company": sample["company"], "task_id": sample["task_id"],
                "title": result["title"], "url": result["url"],
                "published_at": result.get("published_at", ""),
                "result_sha256": result["result_sha256"], **triage,
            })
    priority_order = {"high": 0, "medium": 1, "low": 2}
    queue.sort(key=lambda item: (
        priority_order[str(item["review_priority"])],
        -int(item["triage_score"]), str(item["company"]), str(item["url"]),
    ))
    counts = Counter(str(item["review_priority"]) for item in queue)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "policy": (
            "Triage only. Search snippets cannot become labels. High and medium "
            "candidates require exact-page verification, China scope, title/date "
            "capture and a replayable artifact before dataset ingestion."
        ),
        "counts": {
            "total": len(queue), "high": counts["high"],
            "medium": counts["medium"], "low": counts["low"],
        },
        "queue": queue,
    }
    _write(Path(args.output), payload)
    print(json.dumps(payload["counts"], ensure_ascii=False))
    print(Path(args.output).resolve())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(f"historical candidate triage error: {exc}", file=sys.stderr)
        raise SystemExit(2)
