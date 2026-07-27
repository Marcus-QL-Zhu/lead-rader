"""Generate structured role analysis and job copy with a direct LLM call.

OpenClaw remains the credential and model configuration owner. Lead Radar reads
that configuration and calls the provider API without invoking an OpenClaw Agent.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import replace
from typing import Any, Mapping, Protocol

from .talent_ad_policy import advertisement_specificity_policy
from .openclaw_llm import OpenClawConfiguredLLMRunner
from .talent_ad_repair import build_ad_repair_prompt, draft_response_issues
from .talent_pool import (
    DraftBundle,
    TalentPoolDraft,
    assert_anonymized,
    build_liepin_position_scope,
    canonical_payload_hash,
    generate_draft_bundle,
    validate_liepin_payload,
)
from .talent_demand_analysis import (
    build_company_demand_prompt,
    enrich_report_with_company_demands,
    is_specific_director_title,
    parse_company_demand_analysis,
)


class OpenClawGenerationError(RuntimeError):
    """Raised when the configured LLM cannot return a safe, valid draft bundle."""


class PromptRunner(Protocol):
    def run(self, prompt: str, *, session_id: str) -> str: ...


def _json_objects(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    objects: list[Any] = []
    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        objects.append(value)
    return objects


def _response_payload(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^\s*```(?:json)?\s*", "", text.strip(), flags=re.I)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    objects = [item for item in _json_objects(cleaned) if isinstance(item, dict)]
    for item in objects:
        if isinstance(item.get("drafts"), list):
            return item
    raise OpenClawGenerationError("assistant response did not contain {drafts: [...]}")


def _forbidden_public_terms(report: Mapping[str, Any]) -> set[str]:
    terms: set[str] = set()
    for lead in report.get("leads") or ():
        if not isinstance(lead, Mapping):
            continue
        terms.add(str(lead.get("company") or "").strip())
        for evidence in lead.get("evidence") or ():
            if isinstance(evidence, Mapping):
                terms.update(str(item).strip() for item in evidence.get("people") or ())
        research = lead.get("basic_research") or {}
        if isinstance(research, Mapping):
            for key in ("aliases", "products", "founders", "customers"):
                values = research.get(key) or ()
                if isinstance(values, str):
                    values = (values,)
                terms.update(str(item).strip() for item in values)
    return {item for item in terms if item}


def _numbered(items: list[str]) -> str:
    return "；".join(f"{index}.{item}" for index, item in enumerate(items, start=1))


def _few_shot_example(
    bundle: DraftBundle,
    company_demands: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Build one fully shaped example from the first inferred demand."""

    draft = bundle.drafts[0]
    hypothesis = company_demands[0]["hypotheses"][0]
    title = str(hypothesis["specific_title"])
    mandate = str(hypothesis["mandate"])
    why_now = str(hypothesis["why_now"])
    responsibilities = [str(item) for item in hypothesis["responsibilities"]]
    must_have = [str(item) for item in hypothesis["must_have"]]
    preferred = [str(item) for item in hypothesis["preferred"]]
    payload = dict(draft.public_payload)
    payload.update(
        {
            "position_name": title,
            "position_scope": build_liepin_position_scope(
                responsibilities,
                must_have,
            ),
            "cities": [str(hypothesis["city"])],
            "must_have_signals": must_have,
            "preferred_signals": preferred,
        }
    )
    return {
        "drafts": [
            {
                "ordinal": 1,
                "talent_persona": f"能够承担‘{mandate}’任务的总监级人才",
                "role_family": title,
                "attraction_angle": why_now,
                "recommended_title": title,
                "why_now": why_now,
                "public_payload": payload,
            }
        ]
    }


