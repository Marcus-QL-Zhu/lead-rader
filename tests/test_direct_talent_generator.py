import json

import pytest

from ht_lead_radar.company_demand_v2 import (
    COMPANY_DEMAND_SYSTEM_PROMPT,
    build_company_evidence_packets,
    parse_single_company_demand,
)
from ht_lead_radar.direct_talent_generator import (
    DirectTalentGenerationError,
    JOB_AD_SYSTEM_PROMPT,
    generate_direct_talent_bundle,
)
from ht_lead_radar.talent_themes import (
    build_talent_themes,
    build_theme_draft_bundle,
)
from test_talent_pool import sample_report


class SequenceRunner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, prompt, *, session_id, system_prompt=""):
        self.calls.append(
            {
                "prompt": prompt,
                "session_id": session_id,
                "system_prompt": system_prompt,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def supported_report(*, leads: int):
    report = sample_report(leads=leads)
    for index, lead in enumerate(report["leads"], start=1):
        lead["evidence"].insert(
            0,
            {
                "event_id": f"ev_operational_{index}",
                "event_type": "factory_or_capacity",
                "source_grade": "A",
                "title": "启动工程化产线",
                "snippet": "公司启动工程验证和小批量产线。",
                "source_url": f"https://official.example.com/{index}",
            },
        )
    return report


def demand_response(packet, *, title):
    evidence_id = packet["evidence"][0]["evidence_id"]
    return {
        "lead_index": packet["lead_index"],
        "company": packet["company"],
        "stage_transition": "从技术验证进入规模化交付",
        "organizational_gaps": ["缺少跨研发和交付的工程化领导者"],
        "role_hypotheses": [
            {
                "specific_title": title,
                "capability_gap": "缺少端到端工程化能力",
                "mandate": "建立技术研发到规模交付的完整闭环",
                "why_now": "公开事件显示公司进入下一产品阶段",
                "horizon": "near_term",
                "evidence_refs": [evidence_id],
                "evidence_against": ["尚未确认现有负责人配置"],
                "unknowns_to_verify": ["团队规模和现有汇报关系"],
                "key_outcomes": [
                    "制定技术路线",
                    "建立工程验证闭环",
                    "搭建跨职能团队",
                ],
                "must_have_signals": [
                    "十年以上相关经验",
                    "有技术工程化经验",
                    "有规模交付经验",
                ],
                "preferred_signals": [
                    "有从样机验证推进至小批量交付的经验",
                ],
                "specificity_terms": [
                    "运动控制",
                    "工程验证",
                    "规模交付",
                ],
                "city": "上海",
                "city_basis": "公开研发活动所在地",
            }
        ],
        "watch_for": [],
    }


def ad_response(seed):
    payload = dict(seed.public_payload)
    return {
        "drafts": [
            {
                "ordinal": 1,
                "talent_persona": seed.talent_persona,
                "role_family": seed.role_family,
                "attraction_angle": seed.attraction_angle,
                "recommended_title": seed.recommended_title,
                "why_now": seed.why_now,
                "public_payload": payload,
            }
        ]
    }


def test_one_company_per_call_then_one_job_ad_per_theme():
    report = supported_report(leads=2)
    packets = build_company_evidence_packets(report)
    raw_demands = [
        demand_response(
            packets[0],
            title="机器人运动控制工程化总监",
        ),
        demand_response(
            packets[1],
            title="商业航天动力系统交付总监",
        ),
    ]
    parsed_demands = tuple(
        parse_single_company_demand(
            json.dumps(response, ensure_ascii=False),
            packet=packet,
        )
        for response, packet in zip(raw_demands, packets, strict=True)
    )
    themes = build_talent_themes(
        report,
        parsed_demands,
        target_count=5,
    )
    seeds = build_theme_draft_bundle(report, parsed_demands, themes)
    runner = SequenceRunner(
        *raw_demands,
        *(ad_response(seed) for seed in seeds.drafts),
    )

    bundle = generate_direct_talent_bundle(
        report,
        target_count=5,
        runner=runner,
    )

    assert len(bundle.drafts) == 2
    assert len(runner.calls) == 4
    assert all(
        call["system_prompt"] == COMPANY_DEMAND_SYSTEM_PROMPT
        for call in runner.calls[:2]
    )
    assert all(
        call["system_prompt"] == JOB_AD_SYSTEM_PROMPT for call in runner.calls[2:]
    )
    assert "星火机器人" in runner.calls[0]["prompt"]
    assert "深空动力" not in runner.calls[0]["prompt"]
    assert "深空动力" in runner.calls[1]["prompt"]
    assert all(len(draft.public_payload["cities"]) == 1 for draft in bundle.drafts)
    assert all(
        draft.public_payload["position_scope"].startswith("【岗位职责】\n• ")
        and "\n\n【任职要求】\n• " in draft.public_payload["position_scope"]
        and draft.public_payload["job_type"] == "社招"
        and draft.public_payload["languages"] == ["普通话"]
        and "五险一金" in draft.public_payload["benefits"]
        for draft in bundle.drafts
    )


def test_insufficient_evidence_can_return_no_role_without_fabricating_draft():
    report = sample_report(leads=1)
    packet = build_company_evidence_packets(report)[0]
    no_role = {
        "lead_index": 1,
        "company": packet["company"],
        "stage_transition": "现有证据只能确认融资事件",
        "organizational_gaps": [],
        "role_hypotheses": [],
        "watch_for": ["产品进入工程化或量产阶段"],
    }

    runner = SequenceRunner(no_role)
    runner.config = type("Config", (), {"provider": "minimax", "model": "MiniMax-M3"})()
    bundle = generate_direct_talent_bundle(
        report,
        target_count=5,
        runner=runner,
    )

    assert bundle.drafts == ()
    assert bundle.generation_model == "minimax/MiniMax-M3"
    assert bundle.company_demand_analysis[0]["hypotheses"] == []


def test_theme_ad_gets_one_bounded_repair_and_cannot_change_theme_title():
    report = supported_report(leads=1)
    packet = build_company_evidence_packets(report)[0]
    demand = demand_response(
        packet,
        title="机器人运动控制工程化总监",
    )
    parsed = (
        parse_single_company_demand(
            json.dumps(demand, ensure_ascii=False),
            packet=packet,
        ),
    )
    themes = build_talent_themes(report, parsed, target_count=5)
    seed = build_theme_draft_bundle(report, parsed, themes).drafts[0]
    rejected = ad_response(seed)
    rejected["drafts"][0]["recommended_title"] = "机器人产品商业化总监"
    rejected["drafts"][0]["public_payload"]["position_name"] = "机器人产品商业化总监"
    runner = SequenceRunner(demand, rejected, ad_response(seed))

    bundle = generate_direct_talent_bundle(
        report,
        target_count=5,
        runner=runner,
    )

    assert len(runner.calls) == 3
    assert "确定性校验发现" in runner.calls[2]["prompt"]
    assert bundle.drafts[0].recommended_title == "机器人运动控制工程化总监"


def test_theme_ranking_prefers_more_independent_evidence():
    report = supported_report(leads=1)
    packet = build_company_evidence_packets(report)[0]
    first_evidence_id = packet["evidence"][0]["evidence_id"]
    second_evidence_id = "ev_second"
    packet["evidence"].append(
        {
            **packet["evidence"][0],
            "evidence_id": second_evidence_id,
            "event_type": "factory_or_capacity",
        }
    )
    weak = demand_response(packet, title="工程验证体系建设总监")
    strong_role = dict(weak["role_hypotheses"][0])
    strong_role["specific_title"] = "机器人运动控制工程化总监"
    strong_role["evidence_refs"] = [first_evidence_id, second_evidence_id]
    weak["role_hypotheses"].append(strong_role)
    parsed = (
        parse_single_company_demand(
            json.dumps(weak, ensure_ascii=False),
            packet=packet,
        ),
    )

    themes = build_talent_themes(report, parsed, target_count=1)

    assert themes[0]["recommended_title"] == "机器人运动控制工程化总监"
    assert themes[0]["preferred_signals"] == ["有从样机验证推进至小批量交付的经验"]


def test_invalid_company_title_gets_one_bounded_repair():
    report = supported_report(leads=1)
    packet = build_company_evidence_packets(report)[0]
    rejected = demand_response(packet, title="机器人生产总监")
    rejected["role_hypotheses"][0]["specific_title"] = "生产总监"
    repaired = demand_response(
        packet,
        title="机器人小批量制造工程化总监",
    )
    parsed = (
        parse_single_company_demand(
            json.dumps(repaired, ensure_ascii=False),
            packet=packet,
        ),
    )
    themes = build_talent_themes(report, parsed, target_count=1)
    seed = build_theme_draft_bundle(report, parsed, themes).drafts[0]
    runner = SequenceRunner(rejected, repaired, ad_response(seed))

    bundle = generate_direct_talent_bundle(
        report,
        target_count=1,
        runner=runner,
    )

    assert len(runner.calls) == 3
    assert "确定性校验错误" in runner.calls[1]["prompt"]
    assert bundle.drafts[0].recommended_title == "机器人小批量制造工程化总监"


def test_unknown_city_defaults_to_shanghai_and_remains_publishable():
    report = supported_report(leads=1)
    packet = build_company_evidence_packets(report)[0]
    response = demand_response(
        packet,
        title="机器人小批量制造工程化总监",
    )
    response["role_hypotheses"][0]["city"] = ""
    response["role_hypotheses"][0]["city_basis"] = "公开证据未确认城市"
    parsed = (
        parse_single_company_demand(
            json.dumps(response, ensure_ascii=False),
            packet=packet,
        ),
    )

    themes = build_talent_themes(report, parsed, target_count=5)

    assert len(themes) == 1
    assert themes[0]["city"] == "上海"


def test_theme_drafts_expire_seven_days_after_run_date():
    report = supported_report(leads=1)
    packet = build_company_evidence_packets(report)[0]
    response = demand_response(
        packet,
        title="机器人小批量制造工程化总监",
    )
    parsed = (
        parse_single_company_demand(
            json.dumps(response, ensure_ascii=False),
            packet=packet,
        ),
    )
    themes = build_talent_themes(report, parsed, target_count=1)

    seed = build_theme_draft_bundle(report, parsed, themes).drafts[0]
    assert seed.expires_at == "2026-08-02"

    generated = generate_direct_talent_bundle(
        report,
        target_count=1,
        runner=SequenceRunner(response, ad_response(seed)),
    )
    assert generated.drafts[0].expires_at == "2026-08-02"


def test_one_invalid_theme_returns_partial_bundle_instead_of_losing_valid_drafts():
    report = supported_report(leads=2)
    packets = build_company_evidence_packets(report)
    raw_demands = [
        demand_response(packets[0], title="机器人运动控制工程化总监"),
        demand_response(packets[1], title="商业航天动力系统交付总监"),
    ]
    parsed = tuple(
        parse_single_company_demand(
            json.dumps(response, ensure_ascii=False),
            packet=packet,
        )
        for response, packet in zip(raw_demands, packets, strict=True)
    )
    themes = build_talent_themes(report, parsed, target_count=5)
    seeds = build_theme_draft_bundle(report, parsed, themes)
    runner = SequenceRunner(
        *raw_demands,
        {"drafts": []},
        {"drafts": []},
        ad_response(seeds.drafts[1]),
    )

    bundle = generate_direct_talent_bundle(report, target_count=5, runner=runner)

    assert len(bundle.drafts) == 1
    assert bundle.drafts[0].recommended_title == seeds.drafts[1].recommended_title
    assert "failed after one repair" in bundle.generation_error


def test_deadline_crossed_inside_llm_call_is_not_accepted(monkeypatch):
    report = supported_report(leads=1)
    packet = build_company_evidence_packets(report)[0]
    runner = SequenceRunner(demand_response(packet, title="机器人运动控制工程化总监"))
    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(
        "ht_lead_radar.direct_talent_generator.time.monotonic",
        lambda: next(ticks),
    )

    with pytest.raises(DirectTalentGenerationError, match="no valid talent theme"):
        generate_direct_talent_bundle(
            report,
            target_count=5,
            runner=runner,
            deadline_seconds=1,
        )


def test_all_company_analysis_failures_do_not_commit_empty_partial_bundle():
    report = supported_report(leads=1)
    runner = SequenceRunner({"unexpected": True}, {"unexpected": True})

    with pytest.raises(DirectTalentGenerationError, match="no valid talent theme"):
        generate_direct_talent_bundle(report, target_count=5, runner=runner)
