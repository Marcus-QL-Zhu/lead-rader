from __future__ import annotations

from datetime import date

import pytest

from ht_lead_radar.backtest import HistoricalJob
from ht_lead_radar.historical_training import (
    CompanyPartition,
    CoverageArtifact,
    CoverageAudit,
    RuleRoleModel,
    build_company_month_rows,
    deterministic_company_split,
    evaluate_role_ranker,
    fit_logistic_regression,
    make_dataset,
    monthly_cutoffs_before,
    stable_company_id,
    validate_company_partitions,
    validate_rows,
)
from ht_lead_radar.models import Evidence


def _evidence(
    company: str,
    *,
    published_at: str = "2025-04-10",
    event_type: str = "factory_or_capacity",
) -> Evidence:
    return Evidence(
        company=company,
        event_type=event_type,
        phase="operational",
        event_date=published_at,
        published_at=published_at,
        title="new operating event",
        snippet="capacity entered operation",
        source_url="https://example.com/event",
        source_name="official",
        source_grade="A",
        independent_source_group="company_official",
        content_sha256="a" * 64,
    )


def _job(
    company: str,
    *,
    title: str = "制造总监",
    published_at: str = "2025-05-20",
) -> HistoricalJob:
    return HistoricalJob(
        company=company,
        title=title,
        description="负责制造运营与产能爬坡",
        published_at=published_at,
        source_url="https://example.com/job",
        content_sha256="b" * 64,
    )


def _company(name: str, split: str = "train") -> CompanyPartition:
    return CompanyPartition(
        company_id=stable_company_id(name),
        company=name,
        company_type="listed",
        split=split,
    )


def test_monthly_cutoffs_use_prior_month_ends() -> None:
    assert monthly_cutoffs_before("2025-05-20", months_back=4) == (
        date(2025, 1, 31),
        date(2025, 2, 28),
        date(2025, 3, 31),
        date(2025, 4, 30),
    )


def test_rows_never_use_evidence_published_after_cutoff() -> None:
    company = _company("Example")
    rows = build_company_month_rows(
        companies=[company],
        evidence=[
            _evidence("Example", published_at="2025-04-10"),
            _evidence(
                "Example",
                published_at="2025-05-10",
                event_type="major_order",
            ),
        ],
        jobs=[_job("Example")],
        role_families=["manufacturing"],
        months_back_from_job=1,
    )
    assert len(rows) == 1
    assert rows[0].label == "positive"
    assert rows[0].features["evidence:count"] == 1
    assert "event:major_order:count" not in rows[0].features


def test_event_date_without_public_timestamp_is_excluded() -> None:
    company = _company("Example")
    item = _evidence("Example")
    missing_public_time = Evidence(
        **{
            **item.__dict__,
            "published_at": "",
            "observed_at": "",
            "event_date": "2025-04-10",
        }
    )
    rows = build_company_month_rows(
        companies=[company],
        evidence=[missing_public_time],
        jobs=[_job("Example")],
        role_families=["manufacturing"],
        months_back_from_job=1,
    )
    assert rows[0].features["evidence:count"] == 0


def test_search_only_audit_does_not_create_confirmed_negative() -> None:
    company = _company("Example")
    rows = build_company_month_rows(
        companies=[company],
        evidence=[_evidence("Example")],
        jobs=[_job("Example")],
        coverage_audits=[
            CoverageAudit(
                company="Example",
                window_start="2025-05-01",
                window_end_exclusive="2025-08-01",
                channels_completed=("official_careers", "public_web_search"),
            )
        ],
        role_families=["manufacturing", "quality"],
        months_back_from_job=1,
    )
    by_role = {row.role_family: row for row in rows}
    assert by_role["manufacturing"].label == "positive"
    assert by_role["quality"].label == "contrastive_negative"
    assert by_role["quality"].observability == "search_only"


def test_replayable_two_channel_audit_creates_confirmed_negative() -> None:
    company = _company("Example")
    artifacts = tuple(
        CoverageArtifact(
            channel=channel,
            source_url=f"https://example.com/{channel}",
            captured_at="2025-08-01T00:00:00+00:00",
            content_sha256=character * 64,
        )
        for channel, character in (
            ("official_careers", "a"),
            ("public_web_search", "b"),
        )
    )
    rows = build_company_month_rows(
        companies=[company],
        evidence=[_evidence("Example")],
        jobs=[_job("Example")],
        coverage_audits=[
            CoverageAudit(
                company="Example",
                window_start="2025-05-01",
                window_end_exclusive="2025-08-01",
                channels_completed=("official_careers", "public_web_search"),
                artifacts=artifacts,
            )
        ],
        role_families=["manufacturing", "quality"],
        months_back_from_job=1,
    )
    by_role = {row.role_family: row for row in rows}
    assert by_role["quality"].label == "negative"
    assert by_role["quality"].observability == "replayable"


