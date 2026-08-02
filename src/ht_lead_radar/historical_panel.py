"""Leakage-safe monthly company snapshots for historical hiring evaluation."""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .company_timeline import build_company_timeline
from .taxonomy import classify_seniority


PANEL_VERSION = "historical-company-panel-v2"
_TEST_EXCLUDED_TITLE = re.compile(
    r"(?:经理|专家|工程师|\bManager\b|\bPrincipal\b|\bStaff\b|Chief Engineer)",
    re.I,
)
_HEAD_SCOPE = re.compile(
    r"(?:团队|组织|预算|损益|P&L|业务结果|战略|部门|事业部)",
    re.I,
)


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _artifact_bytes(
    root: Path,
    relative: Any,
    expected_sha256: Any,
) -> bytes | None:
    path_value = str(relative or "").strip()
    expected = str(expected_sha256 or "").strip().lower()
    if not path_value or len(expected) != 64:
        return None
    root_resolved = root.resolve()
    artifact = (root_resolved / path_value).resolve()
    if artifact != root_resolved and root_resolved not in artifact.parents:
        return None
    if not artifact.is_file():
        return None
    body = artifact.read_bytes()
    return body if hashlib.sha256(body).hexdigest() == expected else None


def _span_is_exact(span: Any, text: str, *, expected: str = "") -> bool:
    if not isinstance(span, Mapping):
        return False
    try:
        start = int(span.get("char_start"))
        end = int(span.get("char_end"))
    except (TypeError, ValueError):
        return False
    quote = str(span.get("text") or "")
    return (
        start >= 0
        and end > start
        and end <= len(text)
        and text[start:end] == quote
        and (not expected or quote == expected)
    )


def _artifact_is_replayable(row: Mapping[str, Any], root: Path) -> bool:
    raw = _artifact_bytes(
        root,
        row.get("raw_artifact_path"),
        row.get("raw_artifact_sha256"),
    )
    normalized = _artifact_bytes(
        root,
        row.get("normalized_text_path"),
        row.get("normalized_text_sha256"),
    )
    if raw is None or normalized is None:
        return False
    normalized_text = normalized.decode("utf-8", errors="strict")
    exact_title = str(row.get("exact_title") or "").strip()
    employer = str(row.get("employer_display") or "").strip()
    publication_text = str(row.get("raw_publication_text") or "").strip()
    source_job_id = str(row.get("source_job_id") or "").strip()
    scopes = row.get("scope_spans")
    return (
        bool(raw)
        and _span_is_exact(row.get("title_span"), normalized_text, expected=exact_title)
        and _span_is_exact(
            row.get("employer_span"),
            normalized_text,
            expected=employer,
        )
        and _span_is_exact(
            row.get("publication_span"),
            normalized_text,
            expected=publication_text,
        )
        and _span_is_exact(
            row.get("source_job_id_span"),
            normalized_text,
            expected=source_job_id,
        )
        and isinstance(scopes, list)
        and bool(scopes)
        and all(_span_is_exact(span, normalized_text) for span in scopes)
    )


def _http_url(value: Any) -> bool:
    return bool(re.fullmatch(r"https?://[^\s]+", str(value or "").strip()))


def _coverage_artifact_is_replayable(
    row: Mapping[str, Any],
    root: Path,
) -> bool:
    raw = _artifact_bytes(
        root,
        row.get("raw_artifact_path"),
        row.get("raw_artifact_sha256"),
    )
    normalized = _artifact_bytes(
        root,
        row.get("normalized_text_path"),
        row.get("normalized_text_sha256"),
    )
    spans = row.get("coverage_evidence_spans")
    if raw is None or normalized is None or not isinstance(spans, list) or not spans:
        return False
    text = normalized.decode("utf-8", errors="strict")
    return all(_span_is_exact(span, text) for span in spans)


def _job_row_is_strict(
    raw: Mapping[str, Any],
    *,
    artifact_root: Path,
    company_ids: Mapping[str, str],
) -> bool:
    company = str(raw.get("company") or "").strip()
    title = str(raw.get("exact_title") or "").strip()
    canonical_company_id = str(raw.get("canonical_company_id") or "").strip()
    scope_spans = raw.get("scope_spans")
    scope = " ".join(
        str(span.get("text") or "")
        for span in scope_spans
        if isinstance(span, Mapping)
    ) if isinstance(scope_spans, list) else ""
    interval_start = _date(raw.get("publication_interval_start"))
    interval_end = _date(raw.get("publication_interval_end_exclusive"))
    required_strings = (
        "artifact_id",
        "source_platform",
        "source_job_id",
        "requested_url",
        "final_url",
        "captured_at",
        "mime_type",
        "extractor_version",
        "employer_display",
        "corporate_family_id",
        "employer_match_basis",
        "raw_publication_text",
        "publication_basis",
        "date_parser_version",
        "timezone",
        "seniority_label",
        "seniority_rule_version",
        "reviewer",
        "reviewed_at",
    )
    if any(not str(raw.get(field) or "").strip() for field in required_strings):
        return False
    if (
        raw.get("evaluation_eligible") is not True
        or raw.get("review_status") != "approved"
        or int(raw.get("http_status") or 0) != 200
        or not _http_url(raw.get("requested_url"))
        or not _http_url(raw.get("final_url"))
        or company_ids.get(company) != canonical_company_id
        or (
            str(raw.get("employer_display") or "").strip() != company
            and raw.get("employer_match_basis") != "verified_relationship_artifact"
        )
        or raw.get("publication_basis")
        not in {"explicit_date", "relative_date_interval"}
        or not interval_start
        or not interval_end
        or interval_start >= interval_end
        or (
            _TEST_EXCLUDED_TITLE.search(title)
            and not re.search(r"\bGeneral Manager\b", title, re.I)
        )
        or not classify_seniority(title, scope)[1]
        or (
            re.search(r"\bHead\b|负责人", title, re.I)
            and not _HEAD_SCOPE.search(scope)
        )
        or not _artifact_is_replayable(raw, artifact_root)
    ):
        return False
    return True


