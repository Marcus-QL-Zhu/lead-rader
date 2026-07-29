from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re

from ht_lead_radar.collectors import _event_date_from_text, grade_source, infer_event
try:
    from scripts.collect_snapshot_news_candidates import _mentions_company
except ModuleNotFoundError:
    from collect_snapshot_news_candidates import _mentions_company


RESULT_START = re.compile(r"(?m)^(.+?) \((https?://[^)\s]+)\)\s*$")


def _blocks(value: str) -> list[tuple[str, str, str]]:
    matches = list(RESULT_START.finditer(value))
    output: list[tuple[str, str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        body = value[match.end():end].strip()
        output.append((match.group(1).strip(), match.group(2), body))
    return output


def _within(value: str, start: date, end: date) -> bool:
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return False
    return start <= parsed <= end


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end-inclusive", default="2026-06-30")
    args = parser.parse_args()
    source = json.loads(args.input.read_text(encoding="utf-8"))
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end_inclusive)
    companies: dict[str, dict] = {}
    for batch in source["batches"]:
        for company in batch["companies"]:
            entry = companies.setdefault(
                company,
                {"company": company, "results": [], "seen_urls": set()},
            )
            for title, url, body in _blocks(batch["raw_result"]):
                if url in entry["seen_urls"]:
                    continue
                if not _mentions_company(company, title, body):
                    continue
                entry["seen_urls"].add(url)
                text = f"{title} {body}"
                event_date = _event_date_from_text(text, "")
                event_type, phase = infer_event(text)
                entry["results"].append(
                    {
                        "title": title,
                        "source_url": url,
                        "source_grade": grade_source(url),
                        "event_date_candidate": event_date,
                        "within_window": _within(event_date, start, end),
                        "event_type": event_type,
                        "phase": phase,
                        "search_excerpt": body[:1200],
                        "search_captured_at": batch["captured_at"],
                        "verification_status": "search_candidate",
                    }
                )
    rows = []
    for entry in companies.values():
        entry.pop("seen_urls")
        rows.append(entry)
    payload = {
        "schema_version": 1,
        "window_start": args.start,
        "window_end_inclusive": args.end_inclusive,
        "strict_policy": (
            "Search candidates are not training evidence until the source page "
            "is captured and its publication date is verified."
        ),
        "companies": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "companies": len(rows),
                "candidates": sum(len(row["results"]) for row in rows),
                "within_window": sum(
                    result["within_window"]
                    for row in rows
                    for result in row["results"]
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
