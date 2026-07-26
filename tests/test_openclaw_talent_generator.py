import json

import pytest

from ht_lead_radar.openclaw_talent_generator import (
    OpenClawGenerationError,
    generate_openclaw_draft_bundle,
)
from ht_lead_radar.talent_demand_analysis import DemandAnalysisError
from ht_lead_radar.talent_pool import generate_draft_bundle
from test_talent_pool import sample_report


class SequenceRunner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, prompt, *, session_id):
        self.calls.append({"prompt": prompt, "session_id": session_id})
        if not self.responses:
            raise AssertionError("unexpected extra model call")
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def valid_demand_response(report):
    titles = [
        "机器人运动控制研发总监",
        "机器人量产供应链总监",
        "机器人产品商业化总监",
    ]
    return {
        "company_demands": [
            {
                "lead_index": index,
                "company": lead["company"],
                "hypotheses": [
                    {
                        "specific_title": titles[(index - 1) % len(titles)],
                        "mandate": "建立运动控制算法到机器人量产交付的完整能力",
                        "why_now": "市场信号显示产品正在从技术验证走向规模交付",
                        "responsibilities": [
                            "制定运动控制技术路线",
                            "搭建算法与工程团队",
                            "推动强化学习算法工程化",
                            "建立仿真和实机验证闭环",
                            "协同关键部件量产导入",
                        ],
                        "must_have": [
                            "十年以上机器人研发经验",
                            "有运动控制算法落地经验",
                            "有强化学习工程化经验",
                            "管理过跨学科研发团队",
                            "有机器人量产交付经验",
                        ],
                        "preferred": [
                            "熟悉仿真训练平台",
                            "有关键部件联合开发经验",
                        ],
                        "specificity_terms": [
                            "运动控制",
                            "强化学习",
                            "机器人量产",
                        ],
                        "city": "上海",
                        "grounding": ["融资及产品推进信号支持组织扩张推测"],
                    }
                ],
            }
            for index, lead in enumerate(report["leads"], start=1)
        ]
    }


def valid_ad_response(report):
    enriched = json.loads(json.dumps(report, ensure_ascii=False))
    enriched["leads"][0]["target_roles"] = ["机器人运动控制研发总监"]
    seed = generate_draft_bundle(enriched, target_count=3)
    titles = [
        "机器人运动控制研发总监",
        "机器人量产供应链总监",
        "机器人产品商业化总监",
    ]
    drafts = []
    for ordinal, item in enumerate(seed.drafts, start=1):
        payload = dict(item.public_payload)
        payload["position_name"] = titles[ordinal - 1]
        payload["cities"] = ["上海"]
        payload["position_scope"] = (
            f"岗位使命：建设第{ordinal}类运动控制与强化学习关键能力。"
            "核心职责：1.制定技术路线；2.搭建团队；3.推动算法工程化；"
            "4.建立仿真实机闭环；5.协同机器人量产。任职要求："
            "1.十年以上经验；2.有运动控制经验；3.有强化学习经验；"
            "4.管理跨学科团队；5.有规模交付经历。"
        )
        payload["must_have_signals"] = [
            "运动控制算法落地",
            "强化学习工程化",
            "机器人量产交付",
            "跨学科团队管理",
            "仿真与实机验证",
        ]
        drafts.append(
            {
                "ordinal": ordinal,
                "talent_persona": f"第{ordinal}类机器人技术总监人才",
                "role_family": f"机器人职能族{ordinal}",
                "attraction_angle": f"技术产品规模化角度{ordinal}",
                "recommended_title": titles[ordinal - 1],
                "why_now": f"第{ordinal}类市场信号增强，值得当前关注。",
                "public_payload": payload,
            }
        )
    return {"drafts": drafts}


def valid_runner(report):
    return SequenceRunner(
        valid_demand_response(report),
        valid_ad_response(report),
    )


def test_openclaw_generation_is_two_stage_specific_and_guarded():
    report = sample_report(leads=1)
    runner = valid_runner(report)
    bundle = generate_openclaw_draft_bundle(
        report,
        target_count=3,
        runner=runner,
    )

    assert bundle.generation_provider == "direct-llm-openclaw-config-two-stage"
    assert bundle.schema_version == 3
    assert len(bundle.company_demand_analysis) == 1
    assert len(bundle.drafts) == 3
    assert len(runner.calls) == 2
    assert "逐家公司推测" in runner.calls[0]["prompt"]
    generation_prompt = runner.calls[1]["prompt"]
    assert "公司需求分析：" in generation_prompt
    assert "单条输出示例" in generation_prompt
    assert "internal_market_signals" not in generation_prompt
    assert "required_liepin_shape" not in generation_prompt
    assert runner.calls[0]["session_id"] != runner.calls[1]["session_id"]
    assert [item.recommended_title for item in bundle.drafts] == [
        "机器人运动控制研发总监",
        "机器人量产供应链总监",
        "机器人产品商业化总监",
    ]
    assert all(len(item.public_payload["cities"]) == 1 for item in bundle.drafts)


def test_openclaw_response_fails_closed_on_leak_or_bad_count():
    report = sample_report(leads=1)
    response = valid_ad_response(report)
    response["drafts"][0]["public_payload"]["position_scope"] += report["leads"][0][
        "company"
    ]
    with pytest.raises(OpenClawGenerationError, match="leaks"):
        generate_openclaw_draft_bundle(
            report,
            target_count=3,
            runner=SequenceRunner(
                valid_demand_response(report), response, response
            ),
        )

    with pytest.raises(OpenClawGenerationError, match="expected 3"):
        generate_openclaw_draft_bundle(
            report,
            target_count=3,
            runner=SequenceRunner(
                valid_demand_response(report),
                {"drafts": valid_ad_response(report)["drafts"][:2]},
                {"drafts": valid_ad_response(report)["drafts"][:2]},
            ),
        )


def test_openclaw_fails_closed_when_demand_analysis_is_generic():
    report = sample_report(leads=1)
    response = valid_demand_response(report)
    response["company_demands"][0]["hypotheses"][0][
        "specific_title"
    ] = "研发总监"
    with pytest.raises(DemandAnalysisError, match="too broad"):
        generate_openclaw_draft_bundle(
            report,
            target_count=3,
            runner=SequenceRunner(response),
        )
