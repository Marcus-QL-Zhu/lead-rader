import json

import pytest

from ht_lead_radar.company_demand_v2 import (
    COMPANY_DEMAND_SYSTEM_PROMPT,
    build_company_demand_repair_prompt,
    build_company_evidence_packets,
    build_single_company_demand_prompt,
    parse_single_company_demand,
)
from ht_lead_radar.talent_demand_analysis import (
    DemandAnalysisError,
    is_specific_director_title,
)
from test_direct_talent_generator import demand_response
from test_talent_pool import sample_report


def test_evidence_packet_keeps_diverse_event_types_and_stable_ids():
    report = sample_report(leads=1)
    lead = report["leads"][0]
    lead["evidence"] = [
        {
            "event_type": "funding",
            "title": "融资一",
            "snippet": "融资",
            "source_url": "https://example.com/f1",
            "source_grade": "A",
        },
        {
            "event_type": "funding",
            "title": "融资转载",
            "snippet": "融资",
            "source_url": "https://example.com/f2",
            "source_grade": "C",
        },
        {
            "event_type": "factory_or_capacity",
            "title": "工厂",
            "snippet": "开始建设产线",
            "source_url": "https://example.com/factory",
            "source_grade": "A",
        },
    ]

    first = build_company_evidence_packets(report)[0]
    second = build_company_evidence_packets(report)[0]

    assert first == second
    assert {item["event_type"] for item in first["evidence"]} == {
        "funding",
        "factory_or_capacity",
    }
    assert all(item["evidence_id"].startswith("ev_") for item in first["evidence"])


def test_unknown_evidence_reference_is_rejected():
    report = sample_report(leads=1)
    packet = build_company_evidence_packets(report)[0]
    response = demand_response(
        packet,
        title="机器人运动控制工程化总监",
    )
    response["role_hypotheses"][0]["evidence_refs"] = ["made-up"]

    with pytest.raises(DemandAnalysisError, match="unknown evidence"):
        parse_single_company_demand(
            json.dumps(response, ensure_ascii=False),
            packet=packet,
        )

def test_bare_owner_title_is_not_unambiguously_director_plus():
    assert not is_specific_director_title("运动控制算法工程化负责人")
    assert is_specific_director_title("运动控制算法工程化总监")

def test_single_funding_event_cannot_create_near_term_role():
    report = sample_report(leads=1)
    packet = build_company_evidence_packets(report)[0]
    response = demand_response(
        packet,
        title="机器人运动控制工程化总监",
    )

    with pytest.raises(DemandAnalysisError, match="near_term requires"):
        parse_single_company_demand(
            json.dumps(response, ensure_ascii=False),
            packet=packet,
        )


def test_single_funding_event_can_create_low_confidence_watchlist_role():
    report = sample_report(leads=1)
    packet = build_company_evidence_packets(report)[0]
    response = demand_response(
        packet,
        title="机器人运动控制工程化总监",
    )
    response["role_hypotheses"][0]["horizon"] = "watchlist"

    parsed = parse_single_company_demand(
        json.dumps(response, ensure_ascii=False),
        packet=packet,
    )

    assert parsed["hypotheses"][0]["horizon"] == "watchlist"


def test_job_ad_alone_cannot_create_early_role():
    report = sample_report(leads=1)
    report["leads"][0]["evidence"][0]["event_type"] = "job_ad"
    packet = build_company_evidence_packets(report)[0]
    response = demand_response(
        packet,
        title="机器人运动控制工程化总监",
    )

    with pytest.raises(DemandAnalysisError, match="pre-ad upstream event"):
        parse_single_company_demand(
            json.dumps(response, ensure_ascii=False),
            packet=packet,
        )


def _operational_packet():
    report = sample_report(leads=1)
    report["leads"][0]["evidence"].insert(
        0,
        {
            "event_id": "ev_factory",
            "event_type": "factory_or_capacity",
            "source_grade": "A",
            "title": "启动工程化产线",
            "snippet": "公司启动小批量工程化产线。",
            "source_url": "https://official.example.com/factory",
        },
    )
    return build_company_evidence_packets(report)[0]


def test_prompt_has_three_diverse_few_shots_and_shanghai_fallback():
    packet = _operational_packet()

    assert COMPANY_DEMAND_SYSTEM_PROMPT.count("示例一") == 1
    assert COMPANY_DEMAND_SYSTEM_PROMPT.count("示例二") == 1
    assert COMPANY_DEMAND_SYSTEM_PROMPT.count("示例三") == 1
    assert "单独融资或单独合作意向只能支持 low-confidence" in COMPANY_DEMAND_SYSTEM_PROMPT
    assert "职能依赖展开" in COMPANY_DEMAND_SYSTEM_PROMPT
    assert "跨职能 hub" in COMPANY_DEMAND_SYSTEM_PROMPT
    assert "中国区业务战略" in COMPANY_DEMAND_SYSTEM_PROMPT
    assert "财务规划与分析" in COMPANY_DEMAND_SYSTEM_PROMPT
    assert "政府事务" in COMPANY_DEMAND_SYSTEM_PROMPT
    assert "治理/使能岗位" in COMPANY_DEMAND_SYSTEM_PROMPT
    assert "采购、" in COMPANY_DEMAND_SYSTEM_PROMPT
    assert "战略联盟责任" in COMPANY_DEMAND_SYSTEM_PROMPT
    assert "事业部总经理" in COMPANY_DEMAND_SYSTEM_PROMPT
    assert "量产项目交付" in COMPANY_DEMAND_SYSTEM_PROMPT
    assert "不确定时填上海" in build_single_company_demand_prompt(packet)


