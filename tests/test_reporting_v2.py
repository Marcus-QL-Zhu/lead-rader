import json
from dataclasses import replace
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
    markdown_path, json_path = write_complete_outputs(
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


def test_report_outputs_strip_dynamic_url_credentials_and_diagnostic_pii(tmp_path):
    evidence, metadata = load_demo_fixture("灵巧手")
    leads = build_leads("灵巧手", evidence, metadata, as_of=date(2026, 7, 24))
    leads[0].evidence[0] = replace(
        leads[0].evidence[0],
        source_url=(
            "https://user:pass@example.test/a?access_token=url-secret&page=2#private"
        ),
    )
    late = [
        {
            "company": "示例公司",
            "reason": "招聘广告",
            "ads": ["https://jobs.test/a?token=late-secret&page=3"],
        }
    ]
    deep = {
        "示例公司": {
            "institutions": [
                {
                    "name": "示例资本",
                    "role": "lead",
                    "confidence": 0.8,
                    "evidence_url": (
                        "https://invest.test/a?app_secret=deep-secret&item=1"
                    ),
                }
            ]
        }
    }
    markdown = render_complete_markdown(
        "灵巧手",
        leads,
        "2026-07-24",
        "test",
        late_opportunities=late,
        deep_research=deep,
        source_summary={
            "diagnostic": "contact +44 20 7946 0958 token=summary-secret"
        },
    )
    markdown_path, json_path = write_complete_outputs(
        tmp_path,
        "safe-report",
        markdown,
        leads=leads,
        manifest={
            "source_url": "https://x.test/a?access_token=manifest-secret&page=4"
        },
        late_opportunities=late,
        deep_research=deep,
    )
    rendered = (
        markdown_path.read_text(encoding="utf-8")
        + json_path.read_text(encoding="utf-8")
    )

    for secret in (
        "user:pass",
        "url-secret",
        "late-secret",
        "deep-secret",
        "summary-secret",
        "manifest-secret",
        "7946 0958",
    ):
        assert secret not in rendered
    assert "https://example.test/a?page=2" in rendered


def test_complete_output_boundary_redacts_pii_and_signed_urls_in_every_section(
    tmp_path,
):
    evidence, metadata = load_demo_fixture("灵巧手")
    leads = build_leads("灵巧手", evidence, metadata, as_of=date(2026, 7, 24))
    leads[0].evidence[0] = replace(
        leads[0].evidence[0],
        title="详情请电 138.0013.8000",
        snippet="邮箱 marcus@example.com，固话 (010) 8765/4321",
        source_url=(
            "https://user:pass@example.test/a?X-Amz-Credential=cred"
            "&X-Amz-Signature=sig&ordinary=keep#fragment"
        ),
    )
    markdown = render_complete_markdown(
        "灵巧手",
        leads,
        "2026-07-24",
        "test",
        source_summary={
            "error": b"Cookie: sid=cookie-secret; call 00 44 20 7946 0958"
        },
    )

    markdown_path, json_path = write_complete_outputs(
        tmp_path,
        "boundary-report",
        markdown,
        leads=leads,
        manifest={
            "diagnostic": "id 11010519491231002X",
            "source_url": "https://x.test/a?github_token=git-secret&page=2",
        },
    )
    rendered = markdown_path.read_bytes() + json_path.read_bytes()

    for unsafe in (
        b"138.0013.8000",
        b"marcus@example.com",
        b"8765/4321",
        b"7946 0958",
        b"11010519491231002X",
        b"user:pass",
        b"cred",
        b"cookie-secret",
        b"git-secret",
    ):
        assert unsafe not in rendered
    assert b"ordinary=keep" in rendered
