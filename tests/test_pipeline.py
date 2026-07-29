from datetime import date

from ht_lead_radar.collectors import load_demo_fixture
from ht_lead_radar.models import Evidence
from ht_lead_radar.pipeline import build_leads
from ht_lead_radar.reporting import render_markdown
from ht_lead_radar.taxonomy import classify_seniority


def test_demo_produces_multiple_evidence_backed_director_leads():
    evidence, metadata = load_demo_fixture("灵巧手")
    leads = build_leads("灵巧手", evidence, metadata, as_of=date(2026, 7, 24))

    assert {lead.company for lead in leads} == {"灵心巧手", "因时机器人", "戴盟机器人"}
    assert all(lead.score >= 60 for lead in leads)
    assert all(
        lead.gates
        == {
            "director_plus": True,
            "has_upstream_signal": True,
            "upstream_precedes_job_ad": True,
        }
        for lead in leads
    )
    assert all(len(lead.evidence) >= 3 for lead in leads)
    assert all(len(lead.outreach_routes) >= 3 for lead in leads)
    assert all(
        all(
            any(term in role for term in ("总监", "平台主管", "总经理", "总师", "首席"))
            for role in lead.target_roles
        )
        for lead in leads
    )
    daimon = next(lead for lead in leads if lead.company == "戴盟机器人")
    assert "具身数据平台主管" in daimon.target_roles


def test_manager_or_expert_titles_never_pass_seniority_gate():
    assert classify_seniority("高级经理")[1] is False
    assert classify_seniority("机器人控制资深专家")[1] is False
    assert classify_seniority("供应链总经理")[1] is True
    assert (
        classify_seniority("首席科学家", "全面负责团队搭建、预算与跨部门交付")[1]
        is True
    )


def test_job_ad_alone_cannot_create_a_lead():
    evidence = [
        Evidence(
            company="示例机器人",
            event_type="job_ad",
            phase="recruit",
            event_date="2026-07-20",
            title="量产总监",
            snippet="公开招聘量产总监",
            source_url="https://example.com/job",
            source_name="company careers",
            source_grade="A",
            direction="灵巧手",
        )
    ]
    assert build_leads("灵巧手", evidence, as_of=date(2026, 7, 24)) == []


def test_report_contains_thesis_roles_routes_and_evidence_urls():
    evidence, metadata = load_demo_fixture("灵巧手")
    leads = build_leads("灵巧手", evidence, metadata, as_of=date(2026, 7, 24))
    report = render_markdown("灵巧手", leads, "2026-07-24", "test")

    assert "为什么可能招总监以上" in report
    assert "公开关系线索" in report
    assert "建议触达路径" not in report
    assert "请其引荐" not in report
    assert "王少鲲—华盖资本科技产业基金" in report
    assert "左家平—达闼机器人、九号机器人" in report
    assert "https://www.dmrobot.com/news/19.html" in report
    assert "这不是全网无广告的证明" in report
    assert all(not route.path for lead in leads for route in lead.outreach_routes)


def test_multi_topic_gate_routes_before_filtering():
    cases = [
        (
            "芯片公司",
            "factory_or_capacity",
            "半导体晶圆产线正式投产",
            "工厂运营总监",
        ),
        (
            "航天公司",
            "technical_milestone",
            "商业航天液体火箭完成试车",
            "型号总师",
        ),
        (
            "机器人公司",
            "major_order",
            "具身智能机器人获得批量订单",
            "交付总监",
        ),
    ]
    evidence = [
        Evidence(
            company=company,
            event_type=event_type,
            phase="build_organize",
            event_date="2026-07-20",
            title=title,
            snippet=title,
            source_url=f"https://example.com/{index}",
            source_name="行业信息源",
            source_grade="B",
            direction="硬科技组合",
        )
        for index, (company, event_type, title, _role) in enumerate(cases)
    ]

    leads = build_leads(
        "硬科技组合",
        evidence,
        as_of=date(2026, 7, 29),
        source_topics=("具身智能", "半导体", "商业航天"),
    )

    by_company = {lead.company: lead for lead in leads}
    assert set(by_company) == {item[0] for item in cases}
    for company, _event_type, _title, role in cases:
        assert role in by_company[company].target_roles
        assert by_company[company].basic_research["matched_source_topics"]


def test_multi_topic_gate_uses_vertical_source_provenance_before_text():
    cases = [
        ("space-test-company", "commercial_space", "\u578b\u53f7\u603b\u5e08"),
        (
            "fusion-test-company",
            "fusion",
            "\u7b49\u79bb\u5b50\u4f53\u7814\u53d1\u603b\u76d1",
        ),
    ]
    evidence = [
        Evidence(
            company=company,
            event_type="technical_milestone",
            phase="build_organize",
            event_date="2026-07-20",
            title="critical device stage test completed",
            snippet="design targets reached",
            source_url=f"https://example.com/{industry_tag}",
            source_name=f"vertical source [{industry_tag}]",
            source_grade="A",
            direction="\u786c\u79d1\u6280\u7ec4\u5408",
            source_id=industry_tag,
            industry_tags=(industry_tag,),
        )
        for company, industry_tag, _role in cases
    ]

    leads = build_leads(
        "\u786c\u79d1\u6280\u7ec4\u5408",
        evidence,
        as_of=date(2026, 7, 29),
        source_topics=(
            "\u5177\u8eab\u667a\u80fd",
            "\u534a\u5bfc\u4f53",
            "\u5546\u4e1a\u822a\u5929",
            "\u6838\u805a\u53d8",
            "\u8111\u673a\u63a5\u53e3",
        ),
    )

    by_company = {lead.company: lead for lead in leads}
    assert set(by_company) == {item[0] for item in cases}
    for company, _industry_tag, role in cases:
        assert role in by_company[company].target_roles
