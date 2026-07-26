"""Generate talent-pool copy through the configured OpenClaw main agent.

OpenClaw remains the model/provider control plane. This module only supplies a
strict prompt, extracts the structured response, and applies deterministic
Lead Rader safety and Liepin-contract validation before anything is persisted.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import replace
from typing import Any, Mapping, Protocol

from .talent_pool import (
    DraftBundle,
    TalentPoolDraft,
    assert_anonymized,
    canonical_payload_hash,
    generate_draft_bundle,
    is_director_plus,
    validate_liepin_payload,
)


class OpenClawGenerationError(RuntimeError):
    """Raised when OpenClaw cannot return a safe, valid draft bundle."""


class PromptRunner(Protocol):
    def run(self, prompt: str, *, session_id: str) -> str: ...


class OpenClawAgentRunner:
    """Call the server's configured OpenClaw main agent through its Gateway."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        agent: str = "main",
        thinking: str = "medium",
        timeout_seconds: int = 600,
    ) -> None:
        self.executable = executable or os.environ.get("OPENCLAW_BIN", "openclaw")
        self.agent = agent
        self.thinking = thinking
        self.timeout_seconds = timeout_seconds

    def run(self, prompt: str, *, session_id: str) -> str:
        command = [
            self.executable,
            "agent",
            "--agent",
            self.agent,
            "--session-id",
            session_id,
            "--message",
            prompt,
            "--thinking",
            self.thinking,
            "--timeout",
            str(self.timeout_seconds),
            "--json",
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds + 30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OpenClawGenerationError(
                f"OpenClaw invocation failed: {type(error).__name__}: {error}"
            ) from error
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise OpenClawGenerationError(
                f"OpenClaw exited with {completed.returncode}: {detail[-1000:]}"
            )
        return _extract_agent_text(completed.stdout)


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


def _extract_agent_text(stdout: str) -> str:
    """Extract the assistant text from OpenClaw's JSON CLI envelope."""

    envelopes = [item for item in _json_objects(stdout) if isinstance(item, dict)]
    for envelope in reversed(envelopes):
        result = envelope.get("result")
        if isinstance(result, Mapping):
            payloads = result.get("payloads")
            if isinstance(payloads, list):
                texts = [
                    item.get("text")
                    for item in payloads
                    if isinstance(item, Mapping)
                    and isinstance(item.get("text"), str)
                ]
                if texts:
                    return "\n".join(texts)
        for key in ("text", "output", "reply", "message", "content"):
            value = envelope.get(key)
            if isinstance(value, str) and value.strip():
                return value
    raise OpenClawGenerationError("OpenClaw output did not contain assistant text")


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


def _seed_payload(bundle: DraftBundle) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []
    for ordinal, draft in enumerate(bundle.drafts, start=1):
        seeds.append(
            {
                "ordinal": ordinal,
                "internal_market_signals": [
                    {
                        "company": lead.company,
                        "score": lead.score,
                        "role_hypotheses": list(lead.role_hypotheses),
                        "event_types": list(lead.event_types),
                    }
                    for lead in draft.source_leads
                ],
                "seed_role_family": draft.role_family,
                "seed_persona": draft.talent_persona,
                "seed_title": draft.recommended_title,
                "seed_attraction_angle": draft.attraction_angle,
                "required_liepin_shape": draft.public_payload,
            }
        )
    return seeds


def build_openclaw_prompt(bundle: DraftBundle) -> str:
    seeds = _seed_payload(bundle)
    return f"""
你是 Lead Rader 的“市场信号驱动人才蓄水广告生成器”。禁止调用任何工具，
只根据下面给出的内部市场信号生成严格 JSON。

目标：
- 为猎头的人才蓄水而写，不声称任何具体公司存在真实委托或正式空缺。
- 每条均为总监级以上；经理、专家、Principal、Staff、Fellow 均不允许。
- 同批画像、职责和吸引角度必须有实质差异，不能只换词。
- 公开 payload 必须彻底匿名：不得出现公司、创始人、投资人、独有产品、
  独家客户、融资轮次、精确办公地点或能反推雇主的组合线索。
- 文案有吸引力但不得捏造股权、融资、团队规模、汇报关系或薪酬承诺。
- position_scope 用中文完整写出：匿名蓄水声明、岗位使命、5-8 条职责、
  5-8 条要求和机会亮点；总长度不超过 500 个字符。
- public_payload 必须保留 required_liepin_shape 的全部字段、字段类型和枚举
  形式；work_experience_years 必须为 [10]；薪资保持 xxk，最高不超过 85k，
  区间差不超过 20k。

只返回一个 JSON 对象，不要 Markdown，不要解释：
{{
  "drafts": [
    {{
      "ordinal": 1,
      "talent_persona": "匿名、可跨客户复用的人才画像",
      "role_family": "职能族",
      "attraction_angle": "吸引角度",
      "recommended_title": "总监级以上标题",
      "why_now": "为什么现在值得蓄水，不出现公司名",
      "public_payload": {{...与 required_liepin_shape 相同的字段...}}
    }}
  ]
}}

必须恰好返回 {len(seeds)} 条，ordinal 必须完整覆盖 1..{len(seeds)}。
内部输入如下（其中公司名只用于推理，严禁复制到输出）：
{json.dumps(seeds, ensure_ascii=False, separators=(",", ":"))}
""".strip()


def _session_id(bundle: DraftBundle) -> str:
    identity = f"{bundle.source_run_id}\x1f{bundle.run_date}\x1f{bundle.direction}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "lead-rader:" + identity))


def generate_openclaw_draft_bundle(
    report: Mapping[str, Any],
    *,
    target_count: int = 5,
    runner: PromptRunner | None = None,
) -> DraftBundle:
    """Generate with OpenClaw, then fail closed on any contract/safety issue."""

    seed_bundle = generate_draft_bundle(report, target_count=target_count)
    if not seed_bundle.drafts:
        return replace(seed_bundle, schema_version=2, generation_provider="openclaw-main")

    active_runner = runner or OpenClawAgentRunner()
    response = _response_payload(
        active_runner.run(
            build_openclaw_prompt(seed_bundle),
            session_id=_session_id(seed_bundle),
        )
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
        if not is_director_plus(strings["recommended_title"]):
            raise OpenClawGenerationError(
                f"draft {ordinal} recommended_title is not Director+"
            )
        payload = value.get("public_payload")
        if not isinstance(payload, dict):
            raise OpenClawGenerationError(f"draft {ordinal} public_payload is not an object")
        if set(payload) != set(seed.public_payload):
            raise OpenClawGenerationError(
                f"draft {ordinal} public_payload fields differ from Liepin contract"
            )
        try:
            validate_liepin_payload(payload)
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
        raise OpenClawGenerationError("OpenClaw returned duplicate public payloads")
    if len({item.talent_persona for item in generated}) != len(generated):
        raise OpenClawGenerationError("OpenClaw returned duplicate talent personas")
    return DraftBundle(
        schema_version=2,
        run_date=seed_bundle.run_date,
        direction=seed_bundle.direction,
        source_run_id=seed_bundle.source_run_id,
        drafts=tuple(generated),
        generation_provider="openclaw-main",
    )


__all__ = [
    "OpenClawAgentRunner",
    "OpenClawGenerationError",
    "build_openclaw_prompt",
    "generate_openclaw_draft_bundle",
]
