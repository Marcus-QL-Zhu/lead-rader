from __future__ import annotations

from datetime import date, timedelta
import hashlib

import pytest

from ht_lead_radar.historical_panel import (
    _job_row_is_strict,
    build_historical_panel,
)


def _strict_job_fixture(
    tmp_path,
    *,
    company="星河科技",
    canonical_company_id="company:star",
    family_id="family:star",
    title="制造运营总监",
    employer=None,
    interval_start="2026-05-15",
    interval_end="2026-05-16",
):
    employer = employer or company
    scope = "全面负责制造运营团队、预算、战略与交付结果"
    publication = f"发布于 {interval_start}"
    normalized_text = (
        f"职位ID：123\n职位：{title}\n雇主：{employer}\n"
        f"{publication}\n职责：{scope}"
    )
    raw_path = tmp_path / "strict-job.html"
    text_path = tmp_path / "strict-job.txt"
    raw_path.write_bytes(f"<html>{normalized_text}</html>".encode("utf-8"))
    text_path.write_bytes(normalized_text.encode("utf-8"))

    def span(value):
        start = normalized_text.index(value)
        return {"text": value, "char_start": start, "char_end": start + len(value)}

    return {
        "company": company,
        "artifact_id": "artifact:linkedin:123",
        "source_platform": "linkedin",
        "source_job_id": "123",
        "source_job_id_span": span("123"),
        "requested_url": "https://jobs.example.com/123",
        "final_url": "https://jobs.example.com/123",
        "captured_at": "2026-05-16T08:00:00+08:00",
        "http_status": 200,
        "mime_type": "text/html",
        "raw_artifact_path": raw_path.name,
        "raw_artifact_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "normalized_text_path": text_path.name,
        "normalized_text_sha256": hashlib.sha256(text_path.read_bytes()).hexdigest(),
        "extractor_version": "job-artifact-v1",
        "exact_title": title,
        "title_span": span(title),
        "employer_display": employer,
        "canonical_company_id": canonical_company_id,
        "corporate_family_id": family_id,
        "employer_span": span(employer),
        "employer_match_basis": "exact_page_employer",
        "scope_spans": [span(scope)],
        "raw_publication_text": publication,
        "publication_span": span(publication),
        "publication_basis": "explicit_date",
        "publication_interval_start": interval_start,
        "publication_interval_end_exclusive": interval_end,
        "date_parser_version": "explicit-date-v1",
        "timezone": "Asia/Shanghai",
        "seniority_label": "director_plus",
        "seniority_rule_version": "director-plus-v1",
        "evaluation_eligible": True,
        "evaluation_exclusion_reason": "",
        "review_status": "approved",
        "reviewer": "fixture",
        "reviewed_at": "2026-05-16T09:00:00+08:00",
    }


