from ht_lead_radar.models import CompanyLead, Evidence
from ht_lead_radar.role_inference import enrich_industry_roles


def test_multi_topic_roles_are_routed_by_each_leads_evidence():
    semiconductor = CompanyLead(
        company="芯片公司",
        direction="硬科技组合",
        score=60,
        confidence_grade="C",
        timing_stage="watch",
        target_roles=[],
        hiring_thesis="存在扩产信号。",
        outreach_routes=[],
        evidence=[
            Evidence(
                company="芯片公司",
                event_type="factory_or_capacity",
                phase="operational",
                event_date="2026-07-20",
                title="半导体晶圆产线正式投产",
                snippet="新增先进制程产能。",
                source_url="https://example.com/chip",
                source_name="半导体行业协会",
                source_grade="B",
                direction="硬科技组合",
            )
        ],
    )
    enrich_industry_roles(
        [semiconductor],
        "硬科技组合",
        source_topics=("具身智能", "半导体"),
    )

    assert semiconductor.basic_research["matched_source_topics"][0] == "半导体"
    assert "工厂运营总监" in semiconductor.target_roles


def test_vertical_source_provenance_routes_when_text_has_no_sector_keyword():
    space = CompanyLead(
        company="space-test-company",
        direction="\u786c\u79d1\u6280\u7ec4\u5408",
        score=60,
        confidence_grade="C",
        timing_stage="watch",
        target_roles=[],
        hiring_thesis="technical milestone",
        outreach_routes=[],
        evidence=[
            Evidence(
                company="space-test-company",
                event_type="technical_milestone",
                phase="operational",
                event_date="2026-07-20",
                title="engine test completed",
                snippet="design targets reached",
                source_url="https://example.com/space",
                source_name="CNSA policy [cnsa-policy-announcements]",
                source_grade="A",
                direction="\u786c\u79d1\u6280\u7ec4\u5408",
                source_id="cnsa-policy-announcements",
                industry_tags=("commercial_space",),
            )
        ],
    )

    enrich_industry_roles(
        [space],
        "\u786c\u79d1\u6280\u7ec4\u5408",
        source_topics=(
            "\u5177\u8eab\u667a\u80fd",
            "\u534a\u5bfc\u4f53",
            "\u5546\u4e1a\u822a\u5929",
            "\u6838\u805a\u53d8",
            "\u8111\u673a\u63a5\u53e3",
        ),
    )

    assert space.basic_research["matched_source_topics"] == ["\u5546\u4e1a\u822a\u5929"]
    assert "\u578b\u53f7\u603b\u5e08" in space.target_roles


def test_multi_sector_source_tags_do_not_override_document_text():
    lead = CompanyLead(
        company="chip-project-company",
        direction="\u786c\u79d1\u6280\u7ec4\u5408",
        score=60,
        confidence_grade="C",
        timing_stage="watch",
        target_roles=[],
        hiring_thesis="capacity milestone",
        outreach_routes=[],
        evidence=[
            Evidence(
                company="chip-project-company",
                event_type="factory_or_capacity",
                phase="build_organize",
                event_date="2026-07-20",
                title="\u534a\u5bfc\u4f53\u6676\u5706\u9879\u76ee\u73af\u5883\u5f71\u54cd\u62a5\u544a\u83b7\u6279",
                snippet="\u65b0\u5efa\u6676\u5706\u5236\u9020\u4ea7\u7ebf",
                source_url="https://example.com/eia",
                source_name="MEE EIA [mee-eia-list]",
                source_grade="A",
                direction="\u786c\u79d1\u6280\u7ec4\u5408",
                source_id="mee-eia-list",
                industry_tags=(
                    "semiconductor",
                    "commercial_space",
                    "fusion",
                    "embodied_intelligence",
                ),
            )
        ],
    )

    enrich_industry_roles(
        [lead],
        "\u786c\u79d1\u6280\u7ec4\u5408",
        source_topics=(
            "\u5177\u8eab\u667a\u80fd",
            "\u534a\u5bfc\u4f53",
            "\u5546\u4e1a\u822a\u5929",
            "\u6838\u805a\u53d8",
            "\u8111\u673a\u63a5\u53e3",
        ),
    )

    assert lead.basic_research["matched_source_topics"] == ["\u534a\u5bfc\u4f53"]
    assert "\u5de5\u5382\u8fd0\u8425\u603b\u76d1" in lead.target_roles
