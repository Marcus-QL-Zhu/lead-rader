from datetime import date

from ht_lead_radar.collectors import load_demo_fixture
from ht_lead_radar.models import Evidence
from ht_lead_radar.pipeline import build_leads
from ht_lead_radar.reporting import render_markdown
from ht_lead_radar.taxonomy import classify_seniority


def test_demo_produces_multiple_evidence_backed_director_leads():
    evidence, metadata = load_demo_fixture('灵巧手')
    leads = build_leads('灵巧手', evidence, metadata, as_of=date(2026, 7, 24))

    assert {lead.company for lead in leads} == {'灵心巧手', '因时机器人', '戴盟机器人'}
    assert all(lead.score >= 60 for lead in leads)
    assert all(
        lead.gates == {
            'director_plus': True,
            'has_upstream_signal': True,
            'upstream_precedes_job_ad': True,
        }
        for lead in leads
    )
    assert all(len(lead.evidence) >= 3 for lead in leads)
    assert all(len(lead.outreach_routes) >= 3 for lead in leads)
    assert all(all(any(term in role for term in ('总监', '平台主管', '总经理', '总师', '首席')) for role in lead.target_roles) for lead in leads)
    daimon = next(lead for lead in leads if lead.company == '戴盟机器人')
    assert '具身数据平台主管' in daimon.target_roles


def test_manager_or_expert_titles_never_pass_seniority_gate():
    assert classify_seniority('高级经理')[1] is False
    assert classify_seniority('机器人控制资深专家')[1] is False
    assert classify_seniority('供应链总经理')[1] is True
    assert classify_seniority('首席科学家', '全面负责团队搭建、预算与跨部门交付')[1] is True


def test_job_ad_alone_cannot_create_a_lead():
    evidence = [Evidence(
        company='示例机器人',
        event_type='job_ad',
        phase='recruit',
        event_date='2026-07-20',
        title='量产总监',
        snippet='公开招聘量产总监',
        source_url='https://example.com/job',
        source_name='company careers',
        source_grade='A',
        direction='灵巧手',
    )]
    assert build_leads('灵巧手', evidence, as_of=date(2026, 7, 24)) == []


def test_report_contains_thesis_roles_routes_and_evidence_urls():
    evidence, metadata = load_demo_fixture('灵巧手')
    leads = build_leads('灵巧手', evidence, metadata, as_of=date(2026, 7, 24))
    report = render_markdown('灵巧手', leads, '2026-07-24', 'test')

    assert '为什么可能招总监以上' in report
    assert '公开关系线索' in report
    assert '建议触达路径' not in report
    assert '请其引荐' not in report
    assert '王少鲲—华盖资本科技产业基金' in report
    assert '左家平—达闼机器人、九号机器人' in report
    assert 'https://www.dmrobot.com/news/19.html' in report
    assert '这不是全网无广告的证明' in report
    assert all(not route.path for lead in leads for route in lead.outreach_routes)
