#!/usr/bin/env python3
"""Persist a public job-detail browser capture as replayable evidence.

The browser supplies public page text and identity fields as JSON on stdin.
This script creates content-addressed raw/normalized artifacts, exact spans,
and a review-queue row.  It never marks a row human-approved by default.
"""

from __future__ import annotations

import argparse
import base64
import calendar
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from typing import Any
import zlib


SCHEMA_VERSION = 1
EXTRACTOR_VERSION = "browser-public-job-v1"
DATE_PARSER_VERSION = "relative-publication-v1"
_DIRECTOR_PLUS = re.compile(
    r"(?:\bDirector\b|\bVice President\b|\bVP\b|\bGeneral Manager\b|"
    r"\bChief\b|\bC[A-Z]O\b|\bHead of\b|\u603b\u76d1|\u526f\u603b\u88c1|"
    r"\u603b\u7ecf\u7406|\u9996\u5e2d|\u8d1f\u8d23\u4eba)",
    re.I,
)
_EXCLUDED = re.compile(
    r"(?:\u7ecf\u7406|\u4e13\u5bb6|\u5de5\u7a0b\u5e08|\bManager\b|"
    r"\bPrincipal\b|\bStaff\b|Chief Engineer)",
    re.I,
)
_HEAD_SCOPE = re.compile(
    r"(?:\u56e2\u961f|\u7ec4\u7ec7|\u9884\u7b97|\u635f\u76ca|P&L|"
    r"\u4e1a\u52a1\u7ed3\u679c|\u6218\u7565|\u90e8\u95e8|\u4e8b\u4e1a\u90e8|"
    r"team|organization|budget|business result|strategy|department|"
    r"business unit|oversee|leadership|manage)",
    re.I,
)


def _digest(body: bytes) -> str:
    return sha256(body).hexdigest()


def _stable_id(prefix: str, value: str, *, size: int = 16) -> str:
    return prefix + sha256(value.encode("utf-8")).hexdigest()[:size]


