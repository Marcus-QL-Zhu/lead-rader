#!/usr/bin/env python3
"""Assemble leakage-safe evidence and outcome labels for one simulated cutoff."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any


EXCLUDED_TITLE_TERMS = (
    "经理",
    "专家",
    "工程师",
    "manager",
    "expert",
    "engineer",
)
DIRECTOR_TERMS = (
    "总监",
    "副总裁",
    "总裁",
    "负责人",
    "首席",
    "director",
    "head of",
    "vice president",
    " vp ",
    "chief",
    "country manager",
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _eligible_title(title: str) -> bool:
    text = re.sub(r"\s+", " ", title.casefold())
    if any(term in text for term in EXCLUDED_TITLE_TERMS) and not any(
        term in text for term in DIRECTOR_TERMS
    ):
        return False
    return any(term in f" {text} " for term in DIRECTOR_TERMS)


def build_snapshot(
    *,
    pool: dict[str, Any],
    news: dict[str, Any],
    jobs: dict[str, Any],
    cutoff: date,
    horizon_end: date,
) -> dict[str, Any]:
    split_by_company = {
        row["company"]: row["split"]
        for row in pool["companies"]
    }
    evidence: list[dict[str, Any]] = []
    for company_row in news["companies"]:
        company = company_row["company"]
        if company not in split_by_company:
            continue
        for item in company_row["results"]:
            event_date = item.get("event_date_candidate", "")
            if not item.get("strict_evidence_ready") or not event_date:
                continue
            if date.fromisoformat(event_date[:10]) > cutoff:
                continue
            evidence.append(
                {
                    "company": company,
                    "split": split_by_company[company],
                    "event_date": event_date[:10],
                    "event_type": item["event_type"],
                    "title": item["title"],
                    "source_url": item["source_url"],
                    "source_grade": item["source_grade"],
                    "content_sha256": item["content_sha256"],
                    "storage_path": item["storage_path"],
                }
            )
    labels: list[dict[str, Any]] = []
    seen_urls: set[tuple[str, str]] = set()
    for item in jobs["queue"]:
        company = item["company"]
        key = (company, item["url"])
        published = item.get("publication_date_candidate", "")
        if (
            company not in split_by_company
            or key in seen_urls
            or item.get("review_priority") != "high"
            or not item.get("direct_job_page")
            or not _eligible_title(item["title"])
            or not published
        ):
            continue
        published_date = date.fromisoformat(published[:10])
        if not cutoff < published_date <= horizon_end:
            continue
        seen_urls.add(key)
        labels.append(
            {
                "company": company,
                "split": split_by_company[company],
                "title": item["title"],
                "published_at_estimate": published,
                "publication_date_basis": item.get("publication_date_basis", ""),
                "source_url": item["url"],
                "result_sha256": item["result_sha256"],
                "label_status": "verified_search_snapshot_candidate",
            }
        )
    evidence_companies = {row["company"] for row in evidence}
    label_companies = {row["company"] for row in labels}
    split_counts = {}
    for split in ("train", "calibration", "test"):
        split_counts[split] = {
            "pool_companies": sum(
                value == split for value in split_by_company.values()
            ),
            "companies_with_strict_precursor_evidence": len(
                {
                    row["company"]
                    for row in evidence
                    if row["split"] == split
                }
            ),
            "companies_with_positive_job_candidate": len(
                {
                    row["company"]
                    for row in labels
                    if row["split"] == split
                }
            ),
        }
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "simulated_cutoff": cutoff.isoformat(),
        "horizon_end_inclusive": horizon_end.isoformat(),
        "leakage_policy": (
            "Only strict non-recruiting evidence dated on or before cutoff enters "
            "prediction inputs. Job results are outcome labels only and are never "
            "included in model evidence."
        ),
        "label_policy": (
            "This snapshot accepts only direct Director+ job-page search captures "
            "inside the horizon. Relative dates remain explicitly estimated and "
            "require page-level archiving before final strict-label promotion."
        ),
        "counts": {
            "evidence_rows": len(evidence),
            "evidence_companies": len(evidence_companies),
            "positive_label_candidates": len(labels),
            "positive_label_companies": len(label_companies),
            "event_types": len({row["event_type"] for row in evidence}),
            "by_split": split_counts,
        },
        "evidence": evidence,
        "job_label_candidates": labels,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--news", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cutoff", default="2026-04-30")
    parser.add_argument("--horizon-end", default="2026-07-31")
    args = parser.parse_args()
    payload = build_snapshot(
        pool=_read(args.pool),
        news=_read(args.news),
        jobs=_read(args.jobs),
        cutoff=date.fromisoformat(args.cutoff),
        horizon_end=date.fromisoformat(args.horizon_end),
    )
    payload["source_hashes"] = {
        "pool": hashlib.sha256(args.pool.read_bytes()).hexdigest(),
        "news": hashlib.sha256(args.news.read_bytes()).hexdigest(),
        "jobs": hashlib.sha256(args.jobs.read_bytes()).hexdigest(),
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