def build_openclaw_prompt(
    bundle: DraftBundle,
    company_demands: tuple[dict[str, Any], ...] = (),
) -> str:
    draft_count = len(bundle.drafts)
    example = _few_shot_example(bundle, company_demands)
    return f"""
{advertisement_specificity_policy()}

你是 Lead Rader 的“市场信号驱动职位广告生成器”。请只根据给出的公司需求分析
生成严格 JSON。

目标：
- 根据市场信号写有吸引力、可公开发布的匿名职位广告。
- 每条均为总监级以上；经理、专家、Principal、Staff、Fellow 均不允许。
- 同批画像、职责和吸引角度必须有实质差异，不能只换词。
- 公开 payload 必须彻底匿名：不得出现公司、创始人、投资人、独有产品、
  独家客户、融资轮次、精确办公地点或能反推雇主的组合线索。
- 文案有吸引力但不得捏造股权、融资、团队规模、汇报关系或薪酬承诺。
- position_scope 严格使用【岗位职责】和【任职要求】两个章节，各写 5-10 行
  以“• ”开头的 bullet；总长度不超过 500 个字符。
- public_payload 是持久化后直接发布的最终 JSON，字段、类型、枚举和值与输出
  示例完全一致；job_type=社招、languages=[普通话]、seniority 无空格，
  benefits 包含五险一金；
  work_experience_years 必须为 [10]；薪资保持 xxk，最高不超过 85k，
  区间差不超过 20k。

公司需求分析：
{json.dumps(company_demands, ensure_ascii=False, separators=(",", ":"))}

单条输出示例（展示完整字段和所需具体程度）：
{json.dumps(example, ensure_ascii=False, separators=(",", ":"))}

请基于公司需求分析恰好生成 {draft_count} 条，ordinal 完整覆盖 1..{draft_count}。
每条使用相应需求推理中的具体业务任务、技术词和能力要求。
只返回一个 JSON 对象 {{"drafts": [...]}}，不要 Markdown，不要解释。
""".strip()


