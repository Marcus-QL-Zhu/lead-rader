"""Evidence-bound MiniMax workflow for company demand and reusable job drafts."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import replace
from typing import Any, Mapping, Protocol

from .company_demand_v2 import (
    COMPANY_DEMAND_SYSTEM_PROMPT,
    build_company_demand_repair_prompt,
    build_company_evidence_packets,
    build_single_company_demand_prompt,
    parse_single_company_demand,
)
from .openclaw_llm import OpenClawConfiguredLLMRunner
from .talent_ad_repair import draft_response_issues
from .talent_pool import (
    DraftBundle,
    TalentPoolDraft,
    canonical_payload_hash,
)
from .talent_themes import (
    build_talent_themes,
    build_theme_draft_bundle,
)


JOB_AD_SYSTEM_PROMPT = """
你是高级猎头职位广告编辑。输入是已经由公开证据支持的人才主题。你的任务是把
该主题写成一条具体、匿名、可公开发布的 Director+ 职位广告 JSON。

每条职责、要求和技术词都应服务于输入主题的 mandate 与 specificity_terms。
只返回严格 JSON，不输出解释或分析过程。
""".strip()


class DirectTalentGenerationError(RuntimeError):
    """Raised when evidence-bound generation cannot pass deterministic checks."""


class PromptRunner(Protocol):
    def run(
        self,
        prompt: str,
        *,
        session_id: str,
        system_prompt: str = "",
    ) -> str: ...


def _json_objects(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    values: list[Any] = []
    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        values.append(value)
    return values


def _draft_response(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^\s*```(?:json)?\s*", "", text.strip(), flags=re.I)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    return next(
        (
            item
            for item in _json_objects(cleaned)
            if isinstance(item, dict) and isinstance(item.get("drafts"), list)
        ),
        {},
    )


def _session_id(source_run_id: str, phase: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"lead-rader:{source_run_id}\x1f{phase}",
        )
    )


def _forbidden_public_terms(report: Mapping[str, Any]) -> set[str]:
    terms: set[str] = set()
    for lead in report.get("leads") or ():
        if not isinstance(lead, Mapping):
            continue
        terms.add(str(lead.get("company") or "").strip())
        for evidence in lead.get("evidence") or ():
            if isinstance(evidence, Mapping):
                terms.update(
                    str(item).strip()
                    for item in evidence.get("people") or ()
                )
        research = lead.get("basic_research") or {}
        if isinstance(research, Mapping):
            for key in ("aliases", "products", "founders", "customers"):
                values = research.get(key) or ()
                if isinstance(values, str):
                    values = (values,)
                terms.update(str(item).strip() for item in values)
    return {item for item in terms if item}


def build_theme_ad_prompt(
    theme: Mapping[str, Any],
    seed: TalentPoolDraft,
) -> str:
    example = {
        "drafts": [
            {
                "ordinal": 1,
                "talent_persona": seed.talent_persona,
                "role_family": seed.role_family,
                "attraction_angle": seed.attraction_angle,
                "recommended_title": seed.recommended_title,
                "why_now": seed.why_now,
                "public_payload": seed.public_payload,
            }
        ]
    }
    return f"""
人才主题：
{json.dumps(theme, ensure_ascii=False, separators=(",", ":"))}

完整输出示例：
{json.dumps(example, ensure_ascii=False, separators=(",", ":"))}

任务：
- 输出恰好一条 draft，ordinal 固定为 1。
- recommended_title 和 position_name 使用人才主题的具体标题。
- position_scope 包含岗位使命、5–8 条核心职责、5–8 条任职要求和机会亮点，
  总长度不超过 500 个字符。
- public_payload 字段集合、类型和枚举形式与示例完全一致。
- cities 只含人才主题中的一个城市。
- 公开内容保持匿名，并自然使用至少两个 specificity_terms。
- 工龄为 [10]；薪资使用 xxk，最高不超过 85k，区间差不超过 20k。

只返回 JSON 对象 {{"drafts": [...]}}。
""".strip()


def build_theme_repair_prompt(
    theme: Mapping[str, Any],
    seed: TalentPoolDraft,
    rejected: Mapping[str, Any],
    issues: list[str],
) -> str:
    return f"""
人才主题：
{json.dumps(theme, ensure_ascii=False, separators=(",", ":"))}

字段示例：
{json.dumps(seed.public_payload, ensure_ascii=False, separators=(",", ":"))}

上一版：
{json.dumps(rejected, ensure_ascii=False, separators=(",", ":"))}

确定性校验发现：
{json.dumps(issues, ensure_ascii=False)}