def test_monthly_panel_reuses_company_split_without_label_leakage(tmp_path) -> None:
    company = "星河科技"
    title = "半导体制造运营总监"
    scope = "全面负责制造运营团队、预算与交付结果"
    employer = "星河科技"
    publication = "发布于 2026-05-15"
    normalized_text = (
        f"职位ID：123\n职位：{title}\n雇主：{employer}\n"
        f"{publication}\n职责：{scope}"
    )
    raw_artifact = tmp_path / "job.html"
    normalized_artifact = tmp_path / "job.txt"
    raw_artifact.write_bytes(f"<html>{normalized_text}</html>".encode("utf-8"))
    normalized_artifact.write_bytes(normalized_text.encode("utf-8"))
    raw_sha = hashlib.sha256(raw_artifact.read_bytes()).hexdigest()
    normalized_sha = hashlib.sha256(normalized_artifact.read_bytes()).hexdigest()

    def span(value):
        start = normalized_text.index(value)
        return {"text": value, "char_start": start, "char_end": start + len(value)}
    pool = {
        "companies": [
            {
                "company": "星河科技",
                "company_type": "startup_private",
                "sector": "semiconductor",
                "split": "train",
                "canonical_company_id": "company:star",
                "corporate_family_id": "family:star",
            },
            {
                "company": "远山科技",
                "company_type": "listed",
                "sector": "semiconductor",
                "split": "test",
                "canonical_company_id": "company:mountain",
                "corporate_family_id": "family:mountain",
            },
        ]
    }
    news = {
        "companies": [
            {
                "company": "星河科技",
                "results": [
                    {
                        "strict_evidence_ready": True,
                        "event_date_candidate": "2026-02-10",
                        "event_type": "factory_or_capacity",
                        "phase": "build_organize",
                        "title": "星河科技启动产线建设",
                        "search_excerpt": "星河科技启动新产线建设。",
                        "source_url": "https://example.com/news",
                        "source_grade": "A",
                        "content_sha256": "news-hash",
                        "storage_path": "artifacts/news.html",
                    },
                    {
                        "strict_evidence_ready": True,
                        "event_date_candidate": "2026-06-01",
                        "event_type": "major_order",
                        "phase": "scale_delivery",
                        "title": "星河科技获得订单",
                        "search_excerpt": "未来证据不得进入四月快照。",
                        "source_url": "https://example.com/future",
                        "source_grade": "A",
                        "content_sha256": "future-hash",
                        "storage_path": "artifacts/future.html",
                    },
                ],
            }
        ]
    }
    jobs = {
        "jobs": [
            {
                "company": company,
                "artifact_id": "artifact:linkedin:123",
                "source_platform": "linkedin",
                "source_job_id": "123",
                "source_job_id_span": span("123"),
                "requested_url": "https://jobs.example.com/123",
                "final_url": "https://jobs.example.com/123",
                "captured_at": "2026-05-16T08:00:00+08:00",
                "http_status": 200,
                "mime_type": "text/html",
                "raw_artifact_path": "job.html",
                "raw_artifact_sha256": raw_sha,
                "normalized_text_path": "job.txt",
                "normalized_text_sha256": normalized_sha,
                "extractor_version": "job-artifact-v1",
                "exact_title": title,
                "title_span": span(title),
                "employer_display": employer,
                "canonical_company_id": "company:star",
                "corporate_family_id": "family:star",
                "employer_span": span(employer),
                "employer_match_basis": "exact_page_employer",
                "scope_spans": [span(scope)],
                "raw_publication_text": publication,
                "publication_span": span(publication),
                "publication_basis": "explicit_date",
                "publication_interval_start": "2026-05-15",
                "publication_interval_end_exclusive": "2026-05-16",
                "date_parser_version": "explicit-date-v1",
                "timezone": "Asia/Shanghai",
                "seniority_label": "director_plus",
                "seniority_rule_version": "director-plus-v1",
                "evaluation_eligible": True,
                "evaluation_exclusion_reason": "",
                "review_status": "approved",
                "reviewer": "fixture",
                "reviewed_at": "2026-05-16T09:00:00+08:00",
            }
        ]
    }

    panel = build_historical_panel(
        pool=pool,
        news=news,
        jobs=jobs,
        cutoffs=[date(2026, 2, 28), date(2026, 4, 30)],
        artifact_root=tmp_path,
    )

    star_rows = [row for row in panel["rows"] if row["company"] == "星河科技"]
    assert [row["split"] for row in star_rows] == ["train", "train"]
    assert [row["label"] for row in star_rows] == ["positive", "positive"]
    assert all(
        all(item["event_type"] != "job_ad" for item in row["timeline"]["evidence"])
        for row in star_rows
    )
    april = next(row for row in star_rows if row["cutoff"] == "2026-04-30")
    assert [item["title"] for item in april["timeline"]["evidence"]] == [
        "星河科技启动产线建设"
    ]
    assert april["job_outcomes"][0]["title"] == title
    assert panel["counts"]["positive_samples"] == 2
    assert panel["counts"]["positive_companies"] == 1
    assert panel["counts"]["by_split"]["test"]["positive_samples"] == 0