def _session_id(bundle: DraftBundle, phase: str) -> str:
    identity = (
        f"{bundle.source_run_id}\x1f{bundle.run_date}\x1f{bundle.direction}\x1f{phase}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "lead-rader:" + identity))


def generate_openclaw_draft_bundle(
    report: Mapping[str, Any],
    *,
    target_count: int = 5,
    runner: PromptRunner | None = None,
) -> DraftBundle:
    """Generate with the direct LLM, then fail closed on every validation issue."""

    initial_bundle = generate_draft_bundle(report, target_count=target_count)
    if not initial_bundle.drafts:
        return replace(
            initial_bundle,
            schema_version=3,
            generation_provider="direct-llm-openclaw-config-two-stage",
        )
    active_runner = runner or OpenClawConfiguredLLMRunner()
    company_demands = parse_company_demand_analysis(
        active_runner.run(
            build_company_demand_prompt(report),
            session_id=_session_id(initial_bundle, "company-demand"),
        ),
        report=report,
    )
    enhanced_report = enrich_report_with_company_demands(report, company_demands)
    seed_bundle = generate_draft_bundle(
        enhanced_report,
        target_count=target_count,
    )
    response = _response_payload(
        active_runner.run(
            build_openclaw_prompt(seed_bundle, company_demands),
            session_id=_session_id(seed_bundle, "advertisement"),
        )
    )
    forbidden = _forbidden_public_terms(report)
    issues = draft_response_issues(
        response,
        seed_bundle=seed_bundle,
        company_demands=company_demands,
        forbidden_terms=forbidden,
    )
    if issues:
        response = _response_payload(
            active_runner.run(
                build_ad_repair_prompt(
                    seed_bundle=seed_bundle,
                    company_demands=company_demands,
                    rejected_response=response,
                    issues=issues,
                ),
                session_id=_session_id(seed_bundle, "advertisement-repair"),
            )
        )
        issues = draft_response_issues(
            response,
            seed_bundle=seed_bundle,
            company_demands=company_demands,
            forbidden_terms=forbidden,
        )
        if issues:
            raise OpenClawGenerationError(
                "LLM response failed validation after one repair: " + "; ".join(issues)
            )
    values = response.get("drafts")
    if not isinstance(values, list) or len(values) != len(seed_bundle.drafts):
        raise OpenClawGenerationError(
            f"expected {len(seed_bundle.drafts)} drafts, got "
            f"{len(values) if isinstance(values, list) else 'non-list'}"
        )
    by_ordinal: dict[int, Mapping[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise OpenClawGenerationError("each draft must be an object")
        ordinal = value.get("ordinal")
        if not isinstance(ordinal, int) or ordinal in by_ordinal:
            raise OpenClawGenerationError("draft ordinals must be unique integers")
        by_ordinal[ordinal] = value
    expected = set(range(1, len(seed_bundle.drafts) + 1))
    if set(by_ordinal) != expected:
        raise OpenClawGenerationError("draft ordinals must exactly cover the seed set")

    specificity_terms = {
        term.casefold()
        for demand in company_demands
        for hypothesis in demand["hypotheses"]
        for term in hypothesis["specificity_terms"]
    }
    forbidden = _forbidden_public_terms(report)
    generated: list[TalentPoolDraft] = []
    for ordinal, seed in enumerate(seed_bundle.drafts, start=1):
        value = by_ordinal[ordinal]
        strings: dict[str, str] = {}
        for key in (
            "talent_persona",
            "role_family",
            "attraction_angle",
            "recommended_title",
            "why_now",
        ):
            text = str(value.get(key) or "").strip()
            if not text:
                raise OpenClawGenerationError(f"draft {ordinal} has empty {key}")
            strings[key] = text
        if not is_specific_director_title(strings["recommended_title"]):
            raise OpenClawGenerationError(
                f"draft {ordinal} recommended_title is too broad or not Director+"
            )
        payload = value.get("public_payload")
        if not isinstance(payload, dict):
            raise OpenClawGenerationError(
                f"draft {ordinal} public_payload is not an object"
            )
        if set(payload) != set(seed.public_payload):
            raise OpenClawGenerationError(
                f"draft {ordinal} public_payload fields differ from Liepin contract"
            )
        try:
            validate_liepin_payload(payload)
            if str(payload["position_name"]).strip() != strings["recommended_title"]:
                raise ValueError(
                    "position_name must equal the specific recommended_title"
                )
            public_text = json.dumps(payload, ensure_ascii=False).casefold()
            matched_terms = {
                term for term in specificity_terms if term and term in public_text
            }
            if len(matched_terms) < 2:
                raise ValueError(
                    "public payload must contain at least two specificity terms"
                )
            assert_anonymized(payload, forbidden_terms=forbidden)
            assert_anonymized(
                {
                    "talent_persona": strings["talent_persona"],
                    "role_family": strings["role_family"],
                    "attraction_angle": strings["attraction_angle"],
                    "recommended_title": strings["recommended_title"],
                    "why_now": strings["why_now"],
                },
                forbidden_terms=forbidden,
            )
        except ValueError as error:
            raise OpenClawGenerationError(f"draft {ordinal}: {error}") from error
        payload_hash = canonical_payload_hash(payload)
        generated.append(
            replace(
                seed,
                talent_persona=strings["talent_persona"],
                role_family=strings["role_family"],
                attraction_angle=strings["attraction_angle"],
                recommended_title=strings["recommended_title"],
                why_now=strings["why_now"],
                public_payload=dict(payload),
                payload_hash=payload_hash,
            )
        )
    if len({item.payload_hash for item in generated}) != len(generated):
        raise OpenClawGenerationError("LLM returned duplicate public payloads")
    if len({item.talent_persona for item in generated}) != len(generated):
        raise OpenClawGenerationError("LLM returned duplicate talent personas")
    if len({item.recommended_title for item in generated}) != len(generated):
        raise OpenClawGenerationError("LLM returned duplicate titles")
    return DraftBundle(
        schema_version=3,
        run_date=seed_bundle.run_date,
        direction=seed_bundle.direction,
        source_run_id=seed_bundle.source_run_id,
        drafts=tuple(generated),
        generation_provider="direct-llm-openclaw-config-two-stage",
        company_demand_analysis=company_demands,
    )


__all__ = [
    "OpenClawGenerationError",
    "build_openclaw_prompt",
    "generate_openclaw_draft_bundle",
]