请修复后返回完整 JSON 对象 {{"drafts": [{{...}}]}}。ordinal 固定为 1，
字段集合与字段示例完全一致，内容继续只对应当前人才主题。
""".strip()


def _single_bundle(bundle: DraftBundle, seed: TalentPoolDraft) -> DraftBundle:
    return replace(bundle, drafts=(seed,))


def _validate_theme_response(
    response: Mapping[str, Any],
    *,
    bundle: DraftBundle,
    seed: TalentPoolDraft,
    theme: Mapping[str, Any],
    forbidden_terms: set[str],
) -> list[str]:
    synthetic_demands = (
        {
            "hypotheses": [
                {"specificity_terms": list(theme["specificity_terms"])}
            ]
        },
    )
    issues = draft_response_issues(
        response,
        seed_bundle=_single_bundle(bundle, seed),
        company_demands=synthetic_demands,
        forbidden_terms=forbidden_terms,
    )
    values = response.get("drafts")
    if isinstance(values, list) and len(values) == 1 and isinstance(values[0], Mapping):
        value = values[0]
        if str(value.get("recommended_title") or "").strip() != str(
            theme["recommended_title"]
        ):
            issues.append("recommended_title must equal the talent theme title")
        payload = value.get("public_payload")
        if isinstance(payload, Mapping) and payload.get("cities") != [theme["city"]]:
            issues.append("cities must equal the talent theme city")
    return issues


def _materialize_draft(
    response: Mapping[str, Any],
    seed: TalentPoolDraft,
) -> TalentPoolDraft:
    value = response["drafts"][0]
    payload = dict(value["public_payload"])
    return replace(
        seed,
        talent_persona=str(value["talent_persona"]).strip(),
        role_family=str(value["role_family"]).strip(),
        attraction_angle=str(value["attraction_angle"]).strip(),
        recommended_title=str(value["recommended_title"]).strip(),
        why_now=str(value["why_now"]).strip(),
        public_payload=payload,
        payload_hash=canonical_payload_hash(payload),
    )


def generate_direct_talent_bundle(
    report: Mapping[str, Any],
    *,
    target_count: int = 5,
    runner: PromptRunner | None = None,
    deadline_seconds: float = 3600,
) -> DraftBundle:
    manifest = report.get("manifest") or {}
    source_run_id = str(manifest.get("run_id") or "")
    active_runner = runner or OpenClawConfiguredLLMRunner()
    runner_config = getattr(active_runner, "config", None)
    generation_provider = str(getattr(runner_config, "provider", "") or "")
    generation_model_name = str(getattr(runner_config, "model", "") or "")
    generation_model = (
        f"{generation_provider}/{generation_model_name}"
        if generation_provider and generation_model_name
        else generation_model_name
    )
    started_at = time.monotonic()

    def ensure_deadline() -> None:
        if time.monotonic() - started_at >= deadline_seconds:
            raise DirectTalentGenerationError(
                f"generation exceeded {deadline_seconds:g}-second deadline"
            )

    packets = build_company_evidence_packets(report)
    demands: list[dict[str, Any]] = []
    failures: list[str] = []
    for packet in packets:
        try:
            ensure_deadline()
            raw = active_runner.run(
                build_single_company_demand_prompt(packet),
                system_prompt=COMPANY_DEMAND_SYSTEM_PROMPT,
                session_id=_session_id(
                    source_run_id,
                    f"company-demand:{packet['lead_index']}",
                ),
            )
            try:
                demand = parse_single_company_demand(raw, packet=packet)
            except Exception as parse_error:
                ensure_deadline()
                repaired_raw = active_runner.run(
                    build_company_demand_repair_prompt(
                        packet,
                        raw,
                        parse_error,
                    ),
                    system_prompt=COMPANY_DEMAND_SYSTEM_PROMPT,
                    session_id=_session_id(
                        source_run_id,
                        f"company-demand-repair:{packet['lead_index']}",
                    ),
                )
                demand = parse_single_company_demand(
                    repaired_raw,
                    packet=packet,
                )
            demands.append(demand)
        except Exception as error:
            failures.append(
                f"{packet['company']}: {type(error).__name__}: {error}"
            )
            demands.append(
                {
                    "lead_index": packet["lead_index"],
                    "company": packet["company"],
                    "stage_transition": "分析失败，未形成岗位结论",
                    "organizational_gaps": [],
                    "hypotheses": [],
                    "watch_for": ["补充证据后重新分析"],
                    "analysis_error": f"{type(error).__name__}: {error}",
                }
            )
    company_demands = tuple(demands)
    themes = build_talent_themes(
        report,
        company_demands,
        target_count=target_count,
    )
    seed_bundle = build_theme_draft_bundle(
        report,
        company_demands,
        themes,
    )
    seed_bundle = replace(seed_bundle, generation_model=generation_model)
    if not themes:
        return replace(
            seed_bundle,
            generation_error="; ".join(failures),
        )

    forbidden = _forbidden_public_terms(report)
    generated: list[TalentPoolDraft] = []
    for theme, seed in zip(themes, seed_bundle.drafts, strict=True):
        ensure_deadline()
        response = _draft_response(
            active_runner.run(
                build_theme_ad_prompt(theme, seed),
                system_prompt=JOB_AD_SYSTEM_PROMPT,
                session_id=_session_id(
                    source_run_id,
                    f"job-ad:{theme['theme_id']}",
                ),
            )
        )
        issues = _validate_theme_response(
            response,
            bundle=seed_bundle,
            seed=seed,
            theme=theme,
            forbidden_terms=forbidden,
        )
        if issues:
            ensure_deadline()
            response = _draft_response(
                active_runner.run(
                    build_theme_repair_prompt(
                        theme,
                        seed,
                        response,
                        issues,
                    ),
                    system_prompt=JOB_AD_SYSTEM_PROMPT,
                    session_id=_session_id(
                        source_run_id,
                        f"job-ad-repair:{theme['theme_id']}",
                    ),
                )
            )
            issues = _validate_theme_response(
                response,
                bundle=seed_bundle,
                seed=seed,
                theme=theme,
                forbidden_terms=forbidden,
            )
        if issues:
            raise DirectTalentGenerationError(
                f"theme {theme['theme_id']} failed after one repair: "
                + "; ".join(issues)
            )
        generated.append(_materialize_draft(response, seed))
    if len({item.recommended_title for item in generated}) != len(generated):
        raise DirectTalentGenerationError("generated themes have duplicate titles")
    if len({item.payload_hash for item in generated}) != len(generated):
        raise DirectTalentGenerationError("generated themes have duplicate payloads")
    return replace(
        seed_bundle,
        drafts=tuple(generated),
        generation_error="; ".join(failures),
    )


__all__ = [
    "DirectTalentGenerationError",
    "JOB_AD_SYSTEM_PROMPT",
    "build_theme_ad_prompt",
    "generate_direct_talent_bundle",
]
