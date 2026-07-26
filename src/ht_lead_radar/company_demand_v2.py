"""Evidence-bound, one-company-at-a-time Director+ demand inference."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from typing import Any, Mapping

from .talent_demand_analysis import (
    DemandAnalysisError,
    is_specific_director_title,
)


COMPANY_DEMAND_SYSTEM_PROMPT = """
你是资深猎头研究员，负责根据企业公开事实判断未来 0–180 天内可能出现的
Director+ 组织缺口。判断顺序是：企业阶段变化 → 新增业务责任 → 缺失组织能力
→ 可能承接该责任的岗位。融资、订单或合作本身不直接等于招聘岗位。

所有事实必须引用输入中的 evidence_id。采用以下证据门槛，不要等待招聘广告：
- 两个相互独立、且共同指向同一新增责任的上游事件，可以支持 near_term 假设；
- 一个 A 级运营变化若会直接创造新责任（例如建产线、设基地、启动临床、进入新市场），可以支持 near_term 或 watchlist 假设；
- 单独融资、单独合作意向或单独招聘广告不足以支持早期岗位假设。
允许证据不足：此时返回空的 role_hypotheses，并列出可公开观察的 watch_for，
不为完成数量而猜测岗位。watch_for 不得虚构具体产量、日期、人名或招聘动作。
最终只返回严格 JSON，不输出分析过程。
""".strip()


def _evidence_id(item: Mapping[str, Any]) -> str:
    existing = str(item.get("event_id") or "").strip()
    if existing:
        return existing
    identity = "\x1f".join(
        str(item.get(key) or "").strip()
        for key in ("source_url", "event_type", "published_at", "event_date", "title")
    )
    return "ev_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _published_at(item: Mapping[str, Any]) -> str:
    return str(item.get("published_at") or item.get("event_date") or "").strip()


def _evidence_priority(item: Mapping[str, Any], position: int) -> tuple[Any, ...]:
    grade = {"A": 3, "B": 2, "C": 1}.get(
        str(item.get("source_grade") or "").upper(),
        0,
    )
    event_type = str(item.get("event_type") or "")
    upstream = 0 if event_type == "job_ad" else 1
    raw_date = _published_at(item)
    try:
        parsed = date.fromisoformat(raw_date[:10]).toordinal()
    except ValueError:
        parsed = 0
    return (-upstream, -grade, -parsed, position)


def _select_diverse_evidence(
    values: list[Mapping[str, Any]],
    *,
    limit: int = 8,
) -> list[Mapping[str, Any]]:
    """Choose independent event types before filling remaining evidence slots."""

    deduplicated: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for position, item in enumerate(values):
        key = _evidence_id(item)
        current = deduplicated.get(key)
        if current is None or _evidence_priority(item, position) < _evidence_priority(
            current[1],
            current[0],
        ):
            deduplicated[key] = (position, item)
    ranked = sorted(
        deduplicated.values(),
        key=lambda pair: _evidence_priority(pair[1], pair[0]),
    )
    by_type: dict[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for pair in ranked:
        by_type[str(pair[1].get("event_type") or "unknown")].append(pair)
    selected: list[tuple[int, Mapping[str, Any]]] = []
    for event_type in sorted(
        by_type,
        key=lambda key: _evidence_priority(by_type[key][0][1], by_type[key][0][0]),
    ):
        selected.append(by_type[event_type].pop(0))
        if len(selected) >= limit:
            break
    remaining = [pair for pairs in by_type.values() for pair in pairs]
    selected.extend(
        sorted(
            remaining,
            key=lambda pair: _evidence_priority(pair[1], pair[0]),
        )[: max(limit - len(selected), 0)]
    )
    return [item for _, item in selected[:limit]]


def build_company_evidence_packets(
    report: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    packets: list[dict[str, Any]] = []
    for lead_index, lead in enumerate(report.get("leads") or (), start=1):
        if not isinstance(lead, Mapping):
            continue
        raw_evidence = [
            item
            for item in lead.get("evidence") or ()
            if isinstance(item, Mapping)
        ]
        evidence = []
        for item in _select_diverse_evidence(raw_evidence):
            evidence.append(
                {
                    "evidence_id": _evidence_id(item),
                    "date": _published_at(item),
                    "event_type": item.get("event_type"),
                    "phase": item.get("phase"),
                    "source_grade": item.get("source_grade"),
                    "title": item.get("title"),
                    "fact": str(item.get("snippet") or "")[:800],
                    "source_url": item.get("source_url"),
                    "people": item.get("people") or [],
                    "organizations": item.get("organizations") or [],
                    "late_validation_only": item.get("event_type") == "job_ad",
                }
            )
        research = lead.get("basic_research")
        packets.append(
            {
                "lead_index": lead_index,
                "company": str(lead.get("company") or "").strip(),
                "direction": str(lead.get("direction") or ""),
                "lead_score_for_ordering_only": lead.get("score"),
                "evidence": evidence,
                "known_context": dict(research) if isinstance(research, Mapping) else {},
            }
        )
    return tuple(packets)


def build_single_company_demand_prompt(packet: Mapping[str, Any]) -> str:
    return f"""
