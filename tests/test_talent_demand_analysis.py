import json

import pytest

from ht_lead_radar.talent_demand_analysis import (
    DemandAnalysisError,
    enrich_report_with_company_demands,
    parse_company_demand_analysis,
)
from ht_lead_radar.talent_pool import (
    generate_draft_bundle,
    validate_liepin_payload,
)
from test_openclaw_talent_generator import valid_demand_response
from test_talent_pool import sample_report


def test_company_demands_replace_generic_role_hypotheses_for_ad_seeding():
    report = sample_report(leads=1)
    demands = parse_company_demand_analysis(
        json.dumps(valid_demand_response(report), ensure_ascii=False),
        report=report,
    )
    enhanced = enrich_report_with_company_demands(report, demands)

    assert enhanced["leads"][0]["target_roles"] == [
        "机器人运动控制研发总监"
    ]
    bundle = generate_draft_bundle(enhanced, target_count=3)
    assert "机器人运动控制研发总监" in bundle.drafts[0].source_role_hypotheses


def test_demand_analysis_requires_one_city():
    report = sample_report(leads=1)
    response = valid_demand_response(report)
    response["company_demands"][0]["hypotheses"][0]["city"] = "上海、杭州"
    with pytest.raises(DemandAnalysisError, match="exactly one"):
        parse_company_demand_analysis(
            json.dumps(response, ensure_ascii=False),
            report=report,
        )


def test_liepin_payload_rejects_multiple_cities():
    payload = generate_draft_bundle(sample_report(leads=1)).drafts[
        0
    ].public_payload
    payload["cities"] = ["上海", "杭州"]
    with pytest.raises(ValueError, match="exactly one"):
        validate_liepin_payload(payload)
