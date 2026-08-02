"""Deterministic prompt mutations for the three-round V27 optimization loop."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from ht_lead_radar.aggregate_adapters.claim_adjudication import (
    CONTRACT_VERSION,
    SYSTEM_PROMPT,
    _FEW_SHOT,
)


APPENDICES = {
    "a": (
        "裁决前按固定顺序检查：第一，action_text是否逐字表达当前或明确计划中的公司动作；"
        "第二，动作承担者或融资接收者是哪一个allowed主体；第三，状态由完成词、启动词或计划词决定；"
        "第四，仅在同一事实已有更具体Claim时才拒绝摘要。不要把缺少金额、客户名或结果数字当作拒绝理由。"
    ),
    "b": (
        "采用原子事实召回策略：逐个Claim独立裁决，周报短讯只要自包含主体和当前动作就接受；"
        "历史沿革、能力介绍和编辑点评拒绝。open_action先找action_text中的谓词，再映射到最窄的"
        "allowed_event_type；发布产品或技术成果通常是technical_milestone，实际交付、发货、销售、"
        "客户采用通常是customer_validation。"
    ),
    "c": (
        "采用主体与时态优先策略：先锁定承担动作的经营公司，投资场景选择融资接收方而不是投资方；"
        "产品选择发布或运营方；任免选择发生组织变化的公司。已完成/发布/交付用completed，"
        "启动/进入用started，拟/计划/将用target，累计历史汇总用cumulative或按背景拒绝。"
        "只有原文不足以支撑主体动作时才拒绝，禁止凭行业常识补事实。"
    ),
}


EXTRA_EXAMPLES: dict[str, dict[str, Any]] = {
    "a": {
        "input": {
            "entities": [
                {"entity_id": "ae_robot", "canonical_name": "远航机器人", "aliases": []}
            ],
            "spans": [
                {"span_id": "as_plan", "text": "远航机器人计划建设年产万台的新工厂。"}
            ],
            "claims": [
                {
                    "claim_id": "ac_plan",
                    "span_id": "as_plan",
                    "event_type_hint": "factory_or_capacity",
                    "event_status_hint": "target",
                    "action_text": "计划建设年产万台的新工厂",
                    "allowed_subject_entity_ids": ["ae_robot"],
                    "primary_subject_entity_id": "ae_robot",
                }
            ],
        },
        "output": {
            "decisions": [
                {
                    "claim_id": "ac_plan",
                    "decision": "accept",
                    "subject_entity_id": "ae_robot",
                    "event_type": "factory_or_capacity",
                    "event_status": "target",
                    "funding_round": "",
                    "funding_amount": "",
                    "cumulative_funding_amount": "",
                    "investors": [],
                    "industry_tags": ["robotics"],
                    "confidence": "high",
                }
            ]
        },
    },
    "b": {
        "input": {
            "entities": [
                {"entity_id": "ae_chip", "canonical_name": "凌云芯片", "aliases": []}
            ],
            "spans": [
                {"span_id": "as_now", "text": "本周，凌云芯片发布车规级芯片。"},
                {"span_id": "as_old", "text": "凌云芯片成立于2019年，长期专注车规芯片。"},
            ],
            "claims": [
                {
                    "claim_id": "ac_now",
                    "span_id": "as_now",
                    "event_type_hint": "open_action",
                    "event_status_hint": "completed",
                    "action_text": "发布车规级芯片",
                    "allowed_subject_entity_ids": ["ae_chip"],
                    "primary_subject_entity_id": "ae_chip",
                    "allowed_event_types": ["technical_milestone", "customer_validation"],
                },
                {
                    "claim_id": "ac_old",
                    "span_id": "as_old",
                    "event_type_hint": "technical_milestone",
                    "event_status_hint": "completed",
                    "action_text": "长期专注车规芯片",
                    "allowed_subject_entity_ids": ["ae_chip"],
                    "primary_subject_entity_id": "ae_chip",
                },
            ],
        },
        "output": {
            "decisions": [
                {
                    "claim_id": "ac_now",
                    "decision": "accept",
                    "subject_entity_id": "ae_chip",
                    "event_type": "technical_milestone",
                    "event_status": "completed",
                    "funding_round": "",
                    "funding_amount": "",
                    "cumulative_funding_amount": "",
                    "investors": [],
                    "industry_tags": ["semiconductor"],
                    "confidence": "high",
                },
                {
                    "claim_id": "ac_old",
                    "decision": "reject",
                    "reason_code": "historical_or_background",
                },
            ]
        },
    },
    "c": {
        "input": {
            "entities": [
                {"entity_id": "ae_bio", "canonical_name": "星海生物", "aliases": []},
                {"entity_id": "ae_fund", "canonical_name": "远山资本", "aliases": []},
            ],
            "spans": [
                {"span_id": "as_raise", "text": "星海生物完成B轮融资，由远山资本领投。"}
            ],
            "claims": [
                {
                    "claim_id": "ac_raise",
                    "span_id": "as_raise",
                    "event_type_hint": "funding",
                    "event_status_hint": "completed",
                    "funding_round_hint": "B轮",
                    "action_text": "完成B轮融资",
                    "allowed_subject_entity_ids": ["ae_bio", "ae_fund"],
                    "primary_subject_entity_id": "ae_bio",
                }
            ],
        },
        "output": {
            "decisions": [
                {
                    "claim_id": "ac_raise",
                    "decision": "accept",
                    "subject_entity_id": "ae_bio",
                    "event_type": "funding",
                    "event_status": "completed",
                    "funding_round": "B轮",
                    "funding_amount": "",
                    "cumulative_funding_amount": "",
                    "investors": ["远山资本"],
                    "industry_tags": ["biotech"],
                    "confidence": "high",
                }
            ]
        },
    },
}


def build_variant(
    *,
    round_number: int,
    variant: str,
    parent: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if round_number not in range(1, MAX_PROMPT_ROUNDS + 1):
        raise ValueError(
            f"round_number must be between 1 and {MAX_PROMPT_ROUNDS}"
        )
    if variant not in APPENDICES:
        raise ValueError("variant must be a, b, or c")
    parent = dict(parent or {})
    system_prompt = str(parent.get("system_prompt") or SYSTEM_PROMPT)
    few_shot = deepcopy(parent.get("few_shot") or _FEW_SHOT)
    examples = list(few_shot.get("examples") or [])
    if not examples:
        examples.append(few_shot)
    examples.append(deepcopy(EXTRA_EXAMPLES[variant]))
    return {
        "system_prompt": f"{system_prompt}\n{APPENDICES[variant]}",
        "few_shot": {"examples": examples},
        "prompt_version": f"aggregate-semantic-v27-loop-r{round_number}-{variant}",
        "contract_version": str(parent.get("contract_version") or CONTRACT_VERSION),
        "lineage": {
            "round": round_number,
            "variant": variant,
            "parent_prompt_version": str(parent.get("prompt_version") or PROMPT_BASE),
        },
    }


PROMPT_BASE = "aggregate-semantic-v27-production-base"
MAX_PROMPT_ROUNDS = 3
