"""Company-level Director+ demand inference before talent-ad generation."""

from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from .talent_pool import is_director_plus


GENERIC_DIRECTOR_TITLES = frozenset(
    {
        "研发总监",
        "硬科技研发总监",
        "技术总监",
        "产品总监",
        "硬科技产品总监",
        "供应链总监",
        "业务拓展总监",
        "销售总监",
        "战略与运营总监",
        "运营总监",
        "人力资源总监",
    }
)


class DemandAnalysisError(ValueError):
    """Raised when company-demand inference is incomplete or ungrounded."""


def _compact_report_leads(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for index, lead in enumerate(report.get("leads") or (), start=1):
        if not isinstance(lead, Mapping):
            continue
        evidence = []
        for item in (lead.get("evidence") or ())[:6]:
            if not isinstance(item, Mapping):
                continue
            evidence.append(
                {
                    "event_type": item.get("event_type"),
                    "title": item.get("title"),
                    "snippet": item.get("snippet"),
                    "published_at": item.get("published_at"),
                    "source_url": item.get("source_url"),
                }
            )
        compact.append(
            {
                "lead_index": index,
                "company": str(lead.get("company") or "").strip(),
                "score": lead.get("score"),
                "existing_role_hypotheses": lead.get("target_roles") or [],
                "evidence": evidence,
                "basic_research": lead.get("basic_research") or {},
            }
        )
    return compact


def build_company_demand_prompt(report: Mapping[str, Any]) -> str:
    leads = _compact_report_leads(report)
    return f"""
你是资深硬科技猎头研究员。请只根据给定事实，逐家公司推测未来
6 个月最可能新增或升级的总监级以上岗位，并把“为什么需要这个岗位”拆成具体
业务任务和候选人能力。这里是内部分析，可以保留公司名，但必须区分事实与推测。

要求：
- 每家公司输出 1–3 个岗位，排除经理、专家、Principal、Staff、Fellow。
- 标题必须带具体赛道、技术、产品、制造环节或商业任务，禁止只写“研发总监”
  “产品总监”“供应链总监”“硬科技研发总监”等泛化标题。
- mandate 必须说明入职后解决的具体问题，不能只写“负责团队和业务”。
- responsibilities 和 must_have 各 5–8 条，必须结合该公司的产品阶段、技术栈、
  量产环节、客户类型或市场动作；不确定的内容明确写成推测，不能冒充事实。
- specificity_terms 给出 3–8 个可以安全写进匿名广告的赛道/技术/能力词。
  不得放公司名、创始人、独有产品名或其他可识别雇主的信息。
- city 是内部建议的单一招聘城市；证据不足时使用“上海”作为人才池默认城市，
  不得返回多个城市。
- grounding 说明推测依据了哪些输入事件；不得补造输入中没有的融资、客户、
  团队规模、薪酬、汇报线或地点事实。

只返回严格 JSON：
{{
  "company_demands": [
    {{
      "lead_index": 1,
      "company": "必须与输入完全一致",
      "hypotheses": [
        {{
          "specific_title": "具体的总监级以上标题",
          "mandate": "具体业务使命",
          "why_now": "为什么现在可能需要",
          "responsibilities": ["5-8条"],
          "must_have": ["5-8条"],
          "preferred": ["2-5条"],
          "specificity_terms": ["3-8个"],
          "city": "一个城市",
          "grounding": ["输入证据与推测关系"]
        }}
      ]
    }}
  ]
}}

必须完整覆盖所有 lead_index，不能增删或重排公司：
{json.dumps(leads, ensure_ascii=False, separators=(",", ":"))}
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


def _string(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DemandAnalysisError(f"{field} must not be empty")
    return text


def _string_list(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> list[str]:
    if not isinstance(value, list):
        raise DemandAnalysisError(f"{field} must be a list")
    items = [_string(item, field=field) for item in value]
    if not minimum <= len(items) <= maximum:
        raise DemandAnalysisError(
            f"{field} must contain {minimum}-{maximum} items"
        )
    if len(set(items)) != len(items):
        raise DemandAnalysisError(f"{field} must not contain duplicates")
    return items


def is_specific_director_title(title: str) -> bool:
    normalized = title.strip()
    collapsed = normalized.replace("\u4e0e", "")
    collapsed_generic = {item.replace("\u4e0e", "") for item in GENERIC_DIRECTOR_TITLES}
    explicit_director_markers = (
        "总监",
        "副总裁",
        "总经理",
        "首席",
        "总师",
        "Director",
        "Head",
        "VP",
        "CTO",
        "COO",
        "CEO",
    )
    return (
        is_director_plus(normalized)
        and any(marker.lower() in normalized.lower() for marker in explicit_director_markers)
        and normalized not in GENERIC_DIRECTOR_TITLES
        and collapsed not in collapsed_generic
        and len(normalized) >= 6
    )


def parse_company_demand_analysis(
    text: str,
    *,
    report: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    response = next(
        (
            item
            for item in _json_objects(text)
            if isinstance(item, Mapping)
            and isinstance(item.get("company_demands"), list)
        ),
        None,
    )
    if response is None:
        raise DemandAnalysisError(
            "assistant response did not contain {company_demands: [...]}"
        )
    leads = [
        item for item in report.get("leads") or () if isinstance(item, Mapping)
    ]
    values = response["company_demands"]
    if len(values) != len(leads):
        raise DemandAnalysisError(
            f"expected {len(leads)} company demands, got {len(values)}"
        )
    by_index: dict[int, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise DemandAnalysisError("each company demand must be an object")
        lead_index = value.get("lead_index")
        if not isinstance(lead_index, int) or lead_index in by_index:
            raise DemandAnalysisError("lead_index must be a unique integer")
        if not 1 <= lead_index <= len(leads):
            raise DemandAnalysisError("lead_index is out of range")
        expected_company = str(leads[lead_index - 1].get("company") or "").strip()
        company = _string(value.get("company"), field="company")
        if company != expected_company:
            raise DemandAnalysisError(
                f"company mismatch at lead_index {lead_index}"
            )
        hypotheses_value = value.get("hypotheses")
        if not isinstance(hypotheses_value, list) or not 1 <= len(
            hypotheses_value
        ) <= 3:
            raise DemandAnalysisError("hypotheses must contain 1-3 items")
        hypotheses: list[dict[str, Any]] = []
        for hypothesis in hypotheses_value:
            if not isinstance(hypothesis, Mapping):
                raise DemandAnalysisError("each hypothesis must be an object")
            title = _string(
                hypothesis.get("specific_title"),
                field="specific_title",
            )
            if not is_specific_director_title(title):
                raise DemandAnalysisError(
                    f"specific_title is too broad or not Director+: {title}"
                )
            city = _string(hypothesis.get("city"), field="city")
            if any(separator in city for separator in (",", "，", "、", "/", "；")):
                raise DemandAnalysisError("city must contain exactly one city")
            hypotheses.append(
                {
                    "specific_title": title,
                    "mandate": _string(hypothesis.get("mandate"), field="mandate"),
                    "why_now": _string(hypothesis.get("why_now"), field="why_now"),
                    "responsibilities": _string_list(
                        hypothesis.get("responsibilities"),
                        field="responsibilities",
                        minimum=5,
                        maximum=8,
                    ),
                    "must_have": _string_list(
                        hypothesis.get("must_have"),
                        field="must_have",
                        minimum=5,
                        maximum=8,
                    ),
                    "preferred": _string_list(
                        hypothesis.get("preferred"),
                        field="preferred",
                        minimum=2,
                        maximum=5,
                    ),
                    "specificity_terms": _string_list(
                        hypothesis.get("specificity_terms"),
                        field="specificity_terms",
                        minimum=3,
                        maximum=8,
                    ),
                    "city": city,
                    "grounding": _string_list(
                        hypothesis.get("grounding"),
                        field="grounding",
                        minimum=1,
                        maximum=6,
                    ),
                }
            )
        by_index[lead_index] = {
            "lead_index": lead_index,
            "company": company,
            "hypotheses": hypotheses,
        }
    expected_indexes = set(range(1, len(leads) + 1))
    if set(by_index) != expected_indexes:
        raise DemandAnalysisError("company demands must cover every lead_index")
    return tuple(by_index[index] for index in sorted(by_index))


def enrich_report_with_company_demands(
    report: Mapping[str, Any],
    company_demands: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    enhanced = copy.deepcopy(dict(report))
    leads = enhanced.get("leads") or []
    by_index = {item["lead_index"]: item for item in company_demands}
    for index, lead in enumerate(leads, start=1):
        demand = by_index.get(index)
        if not isinstance(lead, dict) or not demand:
            continue
        lead["target_roles"] = [
            hypothesis["specific_title"]
            for hypothesis in demand["hypotheses"]
        ]
        lead["agent_demand_analysis"] = demand
    return enhanced


__all__ = [
    "DemandAnalysisError",
    "build_company_demand_prompt",
    "enrich_report_with_company_demands",
    "is_specific_director_title",
    "parse_company_demand_analysis",
]
