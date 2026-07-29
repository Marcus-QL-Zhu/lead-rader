from datetime import date

import pytest

from ht_lead_radar.backtest import (
    BacktestConfig,
    HistoricalJob,
    build_prediction_packets,
    evidence_before_cutoff,
    role_family,
    validate_predictions,
    _cap_analysis_hypotheses,
)
from ht_lead_radar.models import Evidence
from backtest_helpers import auditable_snapshot


def _evidence(event_type: str, event_date: str, title: str = "") -> Evidence:
    return Evidence(
        company="时间边界科技",
        event_type=event_type,
        phase="build_organize",
        event_date=event_date,
        title=title or event_type,
        snippet=title or event_type,
        source_url=f"https://example.com/{event_type}/{event_date}",
        source_name="example",
        source_grade="A",
        direction="机器人",
        published_at=event_date,
        company_type="startup_private",
        source_kind="company_official",
        source_excerpt=title or event_type,
    )


_EMPTY_PACKETS_SHA256 = (
    "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
)


def _manifest(cutoff: str = "2026-05-01") -> dict:
    return {
        "cutoff": cutoff,
        "horizon_months": 3,
        "workforce_precursors_enabled": False,
        "prediction_inputs_exclude_job_ads": True,
        "snapshot_schema_version": 2,
        "prediction_packets_sha256": _EMPTY_PACKETS_SHA256,
    }


def test_acceptance_backtest_excludes_answers_and_workforce_precursors():
    config = BacktestConfig(cutoff=date(2026, 5, 1))
    values = [
        _evidence("executive_change", "2026-04-20", "任命新总裁"),
        _evidence("workforce_cluster", "2026-04-21", "集中招聘经理、专家和工程师"),
        _evidence("job_ad", "2026-04-22", "招聘供应链总监"),
        _evidence("funding", "2026-05-02", "完成融资"),
        _evidence("technical_milestone", "", "日期未知"),
    ]

    selected = evidence_before_cutoff(values, config)

    assert [item.event_type for item in selected] == ["executive_change"]
    packets = build_prediction_packets(values, config)
    assert len(packets) == 1
    assert packets[0]["simulated_as_of"] == "2026-05-01"
    assert packets[0]["evidence"][0]["late_validation_only"] is False


def test_backtest_config_supports_isolated_top_k_iteration() -> None:
    config = BacktestConfig(
        cutoff=date(2026, 4, 1),
        max_roles_per_company=3,
        prompt_version="historical-demand-v9-top3",
        experiment_id="holdout-v16",
    )
    assert config.max_roles_per_company == 3
    assert config.prompt_version == "historical-demand-v9-top3"

    with pytest.raises(ValueError, match="between 1 and 5"):
        BacktestConfig(cutoff=date(2026, 4, 1), max_roles_per_company=0)

def test_workforce_precursor_module_can_be_enabled_outside_acceptance_test():
    selected = evidence_before_cutoff(
        [_evidence("workforce_cluster", "2026-04-20")],
        BacktestConfig(
            cutoff=date(2026, 5, 1),
            include_workforce_precursors=True,
        ),
    )
    assert [item.event_type for item in selected] == ["workforce_cluster"]


def test_validation_uses_same_company_family_director_plus_and_three_month_window():
    snapshot = {
        "manifest": _manifest(),
        "prediction_packets": [],
        "company_types": {"时间边界科技": "startup_private"},
        "analyses": [
            {
                "company": "时间边界科技",
                "role_hypotheses": [
                    {
                        "specific_title": "机器人批量交付供应链总监",
                        "capability_gap": "供应商体系",
                        "mandate": "保障关键物料",
                    },
                    {
                        "specific_title": "机器人算法平台主管",
                        "capability_gap": "算法平台",
                        "mandate": "建设算法团队",
                    },
                ],
            }
        ],
    }
    snapshot = auditable_snapshot(
        company=next(iter(snapshot["company_types"])),
        company_type=next(iter(snapshot["company_types"].values())),
        analyses=snapshot["analyses"],
    )
    jobs = [
        HistoricalJob(
            company="时间边界科技",
            title="全球采购与供应链总监",
            description="全面负责供应链团队",
            published_at="2026-06-10",
            source_url="https://jobs.example/supply-chain",
        ),
        HistoricalJob(
            company="时间边界科技",
            title="算法经理",
            description="个人贡献者",
            published_at="2026-06-12",
            source_url="https://jobs.example/manager",
        ),
        HistoricalJob(
            company="时间边界科技",
            title="算法总监",
            description="负责算法团队",
            published_at="2026-08-01",
            source_url="https://jobs.example/outside-window",
        ),
    ]

    report = validate_predictions(snapshot, jobs)

    assert report["counts"] == {
        "candidate_count": 1,
        "analyzed_company_count": 1,
        "failed_company_count": 0,
        "companies_with_hypotheses": 1,
        "candidate_prediction_coverage": 1.0,
        "predictions": 2,
        "role_matches": 1,
        "company_only_matches": 1,
        "not_observed": 0,
            "distinct_predicted_titles": 2,
            "distinct_predicted_role_families": 2,
            "distinct_canonical_role_keys": 2,
        }
    assert report["matches"][0]["status"] == "family_match"
    assert report["matches"][0]["lead_days"] == 40
    assert report["matches"][1]["status"] == "company_only_match"
    assert report["verified_company_types"] == ["startup_private"]


def test_role_family_matches_responsibility_not_exact_title():
    assert role_family("量产物料保障负责人", "统筹采购和供应商") == "supply_chain"
    assert role_family("中国区汽车战略客户总监") == "sales_accounts"


def test_validation_accepts_normalized_company_demand_hypotheses_key():
    snapshot = {
        "manifest": _manifest(),
        "prediction_packets": [],
        "company_types": {"测试公司": "startup_private"},
        "analyses": [
            {
                "company": "测试公司",
                "hypotheses": [
                    {
                        "specific_title": "半导体设备战略采购总监",
                        "capability_gap": "设备采购",
                        "mandate": "建立供应商体系",
                    }
                ],
            }
        ],
    }
    snapshot = auditable_snapshot(
        company=next(iter(snapshot["company_types"])),
        company_type=next(iter(snapshot["company_types"].values())),
        analyses=snapshot["analyses"],
    )
    jobs = [
        HistoricalJob(
            company="测试公司",
            title="采购总监",
            description="负责设备采购与供应商管理",
            published_at="2026-05-20",
            source_url="https://example.com/job",
        )
    ]

    report = validate_predictions(snapshot, jobs)

    assert report["counts"]["role_matches"] == 1


def test_historical_job_accepts_auditor_field_aliases():
    job = HistoricalJob.from_dict(
        {
            "company": "test-company",
            "exact_title": "Clinical Development Director",
            "responsibilities_summary": "Leads China clinical development.",
            "published_at": "2026-02-01",
            "source_url": "https://example.com/job",
        }
    )

    assert job.title == "Clinical Development Director"
    assert job.description == "Leads China clinical development."


def test_prediction_top_k_is_enforced_after_model_parsing():
    analysis = {
        "stage_transition": "scale-up",
        "hypotheses": [
            {"specific_title": f"role-{index}"} for index in range(4)
        ],
        "watch_for": [],
    }

    capped = _cap_analysis_hypotheses(analysis, 3)

    assert [item["specific_title"] for item in capped["hypotheses"]] == [
        "role-0",
        "role-1",
        "role-2",
    ]
    assert len(analysis["hypotheses"]) == 4