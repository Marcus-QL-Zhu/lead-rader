from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from ht_lead_radar.taxonomy import classify_seniority


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize a Codex in-app-browser Liepin export."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw_bytes = args.input.read_bytes()
    source = json.loads(raw_bytes.decode("utf-8"))
    observed_at = source.get("generated_at") or datetime.now().astimezone().isoformat()
    page_hash = sha256(raw_bytes).hexdigest()
    rows: list[dict[str, object]] = []
    for company in source["companies"]:
        jobs: list[dict[str, object]] = []
        for raw_job in company.get("jobs", []):
            title = str(raw_job.get("title", "")).strip()
            card_text = str(raw_job.get("card_text", "")).strip()
            seniority, eligible, scope_terms = classify_seniority(title, card_text)
            jobs.append(
                {
                    "company": company["company"],
                    "liepin_company_name": company.get("liepin_company_name", ""),
                    "title": title,
                    "card_text": card_text,
                    "job_url": raw_job["job_url"],
                    "source_kind": "liepin_company_guest_browser",
                    "company_page_url": company["company_page_url"],
                    "observed_at": observed_at,
                    "browser_export_sha256": page_hash,
                    "seniority_classification": seniority,
                    "eligible_director_plus": eligible,
                    "matched_scope_terms": scope_terms,
                }
            )
        rows.append(
            {
                "company": company["company"],
                "liepin_company_name": company.get("liepin_company_name", ""),
                "company_page_url": company["company_page_url"],
                "observed_at": observed_at,
                "status": "collected" if company.get("liepin_company_name") else "empty",
                "jobs": jobs,
            }
        )
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_policy": {
            "guest_only": True,
            "read_only": True,
            "publication_date_policy": (
                "observed_at is capture time; relative card dates are retained only "
                "inside card_text and are not converted to original publication dates"
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
    print(
        f"companies={len(rows)} jobs={sum(len(row['jobs']) for row in rows)} "
        f"director_plus={sum(job['eligible_director_plus'] for row in rows for job in row['jobs'])} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