def test_company_cannot_cross_partitions() -> None:
    with pytest.raises(ValueError, match="company_id appears"):
        validate_company_partitions(
            [
                _company("Example", "train"),
                _company("Example", "test"),
            ]
        )


def test_deterministic_split_respects_counts_and_forced_test() -> None:
    companies = [
        {
            "company": f"Company {index}",
            "company_type": ("listed", "foreign", "startup_private")[index % 3],
        }
        for index in range(30)
    ]
    result = deterministic_company_split(
        companies,
        train_count=15,
        calibration_count=5,
        test_count=6,
        seed="dataset-v1",
        forced_test=["Company 1", "Company 2"],
    )
    counts = {
        split: sum(item.split == split for item in result)
        for split in ("train", "calibration", "test")
    }
    assert counts == {"train": 15, "calibration": 5, "test": 6}
    assert {
        item.company for item in result if item.split == "test"
    }.issuperset({"Company 1", "Company 2"})
    repeated = deterministic_company_split(
        companies,
        train_count=15,
        calibration_count=5,
        test_count=6,
        seed="dataset-v1",
        forced_test=["Company 1", "Company 2"],
    )
    assert result == repeated


def test_deterministic_split_prioritizes_labeled_development_companies() -> None:
    companies = [
        {
            "company": f"Company {index}",
            "company_type": "listed",
            "priority": int(index < 4),
        }
        for index in range(12)
    ]
    result = deterministic_company_split(
        companies,
        train_count=4,
        calibration_count=2,
        test_count=2,
        seed="priority-test",
        forced_test=["Company 10", "Company 11"],
    )
    development = {
        item.company for item in result if item.split in {"train", "calibration"}
    }
    assert development.issuperset({f"Company {index}" for index in range(4)})


def test_dataset_hashes_rows_and_sources() -> None:
    company = _company("Example")
    rows = build_company_month_rows(
        companies=[company],
        evidence=[_evidence("Example")],
        jobs=[_job("Example")],
        role_families=["manufacturing", "quality"],
        months_back_from_job=1,
    )
    dataset = make_dataset(
        companies=[company],
        rows=rows,
        source_hashes={"evidence.json": "a" * 64},
        created_at="2025-08-01T00:00:00+00:00",
    )
    assert dataset.dataset_id.startswith("hist_")
    assert dataset.summary["row_count"] == 2
    validate_rows(dataset.rows)


def test_logistic_ranker_learns_event_role_interaction() -> None:
    companies = [
        _company("Factory A", "train"),
        _company("Factory B", "train"),
        _company("Factory C", "calibration"),
    ]
    evidence = [
        _evidence("Factory A", event_type="factory_or_capacity"),
        _evidence("Factory B", event_type="factory_or_capacity"),
        _evidence("Factory C", event_type="factory_or_capacity"),
    ]
    jobs = [
        _job("Factory A", title="制造总监"),
        _job("Factory B", title="制造总监"),
        _job("Factory C", title="制造总监"),
    ]
    rows = build_company_month_rows(
        companies=companies,
        evidence=evidence,
        jobs=jobs,
        role_families=["manufacturing", "quality"],
        months_back_from_job=1,
    )
    model = fit_logistic_regression(rows, iterations=400)
    metrics = evaluate_role_ranker(
        model,
        rows,
        split="calibration",
        top_k=1,
    )
    assert metrics["evaluated_company_cutoffs"] == 1
    assert metrics["top1_accuracy"] == 1.0
    assert metrics["macro_f1_top1"] == 1.0
    assert metrics["precision_at_20"] == 1.0
    assert metrics["recall_at_20"] == 1.0
    assert metrics["company_type_slices"]["listed"]["top1_accuracy"] == 1.0


def test_rule_baseline_maps_factory_signal_to_manufacturing() -> None:
    company = _company("Factory Rule", "calibration")
    rows = build_company_month_rows(
        companies=[company],
        evidence=[_evidence("Factory Rule", event_type="factory_or_capacity")],
        jobs=[_job("Factory Rule", title="制造总监")],
        role_families=["manufacturing", "marketing"],
        months_back_from_job=1,
    )
    metrics = evaluate_role_ranker(
        RuleRoleModel(),
        rows,
        split="calibration",
        top_k=1,
    )
    assert metrics["top1_accuracy"] == 1.0
