#!/usr/bin/env python3
"""Turn raw web-search batches into an auditable historical-job review queue."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from ht_lead_radar.collectors import _event_date_from_text
from ht_lead_radar.historical_job_triage import triage_candidate
from ht_lead_radar.search_snapshot_dates import relative_publication_date

try:
    from scripts.collect_snapshot_news_candidates import _mentions_company
    from scripts.normalize_web_search_batches import _blocks
except ModuleNotFoundError:
    from collect_snapshot_news_candidates import _mentions_company
    from normalize_web_search_batches import _blocks


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_queue(
    source: dict[str, Any],
    *,
    aliases_by_company: dict[str, list[str]],
    window_start: str,
    window_end_exclusive: str,
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    query = (
        "historical Director+ job discovery "
        f"after:{window_start} before:{window_end_exclusive}"
    )
    for batch in source["batches"]:
        blocks = _blocks(batch["raw_result"])
        for company in batch["companies"]:
            for title, url, body in blocks:
                key = (company, url)
                search_text = f"{title} {body}".casefold()
                supplied_aliases = aliases_by_company.get(company, [])
                company_match = _mentions_company(company, title, body) or any(
                    alias.casefold() in search_text
                    for alias in supplied_aliases
                    if len(alias.strip()) >= 2
                )
                if key in seen or not company_match:
                    continue
                seen.add(key)
                publication_candidate = _event_date_from_text(
                    f"{title} {body}",
                    "",
                )
                publication_basis = "exact_date_in_search_result"
                if not publication_candidate:
                    publication_candidate, publication_basis = (
                        relative_publication_date(
                            body,
                            captured_at=batch["captured_at"],
                        )
                    )
                triage = triage_candidate(
                    company=company,
                    query=query,
                    title=title,
                    snippet=body,
                    url=url,
                    published_at=publication_candidate,
                    aliases=aliases_by_company.get(company, []),
                )
                digest = hashlib.sha256(
                    json.dumps(
                        {
                            "company": company,
                            "title": title,
                            "url": url,
                            "body": body,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                queue.append(
                    {
                        "company": company,
                        "title": title,
                        "url": url,
                        "search_excerpt": body[:1600],
                        "publication_date_candidate": publication_candidate,
                        "publication_date_basis": publication_basis,
                        "search_captured_at": batch["captured_at"],
                        "result_sha256": digest,
                        **triage,
                    }
                )
    priority_order = {"high": 0, "medium": 1, "low": 2}
    queue.sort(
        key=lambda item: (
            priority_order[str(item["review_priority"])],
            -int(item["triage_score"]),
            str(item["company"]),
            str(item["url"]),
        )
    )
    return queue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--company-pool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--company-aliases", type=Path)
    parser.add_argument("--window-start", default="2026-01-01")
    parser.add_argument("--window-end-exclusive", default="2026-07-01")
    args = parser.parse_args()

    source = _read(args.input)
    pool = _read(args.company_pool)
    aliases: dict[str, list[str]] = {
        row["company"]: list(row.get("aliases", []))
        for row in pool["companies"]
    }
    if args.company_aliases:
        alias_payload = _read(args.company_aliases)
        for company, values in alias_payload.get("aliases", {}).items():
            aliases.setdefault(company, []).extend(str(value) for value in values)
    queue = build_queue(
        source,
        aliases_by_company=aliases,
        window_start=args.window_start,
        window_end_exclusive=args.window_end_exclusive,
    )
    counts = Counter(str(item["review_priority"]) for item in queue)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "policy": (
            "Search results are review candidates only. A positive label requires "
            "an exact company-attributable job page, Director+ title, China scope, "
            "verified publication date, captured artifact and SHA-256."
        ),
        "window_start": args.window_start,
        "window_end_exclusive": args.window_end_exclusive,
        "counts": {
            "total": len(queue),
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "companies": len({item["company"] for item in queue}),
        },
        "queue": queue,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