def _capture_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _shift_month(value: date, months: int) -> date:
    ordinal = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(ordinal, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def publication_interval(
    raw_text: str,
    *,
    captured_at: datetime,
) -> tuple[str, str, str]:
    """Return basis and a conservative half-open publication interval."""

    text = raw_text.strip()
    explicit = re.search(
        r"(?P<year>20\d{2})[-/.\u5e74](?P<month>\d{1,2})[-/.\u6708]"
        r"(?P<day>\d{1,2})(?:\u65e5)?",
        text,
    )
    if explicit:
        value = date(
            int(explicit.group("year")),
            int(explicit.group("month")),
            int(explicit.group("day")),
        )
        return "explicit_date", value.isoformat(), (value + timedelta(days=1)).isoformat()

    capture_date = captured_at.date()
    match = re.search(
        r"(?P<count>\d+)\s*(?P<unit>\u5c0f\u65f6|\u5929|\u5468|\u4e2a\u6708|\u6708)\u524d",
        text,
    )
    if not match:
        if re.search(r"\u521a\u521a|\u4eca\u5929", text):
            return (
                "relative_date_interval",
                capture_date.isoformat(),
                (capture_date + timedelta(days=1)).isoformat(),
            )
        raise ValueError(f"unsupported publication phrase: {raw_text!r}")
    count = int(match.group("count"))
    unit = match.group("unit")
    if unit == "\u5c0f\u65f6":
        start = capture_date - timedelta(days=1)
        end = capture_date + timedelta(days=1)
    elif unit == "\u5929":
        start = capture_date - timedelta(days=count + 1)
        end = capture_date - timedelta(days=count) + timedelta(days=1)
    elif unit == "\u5468":
        start = capture_date - timedelta(weeks=count + 1)
        end = capture_date - timedelta(weeks=count) + timedelta(days=1)
    else:
        start = _shift_month(capture_date, -(count + 1))
        end = _shift_month(capture_date, -count) + timedelta(days=1)
    return "relative_date_interval", start.isoformat(), end.isoformat()


def _span(text: str, value: str, *, after: int = 0) -> dict[str, Any]:
    start = text.find(value, after)
    if start < 0:
        raise ValueError(f"evidence text not found in normalized artifact: {value[:80]!r}")
    return {"text": value, "char_start": start, "char_end": start + len(value)}


def _relative_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    base = root.resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError(f"artifact path escapes root: {path}")
    return resolved.relative_to(base).as_posix()


def _seniority(title: str, scope_text: str) -> tuple[str, bool, str]:
    if _EXCLUDED.search(title) and not re.search(r"\bGeneral Manager\b", title, re.I):
        return "excluded_below_director", False, "manager_expert_engineer_or_ic_title"
    if not _DIRECTOR_PLUS.search(title):
        return "unverified", False, "no_director_plus_title_marker"
    if re.search(r"\bHead(?:\s+of)?\b|\u8d1f\u8d23\u4eba", title, re.I) and not _HEAD_SCOPE.search(
        scope_text
    ):
        return "head_scope_unverified", False, "head_title_without_ownership_scope"
    return "director_plus", True, ""


def build_artifact(
    payload: dict[str, Any],
    *,
    artifact_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    required = (
        "requested_url",
        "final_url",
        "captured_at",
        "source_platform",
        "source_job_id",
        "company",
        "exact_title",
        "employer_display",
        "raw_publication_text",
        "main_text",
    )
    missing = [field for field in required if not str(payload.get(field) or "").strip()]
    if missing:
        raise ValueError("missing required browser fields: " + ", ".join(missing))
    for field in ("requested_url", "final_url"):
        if not re.fullmatch(r"https?://[^\s]+", str(payload[field]).strip()):
            raise ValueError(f"invalid {field}")

    captured_at = _capture_datetime(payload["captured_at"])
    publication_basis, interval_start, interval_end = publication_interval(
        str(payload["raw_publication_text"]),
        captured_at=captured_at,
    )
    source_platform = str(payload["source_platform"]).strip()
    source_job_id = str(payload["source_job_id"]).strip()
    exact_title = str(payload["exact_title"]).strip()
    employer = str(payload["employer_display"]).strip()
    publication_text = str(payload["raw_publication_text"]).strip()
    main_text = str(payload["main_text"]).replace("\r\n", "\n").replace("\r", "\n").strip()
    scope_quotes = [
        str(value).strip()
        for value in payload.get("scope_quotes") or []
        if str(value).strip()
    ]
    if not scope_quotes:
        raise ValueError("at least one exact scope quote is required")
    if any(value not in main_text for value in scope_quotes):
        raise ValueError("scope quote is not present verbatim in main_text")

    normalized = (
        f"SOURCE_JOB_ID\n{source_job_id}\n"
        f"TITLE\n{exact_title}\n"
        f"EMPLOYER\n{employer}\n"
        f"PUBLICATION\n{publication_text}\n"
        f"LOCATION\n{str(payload.get('location') or '').strip()}\n"
        f"CONTENT\n{main_text}\n"
    )
    scope_text = "\n".join(scope_quotes)
    seniority_label, eligible, exclusion_reason = _seniority(exact_title, scope_text)
    company = str(payload["company"]).strip()
    canonical_company_id = str(payload.get("canonical_company_id") or "").strip()
    if not canonical_company_id:
        canonical_company_id = _stable_id("co_", company.casefold())
    corporate_family_id = str(payload.get("corporate_family_id") or "").strip()
    if not corporate_family_id:
        corporate_family_id = _stable_id("fam_", company.casefold())
    artifact_id = _stable_id(
        "job_",
        f"{source_platform}\x1f{source_job_id}\x1f{str(payload['final_url']).strip()}",
    )
    destination = (output_dir / artifact_id).resolve()
    base = artifact_root.resolve()
    if destination != base and base not in destination.parents:
        raise ValueError("output_dir escapes artifact_root")
    destination.mkdir(parents=True, exist_ok=True)

    raw_record = dict(payload)
    raw_record["capture_schema_version"] = SCHEMA_VERSION
    raw_bytes = (json.dumps(raw_record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    normalized_bytes = normalized.encode("utf-8")
    raw_path = destination / "raw-browser-capture.json"
    text_path = destination / "normalized.txt"
    raw_path.write_bytes(raw_bytes)
    text_path.write_bytes(normalized_bytes)

    row = {
        "artifact_id": artifact_id,
        "source_platform": source_platform,
        "source_job_id": source_job_id,
        "requested_url": str(payload["requested_url"]).strip(),
        "final_url": str(payload["final_url"]).strip(),
        "captured_at": captured_at.isoformat(),
        "http_status": int(payload.get("http_status") or 200),
        "mime_type": str(payload.get("mime_type") or "text/html"),
        "raw_artifact_path": _relative_path(raw_path, base),
        "raw_artifact_sha256": _digest(raw_bytes),
        "normalized_text_path": _relative_path(text_path, base),
        "normalized_text_sha256": _digest(normalized_bytes),
        "extractor_version": EXTRACTOR_VERSION,
        "company": company,
        "canonical_company_id": canonical_company_id,
        "corporate_family_id": corporate_family_id,
        "employer_display": employer,
        "employer_match_basis": str(
            payload.get("employer_match_basis")
            or ("exact_display" if employer == company else "unverified_alias")
        ),
        "exact_title": exact_title,
        "title": exact_title,
        "raw_publication_text": publication_text,
        "publication_basis": publication_basis,
        "publication_interval_start": interval_start,
        "publication_interval_end_exclusive": interval_end,
        "date_parser_version": DATE_PARSER_VERSION,
        "timezone": str(payload.get("timezone") or "Asia/Shanghai"),
        "source_job_id_span": _span(normalized, source_job_id),
        "title_span": _span(normalized, exact_title),
        "employer_span": _span(normalized, employer),
        "publication_span": _span(normalized, publication_text),
        "scope_spans": [_span(normalized, value) for value in scope_quotes],
        "seniority_label": seniority_label,
        "seniority_rule_version": "director-plus-artifact-v1",
        "evaluation_eligible": eligible,
        "exclusion_reason": exclusion_reason,
        "reviewer": str(payload.get("reviewer") or "pending"),
        "reviewed_at": str(payload.get("reviewed_at") or captured_at.isoformat()),
        "review_status": str(payload.get("review_status") or "pending_human_review"),
    }
    row_path = destination / "job-row.json"
    row_path.write_text(
        json.dumps(row, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, default=Path("."))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("evaluation/training-v3/job-artifacts"),
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--gzip-input", type=Path)
    parser.add_argument("--base64-input")
    parser.add_argument("--gzip-base64-input")
    args = parser.parse_args()
    if args.gzip_input:
        raw = zlib.decompress(args.gzip_input.read_bytes(), 16 + zlib.MAX_WBITS).decode(
            "utf-8"
        )
    elif args.gzip_base64_input:
        raw = zlib.decompress(base64.b64decode(args.gzip_base64_input), 16 + zlib.MAX_WBITS).decode(
            "utf-8"
        )
    elif args.base64_input:
        raw = base64.b64decode(args.base64_input).decode("utf-8")
    elif args.input:
        raw = args.input.read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("browser capture must be one JSON object")
    row = build_artifact(
        payload,
        artifact_root=args.artifact_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