公司事实包：
{json.dumps(packet, ensure_ascii=False, separators=(",", ":"))}

任务：
1. 先判断公司正在发生的阶段变化，以及因此新增的业务责任和组织能力缺口。
2. 按 system 中的证据门槛输出 1–3 个具体 Director+ 岗位；满足门槛时不要因为尚未发布招聘广告而放弃假设，否则输出空数组。
3. 岗位标题包含具体赛道、技术、产品环节、制造环节或商业任务，并使用“总监、VP、副总裁、总经理、首席、总师、Head、Director、CTO、COO、CEO”等无歧义的 Director+ 职级。“负责人”单独出现不算 Director+。“生产总监”“研发总监”“供应链总监”等泛称也不合格；标题结构参考“机器人小批量制造工程化总监”。
4. evidence_refs 只能填写事实包中存在的 evidence_id。
5. job_ad 只能作为晚期验证，不能作为早期岗位推断的唯一依据。
6. horizon 只能是 near_term（0–90 天）或 watchlist（91–180 天）。
7. city 只填一个城市；无法从事实判断时填空字符串，并在 city_basis 说明待核。
8. why_now 与 city_basis 只能复述或明确推导输入事实，不能把“产线在某城市”改写成“总部在该城市”；计划结果必须写成目标，不能冒充已发生事实。
9. watch_for 优先使用招聘广告之前的可观察信号，不把发布职位广告作为主要触发条件。

输出格式：
{{
  "lead_index": {int(packet["lead_index"])},
  "company": {json.dumps(packet["company"], ensure_ascii=False)},
  "stage_transition": "企业正在经历的阶段变化，证据不足则说明未知",
  "organizational_gaps": ["0-5条能力缺口"],
  "role_hypotheses": [
    {{
      "specific_title": "具体 Director+ 岗位",
      "capability_gap": "该岗位弥补的组织能力缺口",
      "mandate": "入职后需要完成的核心任务",
      "why_now": "为什么是当前或下一阶段",
      "horizon": "near_term",
      "evidence_refs": ["输入中的 evidence_id"],
      "evidence_against": ["0-4条反证或替代解释"],
      "unknowns_to_verify": ["1-5条需要人工核实的信息"],
      "key_outcomes": ["3-5条预期结果"],
      "must_have_signals": ["3-5条候选人关键能力"],
      "preferred_signals": ["1-3条加分能力；必须是候选人特征，不能写待核问题"],
      "specificity_terms": ["3-8个匿名广告可用词"],
      "city": "一个城市或空字符串",
      "city_basis": "城市依据或待核原因"
    }}
  ],
  "watch_for": ["没有可辩护岗位时，列出1-5个后续触发信号"]
}}

只返回上述 JSON 对象。
""".strip()


def build_company_demand_repair_prompt(
    packet: Mapping[str, Any],
    rejected_response: str,
    error: Exception,
) -> str:
    return f"""
公司事实包：
{json.dumps(packet, ensure_ascii=False, separators=(",", ":"))}

上一版输出：
{rejected_response}

确定性校验错误：
{type(error).__name__}: {error}

