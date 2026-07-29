from ht_lead_radar.backtest import (
    HistoricalJob,
    effective_role_family,
    role_family,
    validate_predictions,
)
from backtest_helpers import auditable_snapshot


def test_title_beats_generic_terms_in_description():
    assert role_family(
        "\u56db\u8db3\u673a\u5668\u4eba\u4ea7\u54c1\u5316\u603b\u76d1",
        "\u7edf\u7b79\u9879\u76ee\u4ea4\u4ed8",
    ) == "product"


def test_specific_engineering_prediction_matches_broad_technical_director_duties():
    snapshot = {
        "manifest": {
            "cutoff": "2026-01-01",
            "horizon_months": 3,
            "workforce_precursors_enabled": False,
            "prediction_inputs_exclude_job_ads": True,
            "snapshot_schema_version": 2,
            "prediction_packets_sha256": (
                "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
            ),
        },
        "prediction_packets": [],
        "company_types": {"test-company": "listed"},
        "analyses": [
            {
                "company": "test-company",
                "hypotheses": [
                    {
                        "specific_title": (
                            "\u673a\u5668\u4eba\u7cfb\u7edf\u96c6\u6210"
                            "\u5de5\u7a0b\u5316\u603b\u76d1"
                        ),
                        "capability_gap": "\u7cfb\u7edf\u96c6\u6210",
                        "mandate": "\u63a8\u8fdb\u5de5\u7a0b\u843d\u5730",
                    }
                ],
            }
        ],
    }
    snapshot = auditable_snapshot(
        company="test-company",
        company_type="listed",
        analyses=snapshot["analyses"],
        cutoff="2026-01-01",
    )
    jobs = [
        HistoricalJob(
            company="test-company",
            title="\u6280\u672f\u603b\u76d1",
            description=(
                "\u4e3b\u5bfc\u673a\u5668\u4eba\u6838\u5fc3\u6a21\u5757\u7814\u53d1\u3001"
                "\u7cfb\u7edf\u96c6\u6210\u548c\u5de5\u7a0b\u843d\u5730"
            ),
            published_at="2026-02-01",
            source_url="https://example.com/job",
        )
    ]

    report = validate_predictions(snapshot, jobs)

    assert report["counts"]["role_matches"] == 1


def test_generic_technical_director_does_not_match_process_engineering():
    from ht_lead_radar.backtest import effective_role_family, role_family_set

    predicted = role_family_set(
        "\u673a\u5668\u4eba\u7cfb\u7edf\u96c6\u6210\u5de5\u7a0b\u5316\u603b\u76d1",
        "\u63a8\u8fdb\u5de5\u7a0b\u5316",
    )
    generic_actual = role_family_set(
        "\u6280\u672f\u603b\u76d1",
        "\u5236\u5b9a\u6280\u672f\u8def\u7ebf\u5e76\u7ba1\u7406\u7814\u53d1\u56e2\u961f",
    )
    assert "process_engineering" in predicted
    assert "process_engineering" not in generic_actual
    assert effective_role_family(
        "\u6280\u672f\u603b\u76d1",
        "\u7814\u53d1\u7ba1\u7406\u4e0e\u6280\u672f\u8def\u7ebf",
    ) == "research_development"


def test_cross_function_collaboration_words_do_not_flip_title_primary_family():
    from ht_lead_radar.backtest import effective_role_family

    assert effective_role_family(
        "\u4f9b\u5e94\u94fe\u603b\u76d1",
        "\u7edf\u7b79\u8d28\u91cf\u534f\u540c",
    ) == "supply_chain"
    assert effective_role_family(
        "\u8d28\u91cf\u603b\u76d1",
        "\u7edf\u7b79\u4f9b\u5e94\u94fe\u534f\u540c",
    ) == "quality"
    assert effective_role_family(
        "A320总装产能爬坡总监",
        "接入全球生产网络",
    ) == "manufacturing"


