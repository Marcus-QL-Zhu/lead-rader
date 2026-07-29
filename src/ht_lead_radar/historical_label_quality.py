"""Evidence-quality checks for future-job labels used by historical replay."""

from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
from typing import Any, Mapping

from .taxonomy import classify_seniority


_PROTOCOL_LEVELS = {
    "v20-conservative-correction": 1,
    "v21-source-backed-seniority": 1,
    "v22-bounded-relative-dates": 2,
    "v23-employer-scope-date-complete": 3,
}


def verify_historical_job_labels(
    bundle: Mapping[str, Any],
    *,
    window_start: str,
    window_end_exclusive: str,
    artifact_root: str | Path = '.',
) -> bool:
    """Reject labels whose seniority, date, or employer evidence is incomplete."""

    protocol = str(bundle.get("label_quality_protocol") or "").strip()
    level = _PROTOCOL_LEVELS.get(protocol)
    if level is None:
        raise ValueError(f"unknown label quality protocol: {protocol or '<missing>'}")
    jobs = bundle.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("historical job bundle must contain a jobs list")
    start = date.fromisoformat(window_start)
    end = date.fromisoformat(window_end_exclusive)
    for index, value in enumerate(jobs):
        if not isinstance(value, Mapping):
            raise ValueError(f"historical job {index} must be an object")
        title = str(value.get("title") or value.get("exact_title") or "").strip()
        scope = str(value.get("scope_evidence") or "").strip()
        if not scope:
            raise ValueError(f"historical job lacks source-backed scope: {title}")
        source_excerpt = str(value.get("source_excerpt") or "").strip()
        artifact_path = str(value.get("source_artifact_path") or "").strip()
        expected_sha = str(value.get("source_artifact_sha256") or "").strip()
        if not source_excerpt or not artifact_path or not expected_sha:
            raise ValueError(f"historical job lacks replayable source artifact: {title}")
        artifact = Path(artifact_root) / artifact_path
        if not artifact.is_file():
            raise ValueError(f"historical job source artifact missing: {title}")
        payload = artifact.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise ValueError(f"historical job source artifact hash mismatch: {title}")
        if source_excerpt not in payload.decode("utf-8", errors="replace"):
            raise ValueError(f"historical job excerpt absent from artifact: {title}")
        if not classify_seniority(title, scope)[1]:
            raise ValueError(f"historical job is not source-backed Director+: {title}")
        if level >= 2:
            interval_start = date.fromisoformat(
                str(value.get("publication_interval_start") or "")
            )
            interval_end = date.fromisoformat(
                str(value.get("publication_interval_end_exclusive") or "")
            )
            if not start <= interval_start < interval_end <= end:
                raise ValueError(
                    f"historical job date interval is not wholly in window: {title}"
                )
        if level >= 3 and not str(value.get("employer_evidence") or "").strip():
            raise ValueError(f"historical job lacks employer evidence: {title}")
    return True