def test_overflow_and_duplicate_lists_are_normalized_without_weakening_gate():
    packet = _operational_packet()
    response = demand_response(packet, title="机器人小批量制造工程化总监")
    role = response["role_hypotheses"][0]
    role["must_have_signals"] = [
        "量产经验",
        "质量体系经验",
        "跨部门交付经验",
        "供应链协同经验",
        "产能爬坡经验",
        "量产经验",
        "项目管理经验",
    ]
    response["watch_for"] = [f"观察信号{i}" for i in range(7)]

    parsed = parse_single_company_demand(
        json.dumps(response, ensure_ascii=False),
        packet=packet,
    )

    assert parsed["hypotheses"][0]["must_have_signals"] == [
        "量产经验",
        "质量体系经验",
        "跨部门交付经验",
        "供应链协同经验",
        "产能爬坡经验",
    ]
    assert parsed["watch_for"] == [f"观察信号{i}" for i in range(5)]


@pytest.mark.parametrize(
    "raw_city",
    [
        "",
        "待定",
        "未知",
        "全国",
        "多地",
        "上海、北京",
        "上海/苏州",
        "上海或北京",
        "上海和苏州",
        "上海与北京",
        "深圳及北京",
    ],
)
def test_unknown_or_multiple_cities_default_to_shanghai(raw_city):
    packet = _operational_packet()
    response = demand_response(packet, title="机器人小批量制造工程化总监")
    role = response["role_hypotheses"][0]
    role["city"] = raw_city
    role["city_basis"] = ""

    parsed = parse_single_company_demand(
        json.dumps(response, ensure_ascii=False),
        packet=packet,
    )

    parsed_role = parsed["hypotheses"][0]
    assert parsed_role["city"] == "上海"
    assert "默认上海" in parsed_role["city_basis"]
    assert "人工复核" in parsed_role["city_basis"]


def test_repair_prompt_contains_complete_safe_json_example():
    packet = _operational_packet()

    prompt = build_company_demand_repair_prompt(
        packet,
        "not-json",
        DemandAnalysisError("response has no company demand JSON object"),
    )

    assert f'"lead_index":{packet["lead_index"]}' in prompt
    assert json.dumps(packet["company"], ensure_ascii=False) in prompt
    assert '"role_hypotheses":[]' in prompt
    assert '"watch_for":["观察新的上游运营信号"]' in prompt
    assert "不要 Markdown" in prompt

def test_unknown_evidence_after_six_valid_refs_is_not_silently_trimmed():
    packet = _operational_packet()
    packet["evidence"] = [
        {**packet["evidence"][0], "evidence_id": f"ev_{index}"}
        for index in range(6)
    ]
    response = demand_response(packet, title="机器人小批量制造工程化总监")
    response["role_hypotheses"][0]["evidence_refs"] = [
        *(item["evidence_id"] for item in packet["evidence"]),
        "made-up",
    ]

    with pytest.raises(DemandAnalysisError, match="at most 6"):
        parse_single_company_demand(
            json.dumps(response, ensure_ascii=False),
            packet=packet,
        )


def test_known_unique_city_is_preserved():
    packet = _operational_packet()
    response = demand_response(packet, title="机器人小批量制造工程化总监")
    role = response["role_hypotheses"][0]
    role["city"] = "深圳"
    role["city_basis"] = "官方披露的唯一研发所在地"

    parsed = parse_single_company_demand(
        json.dumps(response, ensure_ascii=False),
        packet=packet,
    )

    assert parsed["hypotheses"][0]["city"] == "深圳"
    assert parsed["hypotheses"][0]["city_basis"] == "官方披露的唯一研发所在地"


@pytest.mark.parametrize("known_city", ["深圳", "呼和浩特市", "和田市"])
def test_city_names_containing_connector_characters_are_preserved(known_city):
    packet = _operational_packet()
    response = demand_response(packet, title="机器人小批量制造工程化总监")
    role = response["role_hypotheses"][0]
    role["city"] = known_city
    role["city_basis"] = "官方披露的唯一所在地"

    parsed = parse_single_company_demand(
        json.dumps(response, ensure_ascii=False),
        packet=packet,
    )

    assert parsed["hypotheses"][0]["city"] == known_city


def test_evidence_against_and_unknowns_are_not_silently_trimmed():
    packet = _operational_packet()
    response = demand_response(packet, title="机器人小批量制造工程化总监")
    response["role_hypotheses"][0]["evidence_against"] = [
        f"反证{i}" for i in range(5)
    ]

    with pytest.raises(DemandAnalysisError, match="evidence_against.*at most 4"):
        parse_single_company_demand(
            json.dumps(response, ensure_ascii=False),
            packet=packet,
        )
