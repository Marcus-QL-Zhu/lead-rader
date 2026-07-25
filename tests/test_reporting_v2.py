import json
from datetime import date

from ht_lead_radar.collectors import load_demo_fixture
from ht_lead_radar.pipeline import build_leads
from ht_lead_radar.reporting_v2 import (
    render_complete_markdown,
    write_complete_outputs,
)


def test_complete_report_preserves_traceable_manifest(tmp_path):
    evidence, metadata = load_demo_fixture("灵巧手")
    leads = build_leads(
        "灵巧手", evidence, metadata, as_of=date(2026, 7, 24)
    )
    plan = {"request": {"raw_text": "最近灵巧手谁可能招总监？"}, "industry_map": {}}
    markdown = render_complete_markdown(
        "灵巧手", leads, "2026-07-24", "test", request_plan=plan
    )
    _, json_path = write_complete_outputs(
        tmp_path,
        "report",
        markdown,
        leads=leads,
        manifest={"request_plan": plan, "run_id": "run-1"},
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["manifest"]["run_id"] == "run-1"
    assert payload["leads"][0]["evidence"]
    assert "最近灵巧手谁可能招总监" in markdown