def test_cross_function_collaboration_words_do_not_create_a_match():
    analyses = [{
        "company": "quality-company",
        "hypotheses": [{
            "specific_title": "供应链总监",
            "capability_gap": "缺少供应链负责人",
            "mandate": "搭建供应链体系",
        }],
    }]
    snapshot = auditable_snapshot(
        company="quality-company",
        company_type="listed",
        analyses=analyses,
        cutoff="2026-01-01",
    )
    jobs = [HistoricalJob(
        company="quality-company",
        title="质量总监",
        description="负责质量体系并统筹供应链协同",
        published_at="2026-02-01",
        source_url="https://example.com/quality",
    )]
    assert validate_predictions(snapshot, jobs)["counts"]["role_matches"] == 0


def test_canonical_role_key_folds_synonymous_modifiers():
    from ht_lead_radar.backtest import canonical_role_key

    assert canonical_role_key(
        "\u5177\u8eab\u667a\u80fd\u673a\u5668\u4eba\u6218\u7565\u91c7\u8d2d\u603b\u76d1"
    ) == canonical_role_key("\u673a\u5668\u4eba\u91c7\u8d2d\u603b\u76d1")

def test_product_platform_architecture_does_not_match_generic_technical_director():
    analyses = [
        {
            "company": "platform-company",
            "hypotheses": [
                {
                    "specific_title": "机器人跨产品线平台架构总监",
                    "capability_gap": "缺少跨产品线技术平台架构",
                    "mandate": "统一机器人平台技术路线",
                }
            ],
        }
    ]
    snapshot = auditable_snapshot(
        company="platform-company",
        company_type="listed",
        analyses=analyses,
        cutoff="2026-01-01",
    )
    jobs = [
        HistoricalJob(
            company="platform-company",
            title="技术总监",
            description="制定技术路线并管理软件、算法、硬件和系统集成团队",
            published_at="2026-02-01",
            source_url="https://example.com/technical-director",
        )
    ]

    report = validate_predictions(snapshot, jobs)

    assert report["counts"]["role_matches"] == 0


def test_secondary_clinical_words_do_not_override_project_title_family():
    analyses = [
        {
            "company": "clinical-company",
            "hypotheses": [
                {
                    "specific_title": "中国临床开发运营总监",
                    "capability_gap": "缺少临床开发运营统筹",
                    "mandate": "统筹临床科学、试验执行和注册协作",
                }
            ],
        }
    ]
    snapshot = auditable_snapshot(
        company="clinical-company",
        company_type="foreign",
        analyses=analyses,
        cutoff="2026-01-01",
    )
    jobs = [
        HistoricalJob(
            company="clinical-company",
            title="Director, Project Lead for Cell Therapy",
            description=(
                "Leads clinical-trial execution and cross-functional delivery "
                "across the cell-therapy development lifecycle."
            ),
            published_at="2026-02-01",
            source_url="https://example.com/cell-therapy-director",
        )
    ]

    report = validate_predictions(snapshot, jobs)

    assert report["counts"]["role_matches"] == 0

def test_manufacturing_head_matches_process_ramp_prediction():
    analyses = [
        {
            "company": "factory-company",
            "hypotheses": [
                {
                    "specific_title": "新基地工艺工程与制造爬坡总监",
                    "capability_gap": "缺少量产爬坡负责人",
                    "mandate": "负责工艺工程和制造爬坡",
                }
            ],
        }
    ]
    snapshot = auditable_snapshot(
        company="factory-company",
        company_type="foreign",
        analyses=analyses,
        cutoff="2026-01-01",
    )
    jobs = [HistoricalJob(
        company="factory-company",
        title="Head of Manufacturing",
        description="Accountable for manufacturing operations and owns the team budget.",
        published_at="2026-02-01",
        source_url="https://example.com/manufacturing-head",
    )]

    assert validate_predictions(snapshot, jobs)["counts"]["role_matches"] == 1

def test_business_unit_head_is_general_management_not_generic_strategy():
    assert effective_role_family(
        "Head of Specialty BU",
        "Defines business strategy and owns end-to-end commercial operations.",
    ) == "general_management"

def test_project_company_general_manager_prefers_general_management():
    assert role_family("蓝电新设项目公司总经理", "负责项目公司整体经营") == "general_management"
