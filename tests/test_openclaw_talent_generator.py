import json

import pytest

from ht_lead_radar.openclaw_talent_generator import (
    OpenClawGenerationError,
    _extract_agent_text,
    generate_openclaw_draft_bundle,
)
from ht_lead_radar.talent_pool import generate_draft_bundle
from test_talent_pool import sample_report


class StaticRunner:
    def __init__(self, response):
        self.response = response
        self.prompt = ""
        self.session_id = ""

    def run(self, prompt, *, session_id):
        self.prompt = prompt
        self.session_id = session_id
        return json.dumps(self.response, ensure_ascii=False)


def valid_response(report):
    seed = generate_draft_bundle(report, target_count=3)
    titles = ["研发总监", "产品总监", "供应链总监"]
    drafts = []
    for ordinal, item in enumerate(seed.drafts, start=1):
        payload = dict(item.public_payload)
        payload["position_name"] = titles[ordinal - 1]
        payload["position_scope"] = (
            "人才蓄水说明：本广告用于硬科技人才交流，不代表特定企业已有正式委托。"
            f"岗位使命：建设第{ordinal}类关键组织能力。"
            "核心职责：1.制定路线；2.搭建团队；3.推动交付；4.建立机制；"
            "5.协同资源。任职要求：1.十年以上经验；2.有团队管理经验；"
            "3.有规模化实践；4.能跨部门协同；5.能对业务结果负责。"
        )
        drafts.append(
            {
                "ordinal": ordinal,
                "talent_persona": f"第{ordinal}类跨客户总监人才",
                "role_family": f"职能族{ordinal}",
                "attraction_angle": f"组织能力建设角度{ordinal}",
                "recommended_title": titles[ordinal - 1],
                "why_now": f"第{ordinal}类市场信号正在增强，适合提前蓄水。",
                "public_payload": payload,
            }
        )
    return {"drafts": drafts}


def test_openclaw_generation_supplies_copy_and_keeps_deterministic_guards():
    report = sample_report(leads=1)
    runner = StaticRunner(valid_response(report))
    bundle = generate_openclaw_draft_bundle(
        report,
        target_count=3,
        runner=runner,
    )

    assert bundle.generation_provider == "openclaw-main"
    assert bundle.schema_version == 2
    assert len(bundle.drafts) == 3
    assert "禁止调用任何工具" in runner.prompt
    assert "严禁复制到输出" in runner.prompt
    assert runner.session_id
    assert [item.recommended_title for item in bundle.drafts] == [
        "研发总监",
        "产品总监",
        "供应链总监",
    ]


def test_openclaw_response_fails_closed_on_leak_or_bad_count():
    report = sample_report(leads=1)
    response = valid_response(report)
    response["drafts"][0]["public_payload"]["position_scope"] += report["leads"][0][
        "company"
    ]
    with pytest.raises(OpenClawGenerationError, match="leaks"):
        generate_openclaw_draft_bundle(
            report,
            target_count=3,
            runner=StaticRunner(response),
        )

    with pytest.raises(OpenClawGenerationError, match="expected 3"):
        generate_openclaw_draft_bundle(
            report,
            target_count=3,
            runner=StaticRunner({"drafts": valid_response(report)["drafts"][:2]}),
        )


def test_openclaw_cli_json_envelope_is_extracted_despite_plugin_logs():
    stdout = (
        "plugin startup log\n"
        + json.dumps(
            {
                "status": "ok",
                "result": {
                    "payloads": [{"text": '{"drafts":[{"ordinal":1}]}'}]
                },
            }
        )
    )
    assert _extract_agent_text(stdout) == '{"drafts":[{"ordinal":1}]}'
