"""Preflight and one bounded repair pass for model-generated talent ads."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .talent_demand_analysis import is_specific_director_title
from .talent_pool import (
    DraftBundle,
    assert_anonymized,
    validate_liepin_payload,
)


def draft_response_issues(
    response: Mapping[str, Any],
    *,
    seed_bundle: DraftBundle,
    company_demands: tuple[dict[str, Any], ...],
    forbidden_terms: Iterable[str],
) -> list[str]:
    values = response.get("drafts")
    if not isinstance(values, list):
        return ["drafts must be a list"]
    expected_count = len(seed_bundle.drafts)
    if len(values) != expected_count:
        return [f"expected {expected_count} drafts, got {len(values)}"]
    specificity_terms = {
        term.casefold()
        for demand in company_demands
        for hypothesis in demand["hypotheses"]
        for term in hypothesis["specificity_terms"]
    }
    issues: list[str] = []
    ordinals: set[int] = set()
    payload_texts: set[str] = set()
    personas: set[str] = set()
    titles: set[str] = set()
    for fallback_ordinal, value in enumerate(values, start=1):
        if not isinstance(value, Mapping):
            issues.append(f"draft {fallback_ordinal} must be an object")
            continue
        ordinal = value.get("ordinal")
        if not isinstance(ordinal, int):
            issues.append(f"draft {fallback_ordinal} ordinal must be an integer")
            continue
        if ordinal in ordinals:
            issues.append(f"draft {ordinal} ordinal is duplicated")
        ordinals.add(ordinal)
        if not 1 <= ordinal <= expected_count:
            issues.append(f"draft {ordinal} ordinal is out of range")
            continue
        seed = seed_bundle.drafts[ordinal - 1]
        title = str(value.get("recommended_title") or "").strip()
        if not is_specific_director_title(title):
            issues.append(f"draft {ordinal} title is too broad or not Director+")
        if title in titles:
            issues.append(f"draft {ordinal} recommended_title is duplicated")
        titles.add(title)
        persona = str(value.get("talent_persona") or "").strip()
        if not persona:
            issues.append(f"draft {ordinal} talent_persona is empty")
        elif persona in personas:
            issues.append(f"draft {ordinal} talent_persona is duplicated")
        personas.add(persona)
        payload = value.get("public_payload")
        if not isinstance(payload, Mapping):
            issues.append(f"draft {ordinal} public_payload must be an object")
            continue
        if set(payload) != set(seed.public_payload):
            issues.append(f"draft {ordinal} fields differ from Liepin contract")
            continue
        try:
            validate_liepin_payload(payload)
        except ValueError as error:
            issues.append(f"draft {ordinal} Liepin contract: {error}")
        if str(payload.get("position_name") or "").strip() != title:
            issues.append(f"draft {ordinal} position_name must equal title")
        public_text = json.dumps(payload, ensure_ascii=False).casefold()
        matched = {
            term for term in specificity_terms if term and term in public_text
        }
        if len(matched) < 2:
            issues.append(
                f"draft {ordinal} contains fewer than two specificity_terms"
            )
        try:
            assert_anonymized(payload, forbidden_terms=forbidden_terms)
            assert_anonymized(
                {
                    "talent_persona": persona,
                    "role_family": value.get("role_family"),
                    "attraction_angle": value.get("attraction_angle"),
                    "recommended_title": title,
                    "why_now": value.get("why_now"),
                },
                forbidden_terms=forbidden_terms,
            )
        except ValueError as error:
            issues.append(f"draft {ordinal} anonymization: {error}")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if canonical in payload_texts:
            issues.append(f"draft {ordinal} public_payload is duplicated")
        payload_texts.add(canonical)
    expected_ordinals = set(range(1, expected_count + 1))
    if ordinals != expected_ordinals:
        issues.append("ordinals must exactly cover the seed set")
    return issues


def build_ad_repair_prompt(
    *,
    seed_bundle: DraftBundle,
    company_demands: tuple[dict[str, Any], ...],
    rejected_response: Mapping[str, Any],
    issues: list[str],
) -> str:
    shapes = [
        {
            "ordinal": index,
            "required_liepin_shape": draft.public_payload,
        }
        for index, draft in enumerate(seed_bundle.drafts, start=1)
    ]
    return f"""
Every recommended_title and talent_persona must be unique within the batch.
你刚生成的职位广告没有通过确定性校验。请根据错误列表修复，
保留已经合格的具体内容，只返回完整、严格的 JSON 对象 {{"drafts": [...]}}。

硬要求：
- 恰好 {len(seed_bundle.drafts)} 条，ordinal 完整覆盖。
- 标题必须是带赛道、技术、产品环节或商业任务的总监级以上具体标题。
- 每条公开内容至少自然包含两个 specificity_terms。
- cities 只能有一个城市。
- 不得出现公司、品牌、独有产品、创始人或客户标识。
- public_payload 字段集合与 required_liepin_shape 完全相同。

校验错误：
{json.dumps(issues, ensure_ascii=False)}

公司需求分析：
{json.dumps(company_demands, ensure_ascii=False, separators=(",", ":"))}

字段形状：
{json.dumps(shapes, ensure_ascii=False, separators=(",", ":"))}

被拒绝的上一版：
{json.dumps(rejected_response, ensure_ascii=False, separators=(",", ":"))}
""".strip()


__all__ = ["build_ad_repair_prompt", "draft_response_issues"]
