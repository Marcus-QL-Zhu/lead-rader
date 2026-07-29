from ht_lead_radar.signals import (
    canonical_event_type,
    infer_signal,
    prediction_signal_types,
)


def test_canonical_signal_taxonomy_covers_organization_and_operating_changes():
    cases = {
        "公司任命新的中国区总裁": "executive_change",
        "完成对目标公司的控股权收购": "merger_acquisition",
        "双方成立合资公司独立运营": "joint_venture_or_spinout",
        "正式启动上市辅导": "ipo_or_listing",
        "新设华东区域总部": "new_site_or_entity",
        "新产线正式投产": "factory_or_capacity",
        "发布采购意向，预算金额一亿元": "procurement_intention",
        "产品通过头部客户验证": "customer_validation",
        "启动全国经销商渠道体系": "channel_expansion",
        "获得医疗器械注册证": "regulatory_or_clinical",
        "与高校成立联合研究中心": "research_or_ip",
        "集团MES系统上线": "enterprise_system",
    }
    for text, expected in cases.items():
        assert infer_signal(text)[0] == expected


def test_negative_signals_do_not_exist_in_prediction_taxonomy():
    values = prediction_signal_types()
    assert "project_call" in values
    assert all("negative" not in value for value in values)
    assert all("risk" not in value for value in values)


def test_workforce_signal_is_built_but_can_be_excluded_from_backtest():
    assert infer_signal("集中招聘机械工程师和软件专家")[0] == "workforce_cluster"
    assert "workforce_cluster" not in prediction_signal_types(
        include_workforce_precursors=False
    )
    assert canonical_event_type("clinical_milestone") == "regulatory_or_clinical"


def test_termination_events_are_ignored_not_promoted_to_positive_signals():
    for text in (
        "\u5173\u95ed\u5de5\u5382\u5e76\u524a\u51cf\u4ea7\u80fd",
        "\u7ec8\u6b62\u4e0a\u5e02\u8f85\u5bfc\u5e76\u64a4\u56de\u7533\u8bf7",
        "\u73af\u8bc4\u6279\u590d\u88ab\u64a4\u9500\uff0c\u9879\u76ee\u505c\u6b62\u5efa\u8bbe",
        "\u53d6\u6d88\u5408\u4f5c\u534f\u8bae",
    ):
        assert infer_signal(text) == ("other", "ignore")


def test_recruiting_text_cannot_be_promoted_by_factory_words():
    assert infer_signal("新工厂招聘机械工程师和软件专家")[0] == "workforce_cluster"


def test_bare_join_does_not_mean_executive_change():
    assert infer_signal("公司加入产业生态联盟") != (
        "executive_change",
        "build_organize",
    )
    assert infer_signal("刘某加入公司担任中国区总经理")[0] == "executive_change"
