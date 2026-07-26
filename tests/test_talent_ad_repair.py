from ht_lead_radar.openclaw_talent_generator import generate_openclaw_draft_bundle
from test_openclaw_talent_generator import (
    SequenceRunner,
    valid_ad_response,
    valid_demand_response,
)
from test_talent_pool import sample_report


def test_one_bounded_repair_can_replace_a_generic_ad():
    report = sample_report(leads=1)
    rejected = valid_ad_response(report)
    rejected["drafts"][0]["recommended_title"] = "战略运营总监"
    rejected["drafts"][0]["public_payload"][
        "position_name"
    ] = "战略运营总监"
    repaired = valid_ad_response(report)
    runner = SequenceRunner(
        valid_demand_response(report),
        rejected,
        repaired,
    )

    bundle = generate_openclaw_draft_bundle(
        report,
        target_count=3,
        runner=runner,
    )

    assert len(runner.calls) == 3
    assert "校验错误" in runner.calls[2]["prompt"]
    assert bundle.drafts[0].recommended_title == "机器人运动控制研发总监"