请只修复上述错误，并重新返回完整的单公司 JSON。仍须只引用事实包里的 evidence_id；
不得用“生产总监、研发总监、供应链总监、负责人”等含混标题；如果没有可辩护的
具体 Director+ 岗位，则返回空 role_hypotheses 和可观察的 watch_for。
""".strip()


def _json_objects(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    result: list[Any] = []
    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        result.append(value)
    return result


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    result = str(value or "").strip()
    if not result and not allow_empty:
        raise DemandAnalysisError(f"{field} must not be empty")
    return result


def _texts(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> list[str]:
    if not isinstance(value, list):
        raise DemandAnalysisError(f"{field} must be a list")
    result = [_text(item, field) for item in value]
    if not minimum <= len(result) <= maximum:
        raise DemandAnalysisError(
            f"{field} must contain {minimum}-{maximum} items"
        )
    if len(result) != len(set(result)):
        raise DemandAnalysisError(f"{field} must not contain duplicates")
    return result


OPERATIONAL_ROLE_SIGNAL_TYPES = frozenset(
    {
        "factory_or_capacity",
        "new_site_or_entity",
        "product_launch",
        "clinical_trial",
        "regulatory_approval",
        "major_order",
        "market_expansion",
        "global_expansion",
        "commercialization",
        "project_buildout",
        "procurement_tender",
        "procurement_intention",
        "eia_or_permit",
        "regulatory_or_clinical",
        "technical_milestone",
        "executive_change",
    }
)


def _validate_evidence_gate(
    packet: Mapping[str, Any],
    refs: list[str],
    horizon: str,
) -> None:
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in packet.get("evidence") or ()
        if isinstance(item, Mapping)
    }
    selected = [evidence_by_id[ref] for ref in refs]
    upstream = [
        item
        for item in selected
        if not item.get("late_validation_only")
        and str(item.get("event_type") or "") != "job_ad"
    ]
    event_types = {
        str(item.get("event_type") or "")
        for item in upstream
        if str(item.get("event_type") or "")
    }
    diverse_upstream = len(upstream) >= 2 and len(event_types) >= 2
    operational = [
        item
        for item in upstream
        if str(item.get("event_type") or "") in OPERATIONAL_ROLE_SIGNAL_TYPES
    ]
    strong_operational = any(
        str(item.get("source_grade") or "").upper() == "A"
        for item in operational
    )
    if horizon == "near_term" and not (diverse_upstream or strong_operational):
        raise DemandAnalysisError(
            "near_term requires two diverse upstream events or one A-grade "
            "operational event"
        )
    if horizon == "watchlist" and not (diverse_upstream or operational):
        raise DemandAnalysisError(
            "watchlist requires an operational event or two diverse upstream events"
        )


def parse_single_company_demand(
    text: str,
    *,
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    value = next(
        (
            item
            for item in _json_objects(text)
            if isinstance(item, Mapping)
            and isinstance(item.get("role_hypotheses"), list)
        ),
        None,
    )
    if value is None:
        raise DemandAnalysisError("response has no company demand JSON object")
    lead_index = value.get("lead_index")
    if lead_index != packet.get("lead_index"):
        raise DemandAnalysisError("lead_index does not match the company packet")
    company = _text(value.get("company"), "company")
    if company != packet.get("company"):
        raise DemandAnalysisError("company does not match the company packet")
    evidence_ids = {
        str(item.get("evidence_id") or "")
        for item in packet.get("evidence") or ()
        if isinstance(item, Mapping)
    }
    hypotheses_value = value["role_hypotheses"]
    if len(hypotheses_value) > 3:
        raise DemandAnalysisError("role_hypotheses must contain 0-3 items")
    hypotheses: list[dict[str, Any]] = []
    for role_index, raw in enumerate(hypotheses_value, start=1):
        if not isinstance(raw, Mapping):
            raise DemandAnalysisError("each role hypothesis must be an object")
        title = _text(raw.get("specific_title"), "specific_title")
        if not is_specific_director_title(title):
            raise DemandAnalysisError(f"specific_title is too broad: {title}")
        refs = _texts(raw.get("evidence_refs"), "evidence_refs", minimum=1, maximum=6)
        if not set(refs).issubset(evidence_ids):
            raise DemandAnalysisError("evidence_refs contain unknown evidence IDs")
        horizon = _text(raw.get("horizon"), "horizon")
        if horizon not in {"near_term", "watchlist"}:
            raise DemandAnalysisError("horizon must be near_term or watchlist")
        _validate_evidence_gate(packet, refs, horizon)
        city = _text(raw.get("city"), "city", allow_empty=True)
        if any(separator in city for separator in (",", "，", "、", "/", "；")):
            raise DemandAnalysisError("city must contain at most one city")
        hypotheses.append(
            {
                "hypothesis_id": f"lead_{lead_index}_role_{role_index}",
                "specific_title": title,
                "capability_gap": _text(raw.get("capability_gap"), "capability_gap"),
                "mandate": _text(raw.get("mandate"), "mandate"),
                "why_now": _text(raw.get("why_now"), "why_now"),
                "horizon": horizon,
                "evidence_refs": refs,
                "evidence_against": _texts(
                    raw.get("evidence_against") or [],
                    "evidence_against",
                    minimum=0,
                    maximum=4,
                ),
                "unknowns_to_verify": _texts(
                    raw.get("unknowns_to_verify"),
                    "unknowns_to_verify",
                    minimum=1,
                    maximum=5,
                ),
                "key_outcomes": _texts(
                    raw.get("key_outcomes"),
                    "key_outcomes",
                    minimum=3,
                    maximum=5,
                ),
                "must_have_signals": _texts(
                    raw.get("must_have_signals"),
                    "must_have_signals",
                    minimum=3,
                    maximum=5,
                ),
                "preferred_signals": _texts(
                    raw.get("preferred_signals"),
                    "preferred_signals",
                    minimum=1,
                    maximum=3,
                ),
                "specificity_terms": _texts(
                    raw.get("specificity_terms"),
                    "specificity_terms",
                    minimum=3,
                    maximum=8,
                ),
                "city": city,
                "city_basis": _text(raw.get("city_basis"), "city_basis"),
            }
        )
    watch_for = _texts(
        value.get("watch_for") or [],
        "watch_for",
        minimum=1 if not hypotheses else 0,
        maximum=5,
    )
    return {
        "lead_index": lead_index,
        "company": company,
        "stage_transition": _text(value.get("stage_transition"), "stage_transition"),
        "organizational_gaps": _texts(
            value.get("organizational_gaps") or [],
            "organizational_gaps",
            minimum=0,
            maximum=5,
        ),
        "hypotheses": hypotheses,
        "watch_for": watch_for,
    }


__all__ = [
    "COMPANY_DEMAND_SYSTEM_PROMPT",
    "OPERATIONAL_ROLE_SIGNAL_TYPES",
    "build_company_demand_repair_prompt",
    "build_company_evidence_packets",
    "build_single_company_demand_prompt",
    "parse_single_company_demand",
]
