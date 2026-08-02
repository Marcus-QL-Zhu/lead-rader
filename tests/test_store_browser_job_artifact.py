from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

from scripts.store_browser_job_artifact import build_artifact, publication_interval


def _payload() -> dict[str, object]:
    return {
        "requested_url": "https://example.com/jobs/123",
        "final_url": "https://example.com/jobs/123",
        "captured_at": "2026-08-01T12:00:00+08:00",
        "source_platform": "example-jobs",
        "source_job_id": "123",
        "company": "\u661f\u6cb3\u79d1\u6280",
        "exact_title": "Commercial Director, China",
        "employer_display": "\u661f\u6cb3\u79d1\u6280",
        "raw_publication_text": "1\u5468\u524d",
        "location": "\u4e0a\u6d77",
        "main_text": "Lead the China commercial team and own market strategy.",
        "scope_quotes": ["Lead the China commercial team and own market strategy."],
    }


def test_relative_publication_interval_is_conservative() -> None:
    basis, start, end = publication_interval(
        "1\u5468\u524d",
        captured_at=datetime.fromisoformat("2026-08-01T12:00:00+08:00"),
    )

    assert basis == "relative_date_interval"
    assert start == "2026-07-18"
    assert end == "2026-07-26"


def test_browser_capture_creates_replayable_artifacts(tmp_path: Path) -> None:
    row = build_artifact(
        _payload(),
        artifact_root=tmp_path,
        output_dir=tmp_path / "jobs",
    )

    raw = tmp_path / row["raw_artifact_path"]
    normalized = tmp_path / row["normalized_text_path"]
    assert raw.is_file()
    assert normalized.is_file()
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == row["raw_artifact_sha256"]
    assert (
        hashlib.sha256(normalized.read_bytes()).hexdigest()
        == row["normalized_text_sha256"]
    )
    text = normalized.read_text(encoding="utf-8")
    for field in (
        "source_job_id_span",
        "title_span",
        "employer_span",
        "publication_span",
    ):
        span = row[field]
        assert text[span["char_start"] : span["char_end"]] == span["text"]
    assert row["evaluation_eligible"] is True
    assert row["review_status"] == "pending_human_review"
    assert json.loads(raw.read_text(encoding="utf-8"))["source_job_id"] == "123"




def test_general_manager_is_not_excluded_by_manager_rule(tmp_path: Path) -> None:
    payload = _payload()
    payload["exact_title"] = "General Manager, Greater China"
    payload["main_text"] = "Own the P&L and lead the Greater China organization."
    payload["scope_quotes"] = [payload["main_text"]]

    row = build_artifact(
        payload,
        artifact_root=tmp_path,
        output_dir=tmp_path / "jobs",
    )

    assert row["evaluation_eligible"] is True
    assert row["seniority_label"] == "director_plus"