def test_panel_does_not_promote_unreplayable_search_candidate(tmp_path) -> None:
    panel = build_historical_panel(
        pool={"companies": [{"company": "星河科技", "split": "test"}]},
        news={"companies": []},
        jobs={
            "queue": [
                {
                    "company": "星河科技",
                    "title": "研发总监",
                    "publication_interval_start": "2026-05-01",
                    "publication_interval_end_exclusive": "2026-05-02",
                    "label_status": "verified_search_snapshot_candidate",
                }
            ]
        },
        cutoffs=[date(2026, 4, 30)],
        artifact_root=tmp_path,
    )

    assert panel["rows"][0]["label"] == "unknown"
    assert panel["counts"]["positive_samples"] == 0


def test_strict_job_rejects_self_declared_wrong_employer_and_manager_title(
    tmp_path,
) -> None:
    wrong_employer = _strict_job_fixture(tmp_path, employer="另一家公司")
    assert not _job_row_is_strict(
        wrong_employer,
        artifact_root=tmp_path,
        company_ids={"星河科技": "company:star"},
    )

    manager = _strict_job_fixture(tmp_path, title="制造运营经理")
    assert not _job_row_is_strict(
        manager,
        artifact_root=tmp_path,
        company_ids={"星河科技": "company:star"},
    )


def test_general_manager_is_not_rejected_by_manager_substring(tmp_path) -> None:
    job = _strict_job_fixture(
        tmp_path,
        title="General Manager, Greater China",
    )

    assert _job_row_is_strict(
        job,
        artifact_root=tmp_path,
        company_ids={job["company"]: "company:star"},
    )


def test_corporate_family_cannot_cross_dataset_splits(tmp_path) -> None:
    with pytest.raises(ValueError, match="corporate family"):
        build_historical_panel(
            pool={
                "companies": [
                    {
                        "company": "集团甲",
                        "split": "train",
                        "canonical_company_id": "company:a",
                        "corporate_family_id": "family:shared",
                    },
                    {
                        "company": "集团甲医疗",
                        "split": "test",
                        "canonical_company_id": "company:b",
                        "corporate_family_id": "family:shared",
                    },
                ]
            },
            news={"companies": []},
            jobs={},
            cutoffs=[date(2026, 4, 30)],
            artifact_root=tmp_path,
        )


def test_partial_date_interval_stays_unknown_even_with_negative_coverage(
    tmp_path,
) -> None:
    job = _strict_job_fixture(
        tmp_path,
        interval_start="2026-04-30",
        interval_end="2026-05-02",
    )
    coverage_text = "每日归档确认总监级职位列表完整"
    coverage_raw = tmp_path / "coverage.html"
    coverage_normalized = tmp_path / "coverage.txt"
    coverage_raw.write_bytes(coverage_text.encode("utf-8"))
    coverage_normalized.write_bytes(coverage_text.encode("utf-8"))
    horizon_start = date(2026, 5, 1)
    horizon_end = horizon_start + timedelta(days=90)
    coverage = {
        "company": "星河科技",
        "coverage_basis": "daily_complete_director_plus_snapshots",
        "complete_director_plus_listing": True,
        "coverage_start": horizon_start.isoformat(),
        "coverage_end_exclusive": horizon_end.isoformat(),
        "covered_dates": [
            (horizon_start + timedelta(days=offset)).isoformat()
            for offset in range(90)
        ],
        "raw_artifact_path": coverage_raw.name,
        "raw_artifact_sha256": hashlib.sha256(
            coverage_raw.read_bytes()
        ).hexdigest(),
        "normalized_text_path": coverage_normalized.name,
        "normalized_text_sha256": hashlib.sha256(
            coverage_normalized.read_bytes()
        ).hexdigest(),
        "coverage_evidence_spans": [
            {
                "text": coverage_text,
                "char_start": 0,
                "char_end": len(coverage_text),
            }
        ],
    }
    panel = build_historical_panel(
        pool={
            "companies": [
                {
                    "company": "星河科技",
                    "split": "test",
                    "canonical_company_id": "company:star",
                    "corporate_family_id": "family:star",
                }
            ]
        },
        news={"companies": []},
        jobs={"jobs": [job], "coverage_snapshots": [coverage]},
        cutoffs=[date(2026, 4, 30)],
        artifact_root=tmp_path,
    )

    assert panel["rows"][0]["label"] == "unknown"
