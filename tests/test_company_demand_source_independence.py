import json

import pytest

from ht_lead_radar.company_demand_v2 import (
    build_company_evidence_packets,
    parse_single_company_demand,
)
from ht_lead_radar.talent_demand_analysis import DemandAnalysisError
from test_direct_talent_generator import demand_response


def test_two_event_types_from_same_publisher_are_not_independent_evidence():
    report = {
        "leads": [
            {
                "company": "test-company",
                "direction": "robotics",
                "evidence": [
                    {
                        "event_id": "ev_funding",
                        "event_type": "funding",
                        "source_grade": "B",
                        "title": "round",
                        "snippet": "funding round",
                        "source_url": "https://publisher.example/a",
                    },
                    {
                        "event_id": "ev_partnership",
                        "event_type": "partnership",
                        "source_grade": "B",
                        "title": "partnership",
                        "snippet": "partnership announcement",
                        "source_url": "https://publisher.example/b",
                    },
                ],
            }
        ]
    }
    packet = build_company_evidence_packets(report)[0]
    response = demand_response(
        packet,
        title="\u673a\u5668\u4eba\u5546\u4e1a\u5316\u4e0e"
        "\u751f\u6001\u5408\u4f5c\u603b\u76d1",
    )
    response["role_hypotheses"][0]["evidence_refs"] = [
        "ev_funding",
        "ev_partnership",
    ]

    with pytest.raises(DemandAnalysisError, match="near_term requires"):
        parse_single_company_demand(
            json.dumps(response, ensure_ascii=False),
            packet=packet,
        )
