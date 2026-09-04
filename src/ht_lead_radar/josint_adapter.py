from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from .josint_snapshot import readonly_sqlite_snapshot


class _SnapshotConnection:
    def __init__(self, db_path: str | Path):
        self._snapshot = readonly_sqlite_snapshot(db_path)
        path = self._snapshot.__enter__()
        try:
            self._connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
            self._connection.execute("PRAGMA query_only=ON")
            self._connection.row_factory = sqlite3.Row
        except BaseException:
            self._snapshot.__exit__(*sys.exc_info())
            raise

    def __getattr__(self, name: str):
        return getattr(self._connection, name)

    def close(self) -> None:
        if getattr(self, "_connection", None) is not None:
            self._connection.close()
            self._connection = None
            self._snapshot.__exit__(None, None, None)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def open_readonly_josint(db_path: str | Path) -> _SnapshotConnection:
    """Open JOSINT without create/write privileges and enforce query-only SQL."""

    return _SnapshotConnection(db_path)


def read_canonical_evidence(
    db_path: str | Path,
    *,
    terms: tuple[str, ...] | list[str],
    direction: str,
) -> list[dict] | None:
    """Read JOSINT v2 canonical targets; return None for a legacy-only database."""
    with open_readonly_josint(db_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_jobs'"
        ).fetchone()
        if not exists:
            return None
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(canonical_jobs)")}
        required = {
            "canonical_job_id", "title", "company_name",
            "guessed_employer", "location", "jd_text", "industry_label",
            "function_label", "target_reason", "first_seen_at",
            "last_seen_at", "source_urls_json", "is_target_job",
        }
        if not required.issubset(columns):
            return None
        rows = connection.execute(
            """
            SELECT canonical_job_id, title, company_name, guessed_employer, location,
                   jd_text, industry_label, function_label, target_reason,
                   first_seen_at, last_seen_at, source_urls_json
            FROM canonical_jobs
            WHERE is_target_job=1
            ORDER BY last_seen_at DESC, title
            """
        ).fetchall()

    output: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        searchable = " ".join(str(row[key] or "") for key in row.keys()).lower()
        if not any(term.lower() in searchable for term in terms):
            continue
        company = str(row["guessed_employer"] or row["company_name"] or "").strip()
        if not company:
            continue
        canonical_id = str(row["canonical_job_id"])
        if canonical_id in seen:
            continue
        seen.add(canonical_id)
        try:
            urls = json.loads(row["source_urls_json"] or "[]")
        except json.JSONDecodeError:
            urls = []
        source_url = next((str(url) for url in urls if url), f"josint://{canonical_id}")
        output.append({
            "company": company,
            "event_type": "job_ad",
            "phase": "marketed_competitive",
            "event_date": str(row["last_seen_at"] or row["first_seen_at"] or ""),
            "title": str(row["title"] or ""),
            "snippet": str(row["jd_text"] or row["target_reason"] or "")[:500],
            "source_url": source_url,
            "source_name": "JOSINT Canonical",
            "source_grade": "C",
            "direction": direction,
        })
    return output
