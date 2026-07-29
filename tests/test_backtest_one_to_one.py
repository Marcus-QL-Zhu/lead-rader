from ht_lead_radar.backtest import HistoricalJob, validate_predictions
from backtest_helpers import auditable_snapshot


def test_one_actual_job_cannot_count_as_multiple_prediction_matches():
    snapshot = {
        "manifest": {
            "cutoff": "2026-05-01",
            "horizon_months": 3,
            "workforce_precursors_enabled": False,
            "prediction_inputs_exclude_job_ads": True,
            "snapshot_schema_version": 2,
            "prediction_packets_sha256": (
                "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
            ),
        },
        "prediction_packets": [],
        "company_types": {"test-company": "startup_private"},
        "analyses": [
            {
                "company": "test-company",
                "hypotheses": [
                    {
                        "specific_title": "\u6218\u7565\u91c7\u8d2d\u603b\u76d1",
                        "capability_gap": "gap-a",
                        "mandate": "mandate-a",
                    },
                    {
                        "specific_title": "\u4f9b\u5e94\u94fe\u4fdd\u969c\u603b\u76d1",
                        "capability_gap": "gap-b",
                        "mandate": "mandate-b",
                    },
                ],
            }
        ],
    }
    snapshot = auditable_snapshot(
        company="test-company",
        company_type="startup_private",
        analyses=snapshot["analyses"],
    )
    jobs = [
        HistoricalJob(
            company="test-company",
            title="\u91c7\u8d2d\u603b\u76d1",
            description="\u7edf\u7b79\u4f9b\u5e94\u5546",
            published_at="2026-05-20",
            source_url="https://example.com/job",
        )
    ]

    report = validate_predictions(snapshot, jobs)

    assert report["counts"]["role_matches"] == 1
    assert report["counts"]["company_only_matches"] == 1


def test_matching_maximizes_distinct_supported_predictions_not_greedy_order():
    analyses = [
        {
            "company": "test-company",
            "hypotheses": [
                {
                    "specific_title": "供应链与采购总监",
                    "capability_gap": "缺少供应链与采购统筹",
                    "mandate": "统筹供应链和采购",
                },
                {
                    "specific_title": "供应链总监",
                    "capability_gap": "缺少供应链体系",
                    "mandate": "建设供应链体系",
                },
            ],
        }
    ]
    snapshot = auditable_snapshot(
        company="test-company",
        company_type="listed",
        analyses=analyses,
        cutoff="2026-01-01",
    )
    jobs = [
        HistoricalJob(
            company="test-company",
            title="供应链总监",
            description="负责供应链体系",
            published_at="2026-01-10",
            source_url="https://example.com/supply-chain",
        ),
        HistoricalJob(
            company="test-company",
            title="采购总监",
            description="负责采购体系",
            published_at="2026-01-20",
            source_url="https://example.com/procurement",
        ),
    ]

    report = validate_predictions(snapshot, jobs)

    assert report["counts"]["role_matches"] == 2
