import json

import pytest

from ht_lead_radar.company_demand_v2 import (
    build_company_evidence_packets,
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


def test_job_ad_alone_cannot_create_early_role():
    report = sample_report(leads=1)
    report["leads"][0]["evidence"][0]["event_type"] = "job_ad"
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
