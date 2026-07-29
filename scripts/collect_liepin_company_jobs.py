from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import time

from ht_lead_radar.liepin_guest import collect_company


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect public Liepin company job pages as a guest."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--delay-seconds", type=float, default=3.0)
    parser.add_argument("--company", action="append", default=[])
    parser.add_argument("--skip-details", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    selected = set(args.company)
    rows: list[dict[str, object]] = []
    for entry in manifest["companies"]:
        company = str(entry["company"])
        if selected and company not in selected:
            continue
        url = entry.get("company_page_url")
        if not url:
            rows.append(
                {
                    "company": company,
                    "status": "missing_company_page",
                    "discovery_status": entry.get("discovery_status", "pending"),
                }
            )
            continue
        try:
            result = collect_company(
                company=company,
                company_page_url=str(url),
                fetch_director_details=not args.skip_details,
                delay_seconds=args.delay_seconds,
            )
            result["status"] = "collected"
            rows.append(result)
        except Exception as exc:  # noqa: BLE001 - retain per-company failures
            rows.append(
                {
                    "company": company,
                    "company_page_url": url,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if args.delay_seconds:
            time.sleep(args.delay_seconds)

    if not any(row["status"] == "collected" for row in rows) and any(
        row["status"] == "failed" for row in rows
    ):
        print(
            "No company page was collected; preserving any existing output because "
            "Liepin access appears blocked."
        )
        return 2

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_policy": {
            "guest_only": True,
            "read_only": True,
            "publication_date_policy": (
                "observed_at is capture time; displayed_update_text is retained verbatim; "
                "neither is treated as an original publication date"
            ),
            "director_plus_evaluation_only": True,
        },
        "companies": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    collected = sum(row["status"] == "collected" for row in rows)
    missing = sum(row["status"] == "missing_company_page" for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    jobs = sum(len(row.get("jobs", [])) for row in rows)
    director_jobs = sum(
        job.get("eligible_director_plus", False)
        for row in rows
        for job in row.get("jobs", [])
    )
    print(
        f"companies={len(rows)} collected={collected} missing={missing} failed={failed} "
        f"jobs={jobs} director_plus={director_jobs} output={args.output}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
