"""Shared, leakage-safe 90/180-day company evidence timeline packets."""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit


TIMELINE_VERSION = "company-timeline-v1"
RECENT_BUCKET = "days_0_90"
PRIOR_BUCKET = "days_91_180"
UNDATED_BUCKET = "undated"


def _parse_date(value: Any) -> date | None:
    match = re.search(r"(20\d{2})[-/年](\d{1,2})(?:[-/月](\d{1,2}))?", str(value or ""))
    if not match:
        return None
    try:
        return date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3) or 1),
        )
    except ValueError:
        return None


def _source_group(item: Mapping[str, Any]) -> str:
    explicit = str(item.get("independent_source_group") or item.get("source_group") or "").strip()
    if explicit:
        return explicit
    return urlsplit(str(item.get("source_url") or "")).netloc.casefold()


def _available_at(item: Mapping[str, Any]) -> date | None:
    values = [
        parsed
        for field in ("published_at", "observed_at")
        if (parsed := _parse_date(item.get(field))) is not None
    ]
    if values:
        return max(values)
    return _parse_date(item.get("event_date") or item.get("date"))


def _occurred_at(item: Mapping[str, Any]) -> date | None:
    return _parse_date(
        item.get("event_date")
        or item.get("date")
        or item.get("published_at")
    )


def _evidence_id(item: Mapping[str, Any]) -> str:
    existing = str(item.get("event_id") or item.get("evidence_id") or "").strip()
    if existing:
        return existing
    identity = "\x1f".join(
        str(item.get(key) or "").strip()
        for key in (
            "company",
            "source_url",
            "event_type",
            "event_date",
            "published_at",
            "title",
        )
    )
    return "ev_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _priority(item: Mapping[str, Any], position: int) -> tuple[Any, ...]:
    bucket = str(item["timeline_bucket"])
    bucket_score = {RECENT_BUCKET: 3, PRIOR_BUCKET: 2, UNDATED_BUCKET: 1}[bucket]
    grade = {"A": 3, "B": 2, "C": 1}.get(
        str(item.get("source_grade") or "").upper(),
        0,
    )
    upstream = 0 if str(item.get("event_type") or "") == "job_ad" else 1
    occurred = _parse_date(item.get("date"))
    return (-upstream, -bucket_score, -grade, -(occurred.toordinal() if occurred else 0), position)


def _timeline_record(
    item: Mapping[str, Any],
    *,
    effective_as_of: date,
    allow_undated: bool,
) -> dict[str, Any] | None:
    event_type = str(item.get("event_type") or "").strip()
    if event_type == "job_ad" or bool(item.get("is_recruiting_input")):
        return None
    occurred = _occurred_at(item)
    available = _available_at(item)
    if available is not None and available > effective_as_of:
        return None
    if occurred is None:
        if not allow_undated:
            return None
        bucket = UNDATED_BUCKET
    else:
        age_days = (effective_as_of - occurred).days
        if age_days < 0:
            return None
        if age_days <= 90:
            bucket = RECENT_BUCKET
        elif age_days <= 180:
            bucket = PRIOR_BUCKET
        else:
            return None
    source_url = str(item.get("source_url") or "")
    fact = str(
        item.get("source_excerpt")
        or item.get("snippet")
        or item.get("fact")
        or ""
    )[:800]
    return {
        "evidence_id": _evidence_id(item),
        "date": occurred.isoformat() if occurred else "",
        "published_at": str(item.get("published_at") or ""),
        "observed_at": str(item.get("observed_at") or ""),
        "available_at": available.isoformat() if available else "",
        "timeline_bucket": bucket,
        "event_type": event_type,
        "phase": item.get("phase"),
        "source_grade": item.get("source_grade"),
        "title": item.get("title"),
        "fact": fact,
        "source_url": source_url,
        "source_locator": item.get("source_locator") or "",
        "source_group": _source_group(item),
        "content_sha256": str(item.get("content_sha256") or ""),
        "people": list(item.get("people") or []),
        "organizations": list(item.get("organizations") or []),
        "late_validation_only": False,
    }


def build_company_timeline(
    evidence: Iterable[Mapping[str, Any]],
    *,
    as_of: str | date,
    limit: int = 8,
    allow_undated: bool = False,
) -> dict[str, Any]:
    """Build one deterministic timeline shared by production and backtests."""

    effective_as_of = as_of if isinstance(as_of, date) else date.fromisoformat(as_of)
    if limit < 1:
        raise ValueError("timeline limit must be positive")
    deduplicated: dict[str, tuple[int, dict[str, Any]]] = {}
    input_count = 0
    for position, raw in enumerate(evidence):
        input_count += 1
        record = _timeline_record(
            raw,
            effective_as_of=effective_as_of,
            allow_undated=allow_undated,
        )
        if record is None:
            continue
        evidence_id = str(record["evidence_id"])
        current = deduplicated.get(evidence_id)
        if current is None or _priority(record, position) < _priority(
            current[1],
            current[0],
        ):
            deduplicated[evidence_id] = (position, record)
    ranked = sorted(
        deduplicated.values(),
        key=lambda pair: _priority(pair[1], pair[0]),
    )
    by_stratum: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    for pair in ranked:
        key = (
            str(pair[1]["timeline_bucket"]),
            str(pair[1].get("event_type") or "unknown"),
        )
        by_stratum.setdefault(key, []).append(pair)
    selected: list[tuple[int, dict[str, Any]]] = []
    for key in sorted(
        by_stratum,
        key=lambda value: _priority(by_stratum[value][0][1], by_stratum[value][0][0]),
    ):
        selected.append(by_stratum[key].pop(0))
        if len(selected) >= limit:
            break
    remaining = [pair for values in by_stratum.values() for pair in values]
    selected.extend(
        sorted(remaining, key=lambda pair: _priority(pair[1], pair[0]))[
            : max(limit - len(selected), 0)
        ]
    )
    records = [record for _, record in selected[:limit]]
    buckets = {
        bucket: [record for record in records if record["timeline_bucket"] == bucket]
        for bucket in (RECENT_BUCKET, PRIOR_BUCKET, UNDATED_BUCKET)
    }
    hash_payload = {
        "timeline_version": TIMELINE_VERSION,
        "as_of": effective_as_of.isoformat(),
        "evidence": records,
    }
    timeline_hash = hashlib.sha256(
        json.dumps(
            hash_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "timeline_version": TIMELINE_VERSION,
        "as_of": effective_as_of.isoformat(),
        "window_start_exclusive": (
            effective_as_of - timedelta(days=181)
        ).isoformat(),
        "recent_window_days": 90,
        "extended_window_days": 180,
        "input_evidence_count": input_count,
        "eligible_evidence_count": len(deduplicated),
        "selected_evidence_count": len(records),
        "omitted_evidence_count": max(len(deduplicated) - len(records), 0),
        "has_undated_evidence": bool(buckets[UNDATED_BUCKET]),
        "buckets": buckets,
        "evidence": records,
        "timeline_sha256": timeline_hash,
    }


__all__ = [
    "PRIOR_BUCKET",
    "RECENT_BUCKET",
    "TIMELINE_VERSION",
    "UNDATED_BUCKET",
    "build_company_timeline",
]