def _strict_job_rows(
    jobs: Mapping[str, Any],
    *,
    artifact_root: Path,
    company_ids: Mapping[str, str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in jobs.get("jobs") or jobs.get("queue") or []:
        if not isinstance(raw, Mapping):
            continue
        if not _job_row_is_strict(
            raw,
            artifact_root=artifact_root,
            company_ids=company_ids,
        ):
            continue
        interval_start = _date(raw.get("publication_interval_start"))
        interval_end = _date(raw.get("publication_interval_end_exclusive"))
        assert interval_start is not None and interval_end is not None
        output.append(
            {
                **dict(raw),
                "publication_interval_start": interval_start.isoformat(),
                "publication_interval_end_exclusive": interval_end.isoformat(),
            }
        )
    return output


def _negative_coverage(
    jobs: Mapping[str, Any],
    *,
    company: str,
    horizon_start: date,
    horizon_end_exclusive: date,
    artifact_root: Path,
) -> bool:
    for raw in jobs.get("coverage_snapshots") or []:
        if not isinstance(raw, Mapping) or raw.get("company") != company:
            continue
        start = _date(raw.get("coverage_start"))
        end = _date(raw.get("coverage_end_exclusive"))
        covered_dates = {
            _date(value)
            for value in raw.get("covered_dates") or []
        }
        required_dates = {
            horizon_start + timedelta(days=offset)
            for offset in range((horizon_end_exclusive - horizon_start).days)
        }
        if (
            raw.get("coverage_basis") == "daily_complete_director_plus_snapshots"
            and raw.get("complete_director_plus_listing") is True
            and start is not None
            and end is not None
            and start <= horizon_start
            and end >= horizon_end_exclusive
            and required_dates.issubset(covered_dates)
            and _coverage_artifact_is_replayable(raw, artifact_root)
        ):
            return True
    return False


def _news_by_company(news: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for company_row in news.get("companies") or []:
        if not isinstance(company_row, Mapping):
            continue
        company = str(company_row.get("company") or "").strip()
        for raw in company_row.get("results") or []:
            if not isinstance(raw, Mapping) or not raw.get("strict_evidence_ready"):
                continue
            event_date = _date(raw.get("event_date_candidate"))
            if event_date is None:
                continue
            output.setdefault(company, []).append(
                {
                    "company": company,
                    "event_type": str(raw.get("event_type") or ""),
                    "phase": str(raw.get("phase") or ""),
                    "event_date": event_date.isoformat(),
                    "published_at": event_date.isoformat(),
                    "title": str(raw.get("title") or ""),
                    "source_excerpt": str(raw.get("search_excerpt") or ""),
                    "source_url": str(raw.get("source_url") or ""),
                    "source_grade": str(raw.get("source_grade") or ""),
                    "content_sha256": str(raw.get("content_sha256") or ""),
                    "source_locator": str(raw.get("storage_path") or ""),
                }
            )
    return output


def build_historical_panel(
    *,
    pool: Mapping[str, Any],
    news: Mapping[str, Any],
    jobs: Mapping[str, Any],
    cutoffs: Iterable[date],
    artifact_root: str | Path = ".",
    horizon_days: int = 90,
    timeline_limit: int = 8,
) -> dict[str, Any]:
    """Cut each company into monthly samples while keeping company splits fixed."""

    if horizon_days < 1:
        raise ValueError("horizon_days must be positive")
    root = Path(artifact_root)
    companies = [
        dict(row)
        for row in pool.get("companies") or []
        if isinstance(row, Mapping) and row.get("company") and row.get("split")
    ]
    company_ids = {
        str(row["company"]): str(row.get("canonical_company_id") or "")
        for row in companies
    }
    split_by_family: dict[str, set[str]] = {}
    for row in companies:
        family = str(
            row.get("corporate_family_id")
            or row.get("canonical_company_id")
            or row["company"]
        )
        split_by_family.setdefault(family, set()).add(str(row["split"]))
    leaking = sorted(
        family for family, splits in split_by_family.items() if len(splits) > 1
    )
    if leaking:
        raise ValueError(
            "corporate family crosses dataset splits: " + ", ".join(leaking)
        )
    evidence_by_company = _news_by_company(news)
    strict_jobs = _strict_job_rows(
        jobs,
        artifact_root=root,
        company_ids=company_ids,
    )
    identities: dict[tuple[str, str], str] = {}
    for row in strict_jobs:
        company = str(row.get("canonical_company_id") or "")
        for key in (
            ("url", str(row.get("final_url") or "").strip()),
            (
                "job_id",
                f"{row.get('source_platform')}:{row.get('source_job_id')}",
            ),
        ):
            prior = identities.setdefault(key, company)
            if prior != company:
                raise ValueError(
                    f"job artifact identity crosses companies: {key[0]}={key[1]}"
                )
    jobs_by_company: dict[str, list[dict[str, Any]]] = {}
    for row in strict_jobs:
        jobs_by_company.setdefault(str(row.get("company") or ""), []).append(row)
    rows: list[dict[str, Any]] = []
    for cutoff in sorted(set(cutoffs)):
        horizon_start = cutoff + timedelta(days=1)
        horizon_end_exclusive = horizon_start + timedelta(days=horizon_days)
        for company_row in companies:
            company = str(company_row["company"])
            timeline = build_company_timeline(
                evidence_by_company.get(company, ()),
                as_of=cutoff,
                limit=timeline_limit,
                allow_undated=False,
            )
            matching_jobs = []
            ambiguous_overlap = False
            for job in jobs_by_company.get(company, ()):
                interval_start = date.fromisoformat(job["publication_interval_start"])
                interval_end = date.fromisoformat(
                    job["publication_interval_end_exclusive"]
                )
                if horizon_start <= interval_start < interval_end <= horizon_end_exclusive:
                    matching_jobs.append(job)
                elif interval_start < horizon_end_exclusive and interval_end > horizon_start:
                    ambiguous_overlap = True
            if matching_jobs:
                label = "positive"
            elif ambiguous_overlap:
                label = "unknown"
            elif _negative_coverage(
                jobs,
                company=company,
                horizon_start=horizon_start,
                horizon_end_exclusive=horizon_end_exclusive,
                artifact_root=root,
            ):
                label = "negative"
            else:
                label = "unknown"
            identity = f"{company}\x1f{cutoff.isoformat()}\x1f{PANEL_VERSION}"
            rows.append(
                {
                    "sample_id": "hs_" + hashlib.sha256(
                        identity.encode("utf-8")
                    ).hexdigest()[:16],
                    "company": company,
                    "company_type": str(company_row.get("company_type") or ""),
                    "sector": str(company_row.get("sector") or ""),
                    "split": str(company_row["split"]),
                    "cutoff": cutoff.isoformat(),
                    "horizon_start": horizon_start.isoformat(),
                    "horizon_end_exclusive": horizon_end_exclusive.isoformat(),
                    "timeline": timeline,
                    "label": label,
                    "job_outcomes": [
                        {
                            "title": job.get("title") or job.get("exact_title"),
                            "source_url": job.get("source_url") or job.get("url"),
                            "publication_interval_start": job[
                                "publication_interval_start"
                            ],
                            "publication_interval_end_exclusive": job[
                                "publication_interval_end_exclusive"
                            ],
                            "source_artifact_path": job.get("source_artifact_path"),
                            "source_artifact_sha256": job.get(
                                "source_artifact_sha256"
                            ),
                        }
                        for job in matching_jobs
                    ],
                }
            )
    digest_payload = {
        "panel_version": PANEL_VERSION,
        "horizon_days": horizon_days,
        "rows": rows,
    }
    panel_sha256 = hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    counts = {
        "samples": len(rows),
        "positive_samples": sum(row["label"] == "positive" for row in rows),
        "negative_samples": sum(row["label"] == "negative" for row in rows),
        "unknown_samples": sum(row["label"] == "unknown" for row in rows),
        "companies": len({row["company"] for row in rows}),
        "positive_companies": len(
            {row["company"] for row in rows if row["label"] == "positive"}
        ),
        "by_split": {
            split: {
                "samples": sum(row["split"] == split for row in rows),
                "positive_samples": sum(
                    row["split"] == split and row["label"] == "positive"
                    for row in rows
                ),
                "negative_samples": sum(
                    row["split"] == split and row["label"] == "negative"
                    for row in rows
                ),
            }
            for split in ("train", "calibration", "test")
        },
    }
    return {
        "schema_version": 1,
        "panel_version": PANEL_VERSION,
        "horizon_days": horizon_days,
        "split_policy": "company-disjoint; all monthly samples inherit company split",
        "leakage_policy": (
            "Prediction input contains only non-recruiting evidence published on or "
            "before cutoff. Job artifacts appear only in the future outcome block."
        ),
        "counts": counts,
        "rows": rows,
        "panel_sha256": panel_sha256,
    }


__all__ = ["PANEL_VERSION", "build_historical_panel"]
