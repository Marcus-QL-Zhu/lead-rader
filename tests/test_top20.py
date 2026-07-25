from datetime import date

from ht_lead_radar.models import Evidence
from ht_lead_radar.pipeline import build_late_opportunities, build_leads


def upstream(company, event_type, url, *, event_id='', event_date='2026-07-20'):
    return Evidence(
        company=company, event_type=event_type, phase='strategy_capital',
        event_date=event_date, title=f'{company} signal', snippet='公开上游信号',
        source_url=url, source_name='source', source_grade='B', direction='脑机接口',
        event_id=event_id,
    )


def test_top20_keeps_low_score_but_preserves_hard_gates():
    evidence = [
        upstream(f'公司{i}科技', 'partnership', f'https://s{i}.example/a')
        for i in range(25)
    ]
    evidence.append(Evidence(
        company='广告公司科技', event_type='job_ad', phase='recruit', event_date='2026-07-20',
        title='研发总监', snippet='招聘研发总监', source_url='https://jobs.example/a',
        source_name='jobs', source_grade='C', direction='脑机接口',
    ))
    leads = build_leads('脑机接口', evidence, as_of=date(2026, 7, 25), minimum_score=0, limit=20)

    assert len(leads) == 20
    assert '广告公司科技' not in {lead.company for lead in leads}
    assert all(lead.gates['director_plus'] and lead.gates['has_upstream_signal'] for lead in leads)
    assert all(lead.score_components for lead in leads)
    assert all(abs(sum(item.points for item in lead.score_components) - lead.score) < 0.11 for lead in leads)


def test_duplicate_sources_for_same_event_do_not_double_need_score():
    one = upstream('示例科技', 'funding', 'https://a.example/1', event_id='event-1')
    duplicate = upstream('示例科技', 'funding', 'https://b.example/2', event_id='event-1')
    single_lead = build_leads('脑机接口', [one], as_of=date(2026, 7, 25))[0]
    duplicate_lead = build_leads('脑机接口', [one, duplicate], as_of=date(2026, 7, 25))[0]
    single_need = next(item.points for item in single_lead.score_components if item.key == 'need')
    duplicate_need = next(item.points for item in duplicate_lead.score_components if item.key == 'need')
    assert duplicate_need == single_need
    assert duplicate_lead.score > single_lead.score  # independent source corroboration only


def test_job_ad_only_goes_to_late_appendix():
    ad = Evidence(
        company='晚期科技', event_type='job_ad', phase='recruit', event_date='2026-07-20',
        title='研发总监', snippet='招聘研发总监', source_url='https://jobs.example/a',
        source_name='jobs', source_grade='C', direction='脑机接口',
    )
    assert build_leads('脑机接口', [ad]) == []
    late = build_late_opportunities('脑机接口', [ad])
    assert late and late[0]['company'] == '晚期科技'


def test_main_gate_requires_dated_upstream_strictly_before_first_job_ad():
    company = '时序科技'
    ad = Evidence(
        company=company, event_type='job_ad', phase='recruit',
        event_date='2026-07-20', title='研发总监', snippet='招聘研发总监',
        source_url='https://jobs.example/director', source_name='jobs',
        source_grade='C', direction='脑机接口',
    )
    earlier = upstream(
        company, 'funding', 'https://news.example/earlier',
        event_date='2026-07-19',
    )
    same_day = upstream(
        company, 'funding', 'https://news.example/same',
        event_date='2026-07-20',
    )
    later = upstream(
        company, 'funding', 'https://news.example/later',
        event_date='2026-07-21',
    )
    unknown = upstream(
        company, 'funding', 'https://news.example/unknown',
        event_date='',
    )

    leads = build_leads('脑机接口', [earlier, ad], as_of=date(2026, 7, 25))
    assert len(leads) == 1
    assert leads[0].gates == {
        'director_plus': True,
        'has_upstream_signal': True,
        'upstream_precedes_job_ad': True,
    }
    assert build_leads('脑机接口', [same_day, ad], as_of=date(2026, 7, 25)) == []
    assert build_leads('脑机接口', [later, ad], as_of=date(2026, 7, 25)) == []
    assert build_leads('脑机接口', [unknown, ad], as_of=date(2026, 7, 25)) == []


def test_undated_job_ad_fails_closed_but_no_ad_still_allows_upstream():
    signal = upstream(
        '边界科技', 'funding', 'https://news.example/signal',
        event_date='2026-07-01',
    )
    undated_ad = Evidence(
        company='边界科技', event_type='job_ad', phase='recruit',
        event_date='', title='研发总监', snippet='招聘研发总监',
        source_url='https://jobs.example/undated', source_name='jobs',
        source_grade='C', direction='脑机接口',
    )

    assert build_leads('脑机接口', [signal], as_of=date(2026, 7, 25))
    assert build_leads(
        '脑机接口', [signal, undated_ad], as_of=date(2026, 7, 25)
    ) == []


def test_late_appendix_excludes_manager_expert_and_ic_ads():
    def ad(title, snippet, suffix):
        return Evidence(
            company=f'{suffix}科技', event_type='job_ad', phase='recruit',
            event_date='2026-07-20', title=title, snippet=snippet,
            source_url=f'https://jobs.example/{suffix}', source_name='jobs',
            source_grade='C', direction='脑机接口',
        )

    evidence = [
        ad('高级经理', '负责研发项目', 'manager'),
        ad('机器人控制资深专家', '个人贡献者岗位', 'expert'),
        ad('算法工程师', '负责算法开发', 'ic'),
        ad('研发总监', '全面负责团队建设', 'director'),
    ]

    late = build_late_opportunities('脑机接口', evidence)
    assert [item['company'] for item in late] == ['director科技']

def test_manager_ad_does_not_close_director_level_upstream_window():
    signal = upstream(
        '分层科技',
        'funding',
        'https://news.example/funding',
        event_date='2026-07-20',
    )
    manager_ad = Evidence(
        company='分层科技',
        event_type='job_ad',
        phase='recruit',
        event_date='2026-07-01',
        title='高级研发经理',
        snippet='负责研发项目，无组织负责人职责',
        source_url='https://jobs.example/manager',
        source_name='jobs',
        source_grade='C',
        direction='脑机接口',
    )

    assert build_leads(
        '脑机接口', [signal, manager_ad], as_of=date(2026, 7, 25)
    )
