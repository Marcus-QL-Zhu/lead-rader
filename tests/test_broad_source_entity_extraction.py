from ht_lead_radar.source_pack_collector import _company_candidates
from ht_lead_radar.source_packs import SourceDefinition


def _broad_source() -> SourceDefinition:
    return SourceDefinition(
        id="broad-media",
        name="行业媒体",
        owner="行业媒体",
        source_type="vertical_technology_media",
        grade="B",
        url="https://media.example/news",
        adapter="html_list",
        signal_types=("funding", "leadership", "project_buildout"),
        industry_tags=("generic",),
        enabled=True,
        verified_on="2026-07-29",
        status="verified_static_list",
        verification_note="test",
    )


def test_prefixed_financing_headline_extracts_company():
    companies = _company_candidates(
        _broad_source(),
        "首发｜未来智能完成A轮融资",
        "本轮资金将用于产品研发和市场拓展。",
    )

    assert "未来智能" in companies


def test_executive_change_extracts_employer_not_person():
    companies = _company_candidates(
        _broad_source(),
        "刘芳出任瑞萨电子中国区总裁",
        "瑞萨电子宣布中国区管理层调整。",
    )

    assert "瑞萨电子" in companies
    assert "刘芳" not in companies


def test_legal_company_name_is_extracted_from_government_detail():
    companies = _company_candidates(
        _broad_source(),
        "重大项目正式开工",
        "建设主体为北京奕行智能科技有限公司，项目计划年内投产。",
    )

    assert "北京奕行智能科技有限公司" in companies


def test_investor_in_body_is_not_misattributed_as_event_company():
    companies = _company_candidates(
        _broad_source(),
        "未来智能有限公司完成A轮融资",
        "投资方深圳资本有限公司参与投资。",
    )

    assert companies == ("未来智能有限公司",)


def test_employer_first_executive_change_extracts_employer():
    companies = _company_candidates(
        _broad_source(),
        "瑞萨电子任命刘芳为中国区总裁",
        "新任负责人将推动中国区业务发展。",
    )

    assert "瑞萨电子" in companies
    assert "中国区" not in companies


def test_funding_title_assigns_recruiting_subject_not_investor():
    companies = _company_candidates(
        _broad_source(),
        "未来智能有限公司获深圳资本有限公司战略投资",
        "本轮资金将用于产品研发。",
    )

    assert companies == ("未来智能有限公司",)


def test_lead_investor_prefix_does_not_become_recruiting_subject():
    companies = _company_candidates(
        _broad_source(),
        "红杉资本有限公司领投未来智能有限公司A轮融资",
        "本轮资金将用于扩大团队。",
    )

    assert companies == ("未来智能有限公司",)


def test_dated_funding_title_returns_only_normalized_company():
    companies = _company_candidates(
        _broad_source(),
        "2025年7月20日 未来智能有限公司完成亿元融资",
        "资金用于扩大研发团队。",
    )

    assert companies == ("未来智能有限公司",)
