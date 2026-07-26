import pytest

from ht_lead_radar.openclaw_talent_generator import (
    OpenClawGenerationError,
    generate_openclaw_draft_bundle,
)
from test_openclaw_talent_generator import (
    SequenceRunner,
    valid_ad_response,
    valid_demand_response,
)
from test_talent_pool import sample_report


def test_duplicate_titles_are_rejected_even_when_payloads_differ():
    report = sample_report(leads=1)
    duplicated = valid_ad_response(report)
    duplicated["drafts"][1]["recommended_title"] = duplicated["drafts"][0][
        "recommended_title"
    ]
    duplicated["drafts"][1]["public_payload"]["position_name"] = duplicated[
        "drafts"
    ][0]["recommended_title"]

    with pytest.raises(OpenClawGenerationError, match="duplicated"):
        generate_openclaw_draft_bundle(
            report,
            target_count=3,
            runner=SequenceRunner(
                valid_demand_response(report),
                duplicated,
                duplicated,
            ),
        )
