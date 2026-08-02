"""Claim-centric MiniMax adjudication with host-owned entities and citations."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha1
import json
import re
from typing import Any, Mapping, Protocol, Sequence

from .action_span_ledger import ActionSpanLedger, AtomicClaim, build_action_span_ledger
from .document_router import DocumentRoute, route_document
from .entity_ledger import ArticleEntityLedger, build_article_entity_ledger
from .models import CleanArticle, SemanticEvent, SourceChannel


PROMPT_VERSION = "aggregate-semantic-v27-claim-centric-r5"
CONTRACT_VERSION = "v5-open-action-ledger"
ALLOWED_EVENT_TYPES = frozenset(
    {
        "funding",
        "executive_change",
        "factory_or_capacity",
        "major_order",
        "partnership",
        "technical_milestone",
        "new_site_or_entity",
        "regulatory_or_clinical",
        "policy_or_standard",
        "procurement_tender",
        "customer_validation",
        "merger_acquisition",
        "ipo_or_listing",
        "enterprise_system",
        "workforce_cluster",
        "research_or_ip",
        "project_buildout",
        "global_expansion",
        "workforce_cluster",
        "research_or_ip",
        "project_buildout",
        "global_expansion",
        "workforce_cluster",
        "research_or_ip",
        "project_buildout",
        "global_expansion",
        "workforce_cluster",
        "research_or_ip",
        "project_buildout",
        "global_expansion",
        "workforce_cluster",
        "research_or_ip",
        "project_buildout",
        "global_expansion",
        "other",
    }
)
ALLOWED_STATUSES = frozenset({"completed", "started", "target", "cumulative"})
ALLOWED_CONFIDENCE = frozenset({"high", "medium", "low", "unknown"})
ALLOWED_REJECTION_REASONS = frozenset(
    {
        "unsupported_company_action",
        "subject_mismatch",
        "historical_or_background",
        "duplicate_or_summary",
        "ambiguous_evidence",
    }
)

SYSTEM_PROMPT = (
    "你是公司事件Claim裁决器。输入中的实体、Claim与原文Span均由宿主锁定。"
    "你只判断每个Claim应接受还是拒绝；接受时从allowed_subject_entity_ids选择一个主体，"
    "判断event_status，并提取原文逐字存在的融资字段与投资方。"
    "事件不限于融资；融资、量产扩产、订单、合作、产品技术发布、客户验证与销售发货、"
    "监管临床、并购控制权、上市、新实体和企业系统等明确公司动作都可接受。"
    "event_type_hint通常是宿主锁定的动作类型；接受时event_type必须原样返回event_type_hint。"
    "唯一例外是event_type_hint=open_action：此时必须从allowed_event_types中选择最准确的类型，"
    "若原文不足以确定公司经营动作则拒绝，严禁返回open_action。"
    "funding_round_hint非空时也由宿主锁定，funding_round必须原样返回。"
    "若Span确实支持该动作，不要因没有融资字段而拒绝。"
    "host_mandatory=true表示宿主高精度规则已锁定该动作，不得拒绝。"
    "只围绕每个Claim自己的action_text裁决；同一Span若有多个独立action_text，多个Claim可以分别接受。"
    "主体是承担该动作的经营公司，不是产品、模型、平台、部门、人物、媒体或栏目短语。"
    "primary_subject_entity_id非空时代表宿主按语法识别的首选主体，应优先采用；"
    "但融资/投资的主体必须是获得资金的公司，不能选投资方。"
    "产品技术事件选择发布或运营该产品的公司；高管变动选择发生组织任免、加入或离开的公司。"
    "标题复述、评论推断和随后有明确详情的摘要Claim应拒绝为duplicate_or_summary。"
    "但周报中自包含的公司融资事实，只要逐字给出主体、完成融资动作以及金额或轮次，必须接受；"
    "不能仅因它位于汇总栏目而拒绝。"
    "公开披露的拟、计划、将要执行的公司动作是有效事件，event_status应为target。"
    "不能用event_type_mismatch拒绝：事件类型已由宿主锁定，只判断该action_text是否真实表达公司动作。"
    "拒绝reason_code只能是unsupported_company_action、subject_mismatch、"
    "historical_or_background、duplicate_or_summary、ambiguous_evidence之一。"
    "每个输入claim_id必须且只能出现一次。只输出JSON对象，不输出Markdown。"
    "格式为{\"decisions\":[{\"claim_id\":\"ac_x\",\"decision\":\"accept\","
    "\"subject_entity_id\":\"ae_x\",\"event_type\":\"funding\","
    "\"event_status\":\"completed\",\"funding_round\":\"A轮\","
    "\"funding_amount\":\"1亿元\",\"cumulative_funding_amount\":\"\","
    "\"investors\":[\"远山资本\"],\"industry_tags\":[\"semiconductor\"],"
    "\"confidence\":\"high\"}]}。拒绝时只填claim_id、decision=reject和reason_code。"
)

_FEW_SHOT = {
    "input": {
        "entities": [
            {"entity_id": "ae_chip", "canonical_name": "星河芯片", "aliases": []},
            {"entity_id": "ae_investor", "canonical_name": "远山资本", "aliases": []},
            {"entity_id": "ae_platform", "canonical_name": "星河智能平台", "aliases": []},
        ],
        "spans": [
            {"span_id": "as_funding", "text": "远山资本将向星河芯片投资1亿元。"},
            {"span_id": "as_product", "text": "星河芯片正式发布星河智能平台。"},
            {"span_id": "as_summary", "text": "本周硬科技融资信号密集，星河芯片融资规模创新高。"},
        ],
        "claims": [
            {
                "claim_id": "ac_funding",
                "span_id": "as_funding",
                "event_type_hint": "funding",
                "event_status_hint": "target",
                "action_text": "向星河芯片投资1亿元",
                "allowed_subject_entity_ids": ["ae_investor", "ae_chip"],
                "primary_subject_entity_id": "ae_investor",
            },
            {
                "claim_id": "ac_product",
                "span_id": "as_product",
                "event_type_hint": "technical_milestone",
                "event_status_hint": "completed",
                "action_text": "发布星河智能平台",
                "allowed_subject_entity_ids": ["ae_chip", "ae_platform"],
                "primary_subject_entity_id": "ae_chip",
            },
            {
                "claim_id": "ac_summary",
                "span_id": "as_summary",
                "event_type_hint": "funding",
                "event_status_hint": "completed",
                "action_text": "融资规模创新高",
                "allowed_subject_entity_ids": ["ae_chip"],
                "primary_subject_entity_id": "ae_chip",
            },
        ],
    },
    "output": {
        "decisions": [
            {
                "claim_id": "ac_funding",
                "decision": "accept",
                "subject_entity_id": "ae_chip",
                "event_type": "funding",
                "event_status": "target",
                "funding_round": "",
                "funding_amount": "1亿元",
                "cumulative_funding_amount": "",
                "investors": ["远山资本"],
                "industry_tags": ["semiconductor"],
                "confidence": "high",
            },
            {
                "claim_id": "ac_product",
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
                "claim_id": "ac_summary",
                "decision": "reject",
                "reason_code": "duplicate_or_summary",
            },
        ]
    },
}


class PromptRunner(Protocol):
    def run(
        self,
        prompt: str,
        *,
        session_id: str,
        system_prompt: str = "",
    ) -> str: ...


class ClaimDecisionError(ValueError):
    pass


def _is_infrastructure_error(error: Exception) -> bool:
    name = type(error).__name__
    message = str(error).casefold()
    return bool(
        name in {
            "DirectLLMError",
            "TimeoutError",
            "URLError",
            "ConnectionError",
        }
        or "provider request failed" in message
        or "timed out" in message
        or "connection refused" in message
    )


def _parse_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]).strip()
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ClaimDecisionError("model output must be one JSON object")
    return payload


def _grounded_string(value: Any, span_text: str, field: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ClaimDecisionError(f"{field} must be a string")
    cleaned = value.strip()
    if cleaned.casefold() in {
        "未披露",
        "未公布",
        "未知",
        "n/a",
        "na",
        "unknown",
        "undisclosed",
        "-",
    }:
        return ""
    if cleaned and cleaned not in span_text:
        raise ClaimDecisionError(f"{field} is not grounded in the claim span")
    return cleaned


def _valuation_only_funding_amount(value: str, span_text: str) -> bool:
    """Reject a valuation figure mistakenly emitted as this-round funding."""

    if not value:
        return False
    position = span_text.find(value)
    if position < 0:
        return False
    prefix = span_text[max(0, position - 24) : position]
    if not re.search(r"估值|投前|投后", prefix):
        return False
    # Keep amounts explicitly attached to a financing amount/scale phrase.
    return not re.search(r"融资(?:金额|规模)?|募资|募集资金", prefix)


def _grounded_list(value: Any, span_text: str, field: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ClaimDecisionError(f"{field} must be a string list")
    output = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
    if field == "investors" and any(item not in span_text for item in output):
        raise ClaimDecisionError("investor is not grounded in the claim span")
    return output


def _batch_prompt(
    batch: Sequence[AtomicClaim],
    action_ledger: ActionSpanLedger,
    entity_ledger: ArticleEntityLedger,
    *,
    contract_version: str = CONTRACT_VERSION,
    few_shot: Mapping[str, Any] = _FEW_SHOT,
    route: DocumentRoute | None = None,
) -> str:
    span_by_id = action_ledger.spans_by_id()
    entity_by_id = entity_ledger.by_id()
    entity_ids = {
        entity_id for claim in batch for entity_id in claim.allowed_subject_entity_ids
    }
    payload = {
        "contract_version": contract_version,
        "route_gate": {
            "document_type": route.document_type if route else "",
            "document_family": route.document_family if route else "",
            "processing_mode": route.processing_mode if route else "",
            "confidence": route.gate_confidence if route else "",
            "llm_required": route.llm_gate_required if route else False,
            "signals": list(route.gate_signals) if route else [],
        },
        "entities": [
            entity_by_id[entity_id].to_prompt_dict() for entity_id in sorted(entity_ids)
        ],
        "spans": [
            {
                "span_id": span_id,
                "text": span_by_id[span_id].text,
            }
            for span_id in dict.fromkeys(claim.span_id for claim in batch)
        ],
        "claims": [claim.to_prompt_dict() for claim in batch],
    }
    return (
        "few_shot="
        + json.dumps(few_shot, ensure_ascii=False, separators=(",", ":"))
        + "\ninput="
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _normalize_rejection_reason(reason: str, claim_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", reason.casefold()).strip("_")
    if normalized in ALLOWED_REJECTION_REASONS:
        return normalized
    if "event_type" in normalized or "type_mismatch" in normalized:
        raise ClaimDecisionError(
            f"event_type_mismatch is a protocol error:{claim_id}"
        )
    aliases = (
        (
            (
                "subject_mismatch",
                "subject_entity_mismatch",
                "span_subject_mismatch",
                "action_subject_mismatch",
                "action_subject_misalignment",
                "no_subject_in_span",
                "missing_subject_entity_in_span",
            ),
            "subject_mismatch",
        ),
        (
            ("historical", "background", "stale", "not_current"),
            "historical_or_background",
        ),
        (
            ("duplicate", "summary", "cumulative", "already_covered"),
            "duplicate_or_summary",
        ),
        (("ambiguous", "unclear", "uncertain"), "ambiguous_evidence"),
        (
            (
                "unsupported",
                "not_company_event",
                "not_company_action",
                "no_company_action",
                "no_action",
                "not_an_event",
                "insufficient_evidence",
                "action_text_not_company_initiated",
            ),
            "unsupported_company_action",
        ),
    )
    for needles, canonical in aliases:
        if any(needle in normalized for needle in needles):
            return canonical
    raise ClaimDecisionError(f"invalid rejection reason:{claim_id}:{normalized}")


def _validate_batch_decisions(
    payload: Mapping[str, Any],
    batch: Sequence[AtomicClaim],
    *,
    ignore_unknown: bool = False,
) -> dict[str, dict[str, Any]]:
    raw = payload.get("decisions")
    if not isinstance(raw, list):
        raise ClaimDecisionError("decisions must be a list")
    expected = {claim.claim_id for claim in batch}
    output: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ClaimDecisionError("every decision must be an object")
        claim_id = str(item.get("claim_id") or "")
        if claim_id not in expected:
            if ignore_unknown and claim_id:
                continue
            raise ClaimDecisionError(f"unknown claim_id:{claim_id}")
        if claim_id in output:
            raise ClaimDecisionError(f"duplicate claim_id:{claim_id}")
        decision = str(item.get("decision") or "")
        if decision not in {"accept", "reject"}:
            raise ClaimDecisionError(f"invalid decision:{claim_id}")
        if decision == "reject":
            reason = str(item.get("reason_code") or "")
            if len(reason) > 96:
                raise ClaimDecisionError(f"rejection reason is too long:{claim_id}")
            item = dict(item)
            item["reason_code"] = _normalize_rejection_reason(reason, claim_id)
        output[claim_id] = dict(item)
    missing = expected - set(output)
    if missing:
        raise ClaimDecisionError(f"missing decisions:{','.join(sorted(missing))}")
    return output


def _funding_recipient_entity_id(
    claim: AtomicClaim,
    span_text: str,
    entity_ledger: ArticleEntityLedger,
) -> str:
    """Resolve the funded company without asking the model to infer role labels."""

    entity_by_id = entity_ledger.by_id()
    allowed = [
        entity_by_id[entity_id]
        for entity_id in claim.allowed_subject_entity_ids
        if entity_id in entity_by_id
    ]
    action = claim.action_text
    for entity in allowed:
        for name in (entity.canonical_name, *entity.aliases):
            if name and re.search(
                rf"向[^，,。；]{{0,30}}{re.escape(name)}[^，,。；]{{0,20}}投资",
                action,
            ):
                return entity.entity_id
    if "投资" not in action or len(allowed) < 2:
        return ""
    relative_action_start = max(0, claim.action_char_start)
    # Claim offsets are article absolute; locate the action within the immutable
    # span to obtain a bounded grammatical prefix.
    local_action_start = span_text.find(action)
    if local_action_start >= 0:
        relative_action_start = local_action_start
    prefix = span_text[max(0, relative_action_start - 100) : relative_action_start]
    investor_ids = []
    for entity in allowed:
        positions = [
            prefix.rfind(name)
            for name in (entity.canonical_name, *entity.aliases)
            if name
        ]
        if positions and max(positions) >= 0:
            investor_ids.append((max(positions), entity.entity_id))
    if not investor_ids:
        return ""
    investor_id = max(investor_ids)[1]
    recipients = [
        entity.entity_id for entity in allowed if entity.entity_id != investor_id
    ]
    if len(recipients) == 1:
        return recipients[0]
    if re.search(r"该初创(?:公司|企业)|这家初创(?:公司|企业)", span_text):
        future_mentions: list[tuple[int, str]] = []
        for entity in allowed:
            if entity.entity_id == investor_id:
                continue
            distances = [
                mention.char_start - claim.action_char_end
                for mention in entity.mentions
                if claim.action_char_end <= mention.char_start <= claim.action_char_end + 320
            ]
            if distances:
                future_mentions.append((min(distances), entity.entity_id))
        if future_mentions:
            future_mentions.sort()
            if len(future_mentions) == 1 or future_mentions[0][0] < future_mentions[1][0]:
                return future_mentions[0][1]
    return ""


def _decision_to_event(
    decision: Mapping[str, Any],
    claim: AtomicClaim,
    article: CleanArticle,
    action_ledger: ActionSpanLedger,
    entity_ledger: ArticleEntityLedger,
    *,
    prompt_version: str = PROMPT_VERSION,
) -> SemanticEvent | None:
    span = action_ledger.spans_by_id()[claim.span_id]
    if decision.get("decision") == "reject":
        if claim.host_mandatory:
            raise ClaimDecisionError(
                f"host-mandatory claim rejected:{claim.claim_id}"
            )
        return None
    returned_event_type = str(decision.get("event_type") or "")
    if claim.event_type_hint == "open_action":
        if returned_event_type not in claim.allowed_event_types:
            raise ClaimDecisionError(
                f"event_type is outside open-action taxonomy:{claim.claim_id}"
            )
        event_type = returned_event_type
    else:
        if returned_event_type != claim.event_type_hint:
            raise ClaimDecisionError(f"event_type is not host-locked:{claim.claim_id}")
        event_type = claim.event_type_hint
    entity_id = str(decision.get("subject_entity_id") or "")
    if entity_id not in claim.allowed_subject_entity_ids:
        raise ClaimDecisionError(f"subject is not allowed:{claim.claim_id}")
    model_entity_id = entity_id
    if event_type == "new_site_or_entity" and claim.primary_subject_entity_id:
        entity_id = claim.primary_subject_entity_id
    if event_type == "customer_validation" and claim.primary_subject_entity_id:
        entity_id = claim.primary_subject_entity_id
    if event_type == "factory_or_capacity" and claim.primary_subject_entity_id:
        entity_id = claim.primary_subject_entity_id
    if (
        event_type == "partnership"
        and claim.primary_subject_entity_id
        and claim.action_text.startswith("达成")
    ):
        entity_id = claim.primary_subject_entity_id
    if event_type == "funding":
        recipient_id = _funding_recipient_entity_id(
            claim, span.text, entity_ledger
        )
        if recipient_id:
            entity_id = recipient_id
    entity = entity_ledger.by_id().get(entity_id)
    if entity is None or not entity.operating_subject_eligible:
        raise ClaimDecisionError(f"subject is not eligible:{claim.claim_id}")
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ClaimDecisionError(f"invalid event_type:{claim.claim_id}")
    event_status = str(decision.get("event_status") or "")
    status_aliases = {
        "in_progress": "started",
        "ongoing": "started",
        "planned": "target",
        "proposed": "target",
        "pending": "target",
        "announced": (
            claim.event_status_hint
            if claim.event_status_hint in ALLOWED_STATUSES
            else "completed"
        ),
    }
    event_status = status_aliases.get(event_status, event_status)
    if claim.event_status_hint in {"started", "target", "cumulative"}:
        event_status = claim.event_status_hint
    if event_status not in ALLOWED_STATUSES:
        raise ClaimDecisionError(f"invalid event_status:{claim.claim_id}")
    confidence = str(decision.get("confidence") or "unknown")
    if confidence not in ALLOWED_CONFIDENCE:
        raise ClaimDecisionError(f"invalid confidence:{claim.claim_id}")
    if claim.funding_round_hint:
        funding_round = claim.funding_round_hint
    else:
        funding_round = _grounded_string(
            decision.get("funding_round"), span.text, "funding_round"
        )
    funding_amount = _grounded_string(
        decision.get("funding_amount"), span.text, "funding_amount"
    )
    if event_type == "funding" and _valuation_only_funding_amount(
        funding_amount, span.text
    ):
        funding_amount = ""
    cumulative_amount = _grounded_string(
        decision.get("cumulative_funding_amount"),
        span.text,
        "cumulative_funding_amount",
    )
    investors = _grounded_list(decision.get("investors"), span.text, "investors")
    industry_tags = _grounded_list(
        decision.get("industry_tags"), span.text, "industry_tags"
    )
    if event_type != "funding":
        funding_round = ""
        funding_amount = ""
        cumulative_amount = ""
        investors = ()
    elif model_entity_id != entity_id:
        model_entity = entity_ledger.by_id().get(model_entity_id)
        if model_entity is not None:
            grounded_investor = next(
                (
                    value
                    for value in (model_entity.canonical_name, *model_entity.aliases)
                    if value and value in span.text
                ),
                "",
            )
            if grounded_investor:
                investors = tuple(dict.fromkeys((*investors, grounded_investor)))
    mentioned = tuple(
        dict.fromkeys(
            value
            for value in (entity.canonical_name, *entity.aliases)
            if value and value in span.text
        )
    ) or (entity.canonical_name,)
    return SemanticEvent(
        source_id=article.index.source_id,
        source_article_id=article.index.source_article_id,
        canonical_url=article.index.canonical_url,
        company_mentions=mentioned,
        canonical_company=entity.canonical_name,
        event_type=event_type,
        event_date=article.index.published_at[:10],
        industry_tags=industry_tags,
        funding_round=funding_round,
        funding_amount=funding_amount,
        cumulative_funding_amount=cumulative_amount,
        investors=investors,
        event_summary=span.text[:500],
        evidence_quotes=(span.text,),
        ambiguities=(),
        confidence=confidence,
        processor="minimax",
        prompt_version=prompt_version,
        content_hash=article.content_hash,
        phase="strategy_capital" if event_status in {"started", "target"} else "build_organize",
        event_status=event_status,
        claim_ids=(claim.claim_id,),
        span_ids=(claim.span_id,),
        subject_entity_id=entity_id,
    )


def _host_locked_rejection_fallback(
    decision: Mapping[str, Any],
    claim: AtomicClaim,
    action_ledger: ActionSpanLedger,
    entity_ledger: ArticleEntityLedger,
) -> dict[str, Any] | None:
    project_invalid_accept = bool(
        decision.get("decision") == "accept" and claim.host_mandatory
    )
    if decision.get("decision") != "reject" and not project_invalid_accept:
        return None
    reason_code = str(decision.get("reason_code") or "")
    subject_mismatch = reason_code == "subject_mismatch"
    if not claim.host_mandatory and not subject_mismatch:
        return None
    span = action_ledger.spans_by_id()[claim.span_id]
    funding_recipient_id = (
        _funding_recipient_entity_id(claim, span.text, entity_ledger)
        if claim.event_type_hint == "funding"
        else ""
    )
    if funding_recipient_id:
        entity_id = funding_recipient_id
    elif claim.host_mandatory and claim.primary_subject_entity_id:
        entity_id = claim.primary_subject_entity_id
    elif len(claim.allowed_subject_entity_ids) == 1:
        entity_id = claim.allowed_subject_entity_ids[0]
    else:
        return None
    entity = entity_ledger.by_id().get(entity_id)
    relative_action_start = max(0, claim.action_char_start - span.char_start)
    prefix = span.text[:relative_action_start]
    names = (
        (entity.canonical_name, *entity.aliases) if entity is not None else ()
    )
    grounding_scope = span.text if claim.host_mandatory else prefix
    recipient_coreference_grounded = bool(
        funding_recipient_id == entity_id
        and re.search(r"该初创(?:公司|企业)|这家初创(?:公司|企业)|两家企业", span.text)
        and any(
            name and name in span.text
            for allowed_id in claim.allowed_subject_entity_ids
            if allowed_id != entity_id
            for allowed_entity in [entity_ledger.by_id().get(allowed_id)]
            if allowed_entity is not None
            for name in (allowed_entity.canonical_name, *allowed_entity.aliases)
        )
    )
    if not any(name and name in grounding_scope for name in names) and not (
        recipient_coreference_grounded
    ):
        return None
    return {
        "claim_id": claim.claim_id,
        "decision": "accept",
        "subject_entity_id": entity_id,
        "event_type": claim.event_type_hint,
        "event_status": claim.event_status_hint,
        "funding_round": "",
        "funding_amount": "",
        "cumulative_funding_amount": "",
        "investors": [],
        "industry_tags": [],
        "confidence": "medium",
    }


def _combine_events(first: SemanticEvent, second: SemanticEvent) -> SemanticEvent:
    first_score = (
        bool(re.search(r"(?:20\d{2}年|\d{1,2}月\d{1,2}日)", first.event_summary)),
        len(first.event_summary),
    )
    second_score = (
        bool(re.search(r"(?:20\d{2}年|\d{1,2}月\d{1,2}日)", second.event_summary)),
        len(second.event_summary),
    )
    preferred, other = (
        (second, first) if second_score > first_score else (first, second)
    )
    return replace(
        preferred,
        company_mentions=tuple(
            dict.fromkeys((*preferred.company_mentions, *other.company_mentions))
        ),
        investors=tuple(dict.fromkeys((*preferred.investors, *other.investors))),
        evidence_quotes=tuple(
            dict.fromkeys((*preferred.evidence_quotes, *other.evidence_quotes))
        ),
        claim_ids=tuple(dict.fromkeys((*preferred.claim_ids, *other.claim_ids))),
        span_ids=tuple(dict.fromkeys((*preferred.span_ids, *other.span_ids))),
        confidence=(
            preferred.confidence
            if preferred.confidence in {"high", "medium"}
            else other.confidence
        ),
    )


def _technical_product_keys(
    event: SemanticEvent, claim_by_id: Mapping[str, AtomicClaim]
) -> set[str]:
    """Return conservative product identifiers used only for same-subject dedupe.

    The key intentionally excludes generic unquoted Chinese noun phrases.  Those
    phrases are too easy to confuse across distinct launches (for example A芯片
    and B芯片).  Versioned names, quoted names, and mixed Chinese/Latin brands are
    stable enough to connect a headline restatement to its detailed paragraph.
    """

    claim_text = " ".join(
        claim_by_id[claim_id].action_text
        for claim_id in event.claim_ids
        if claim_id in claim_by_id
    )
    keys: set[str] = set()
    generic_keys = {
        "api",
        "app",
        "pc",
        "pc端",
        "网页端",
        "客户端",
        "服务",
        "平台",
        "系统",
        "模型",
        "产品",
        "功能",
        "agent",
        "gpu",
        "cpu",
        "ipo",
        "ceo",
        "oled",
    }
    for evidence_index, evidence in enumerate(
        (claim_text, " ".join(event.evidence_quotes))
    ):
        if not evidence:
            continue
        # Action text is the identity-bearing scope. When it already contains a
        # specific product key, only import stable all-caps tokens from the
        # broader evidence quote; importing every mixed-script token would pull
        # a second version from the same sentence (for example Grok 4.7 into a
        # Grok 4.6 claim).
        if evidence_index == 1 and keys:
            keys.update(
                token
                for token in re.findall(
                    r"(?<![A-Za-z0-9])[A-Z]{3,}[A-Za-z0-9.+-]{0,20}(?![A-Za-z0-9])",
                    evidence,
                )
                if token.casefold() not in generic_keys
            )
            continue
        keys.update(
            re.findall(
                r"[A-Z][A-Za-z0-9.+-]{1,30}\s+[A-Z]?\d[A-Za-z0-9.+-]*",
                evidence,
            )
        )
        # Mixed-script model names are often written without a space
        # (Gemini4, Seedance2.5). Treat them as stable product keys too.
        keys.update(
            re.findall(
                r"(?<![A-Za-z0-9])[A-Z][A-Za-z]{1,20}\d[A-Za-z0-9.+-]*",
                evidence,
            )
        )
        keys.update(
            token
            for token in re.findall(
                r"(?<![A-Za-z0-9])[A-Z]{3,}[A-Za-z0-9.+-]{0,20}(?![A-Za-z0-9])",
                evidence,
            )
            if token.casefold() not in generic_keys
        )
        keys.update(
            match.strip()
            for match in re.findall(r"[“\"]([^”\"\n]{2,40})[”\"]", evidence)
            if re.search(
                r"发布|推出|上线|开源|升级|模型|产品|平台|系统|功能", evidence
            )
        )
        mixed_keys = re.findall(
            r"(?<![A-Za-z0-9])(?:[\u4e00-\u9fff]{1,10}[A-Z][A-Za-z0-9.+-]{1,30}|"
            r"[A-Z][A-Za-z0-9.+-]{1,30}[\u4e00-\u9fff]{1,10})(?![A-Za-z0-9])",
            evidence,
        )
        for key in mixed_keys:
            # A bounded regex window can include the company/action prefix before a
            # mixed-script product ("公司发布产品纳米Work").  Keep only the stable
            # suffix after the last product/action marker.
            normalized = re.sub(
                r"^.*(?:发布|推出|上线|开源|升级|产品|模型|平台|系统|功能|服务|"
                r"表示|宣布|称)",
                "",
                key,
            )
            company_prefix = event.canonical_company[:2]
            if (
                len(company_prefix) == 2
                and normalized.startswith(company_prefix)
                and re.search(r"[A-Za-z]", normalized[len(company_prefix) :])
            ):
                normalized = normalized[len(company_prefix) :]
            keys.add(normalized or key)
        keys = {
            key
            for key in keys
            if re.sub(r"\s+", "", key).strip(" ，。；：、").casefold()
            not in generic_keys
        }
    normalized_keys = {
        re.sub(r"\s+", " ", key).strip(" ，。；：、")
        for key in keys
        if key.strip(" ，。；：、")
    }
    numeric_keys = {key for key in normalized_keys if re.search(r"\d", key)}
    if numeric_keys:
        stable_upper_keys = {
            key
            for key in normalized_keys
            if re.fullmatch(r"[A-Z][A-Z0-9.+-]{2,}", key)
        }
        normalized_keys = numeric_keys | stable_upper_keys
    return normalized_keys


def _merge_events(
    events: Sequence[SemanticEvent], action_ledger: ActionSpanLedger
) -> list[SemanticEvent]:
    claim_by_id = action_ledger.claims_by_id()
    output: dict[
        tuple[str, str, str, str, str, str, tuple[str, ...], tuple[str, ...]],
        SemanticEvent,
    ] = {}
    for event in events:
        action_discriminators = tuple(
            sorted(
                re.sub(r"\W+", "", claim_by_id[claim_id].action_text).casefold()
                for claim_id in event.claim_ids
                if claim_id in claim_by_id
            )
        )
        key = (
            event.subject_entity_id,
            event.event_type,
            event.event_status,
            event.funding_round,
            event.funding_amount,
            event.cumulative_funding_amount,
            tuple(sorted(event.span_ids)),
            action_discriminators,
        )
        previous = output.get(key)
        if previous is None:
            output[key] = event
            continue
        output[key] = _combine_events(previous, event)

    merged: list[SemanticEvent] = []
    duplicate_index: dict[tuple[str, str, str, str, str, str, str], int] = {}
    for event in output.values():
        action_keys = {
            re.sub(r"\W+", "", claim_by_id[claim_id].action_text).casefold()
            for claim_id in event.claim_ids
            if claim_id in claim_by_id
        }
        matched_index: int | None = None
        for action_key in sorted(action_keys):
            duplicate_key = (
                event.subject_entity_id,
                event.event_type,
                event.event_status,
                event.funding_round,
                event.funding_amount,
                event.cumulative_funding_amount,
                action_key,
            )
            if duplicate_key in duplicate_index:
                matched_index = duplicate_index[duplicate_key]
                break
        if matched_index is None:
            matched_index = len(merged)
            merged.append(event)
        else:
            merged[matched_index] = _combine_events(merged[matched_index], event)
        for action_key in action_keys:
            duplicate_index[
                (
                    event.subject_entity_id,
                    event.event_type,
                    event.event_status,
                    event.funding_round,
                    event.funding_amount,
                    event.cumulative_funding_amount,
                    action_key,
                )
            ] = matched_index

    product_merged: list[SemanticEvent] = []
    product_index: dict[tuple[str, str, str, str], int] = {}
    for event in merged:
        product_keys = _technical_product_keys(event, claim_by_id)
        matched_index = None
        if event.event_type == "technical_milestone":
            for product_key in sorted(product_keys):
                key = (
                    event.subject_entity_id,
                    event.event_type,
                    event.event_status,
                    product_key.casefold(),
                )
                if key in product_index:
                    previous = product_merged[product_index[key]]
                    previous_keys = _technical_product_keys(previous, claim_by_id)
                    current_numeric = {
                        value for value in product_keys if re.search(r"\d", value)
                    }
                    previous_numeric = {
                        value for value in previous_keys if re.search(r"\d", value)
                    }
                    if (
                        current_numeric
                        and previous_numeric
                        and not current_numeric.intersection(previous_numeric)
                    ):
                        continue
                    matched_index = product_index[key]
                    break
        if matched_index is None:
            matched_index = len(product_merged)
            product_merged.append(event)
        else:
            product_merged[matched_index] = _combine_events(
                product_merged[matched_index], event
            )
        for product_key in product_keys:
            product_index[
                (
                    event.subject_entity_id,
                    event.event_type,
                    event.event_status,
                    product_key.casefold(),
                )
            ] = matched_index
    merged = product_merged

    current_product_keys = {
        (event.subject_entity_id, key.casefold())
        for event in merged
        if event.event_type == "technical_milestone"
        and event.event_status in {"completed", "started"}
        for key in _technical_product_keys(event, claim_by_id)
    }
    merged = [
        event
        for event in merged
        if not (
            event.event_type == "technical_milestone"
            and event.event_status == "target"
            and any(
                (event.subject_entity_id, key.casefold()) in current_product_keys
                for key in _technical_product_keys(event, claim_by_id)
            )
        )
    ]

    customer_span_keys = {
        (event.subject_entity_id, span_id)
        for event in merged
        if event.event_type == "customer_validation"
        for span_id in event.span_ids
    }
    merged = [
        event
        for event in merged
        if not (
            event.event_type == "technical_milestone"
            and re.search(
                r"交付|用户复现|业务重心|真实业务场景",
                "".join(event.evidence_quotes),
            )
            and any(
                (event.subject_entity_id, span_id) in customer_span_keys
                for span_id in event.span_ids
            )
        )
    ]

    detailed_funding_by_subject: dict[str, list[SemanticEvent]] = {}
    for event in merged:
        if event.event_type != "funding":
            continue
        evidence = "".join(event.evidence_quotes)
        if re.search(r"近日|日前|今日|本月|宣布|公告", evidence):
            detailed_funding_by_subject.setdefault(
                event.subject_entity_id, []
            ).append(event)
    filtered_funding: list[SemanticEvent] = []
    for event in merged:
        if event.event_type != "funding":
            filtered_funding.append(event)
            continue
        evidence = "".join(event.evidence_quotes)
        details = [
            other
            for other in detailed_funding_by_subject.get(
                event.subject_entity_id, []
            )
            if other is not event
            and not (
                event.funding_round
                and other.funding_round
                and event.funding_round != other.funding_round
                and set(event.span_ids).intersection(other.span_ids)
            )
        ]
        summary_like = bool(
            re.search(
                r"投资(?:端|方面)|快速完成[两三\d]+轮|"
                r"(?:半年|年内|月内)[^。；]{0,30}连续完成|"
                r"(?:至此|累计)[^。；]{0,50}(?:完成|融资)|"
                r"(?:本轮|此轮|最新一轮)融资(?:完成|距)|融资完成后",
                evidence,
            )
        )
        cumulative_round_like = bool(
            re.search(
                r"(?:成立|累计|此前|先后|半年内|年内)[^。！？；\n]{0,50}"
                r"(?:第[一二三四五六七八九十0-9]+轮|[A-H](?:\+{0,2})?\s*轮)",
                evidence,
                re.I,
            )
        )
        same_amount_detail = bool(
            event.funding_amount
            and any(
                other.funding_amount
                and re.sub(r"超过|超", "", other.funding_amount)
                == re.sub(r"超过|超", "", event.funding_amount)
                for other in details
            )
        )
        much_shorter_detail = bool(
            any(
                event.funding_amount == other.funding_amount
                and len(evidence) * 2 < len("".join(other.evidence_quotes))
                for other in details
            )
        )
        if details and (
            cumulative_round_like
            or summary_like
            or same_amount_detail
            or much_shorter_detail
            or (not event.funding_round and "宣布" not in evidence)
        ):
            continue
        filtered_funding.append(event)
    merged = filtered_funding

    # A single financing round is often described once in a headline and
    # again in a detail paragraph, with the latter adding amount/investor
    # fields.  The action-level key above intentionally keeps those claims
    # separate for adjudication; consolidate them only after the detailed-
    # announcement filter so one round is emitted with all grounded evidence.
    funding_round_index: dict[tuple[str, str, str], int] = {}
    funding_round_merged: list[SemanticEvent] = []
    for event in merged:
        normalized_round = re.sub(r"\s+", "", event.funding_round).casefold()
        if event.event_type != "funding" or not normalized_round:
            funding_round_merged.append(event)
            continue
        key = (
            event.subject_entity_id,
            event.event_status,
            normalized_round,
        )
        incumbent = funding_round_index.get(key)
        if incumbent is None:
            funding_round_index[key] = len(funding_round_merged)
            funding_round_merged.append(event)
        else:
            funding_round_merged[incumbent] = _combine_events(
                funding_round_merged[incumbent], event
            )
    merged = funding_round_merged

    span_by_id = action_ledger.spans_by_id()
    site_merged: list[SemanticEvent] = []
    for event in merged:
        matched_index: int | None = None
        if event.event_type == "new_site_or_entity":
            event_starts = [
                span_by_id[span_id].char_start
                for span_id in event.span_ids
                if span_id in span_by_id
            ]
            for index, previous in enumerate(site_merged):
                if (
                    previous.event_type != event.event_type
                    or previous.event_status != event.event_status
                    or previous.subject_entity_id != event.subject_entity_id
                ):
                    continue
                previous_starts = [
                    span_by_id[span_id].char_start
                    for span_id in previous.span_ids
                    if span_id in span_by_id
                ]
                if not event_starts or not previous_starts:
                    continue
                if min(
                    abs(left - right)
                    for left in event_starts
                    for right in previous_starts
                ) > 320:
                    continue
                joined = "".join((*previous.evidence_quotes, *event.evidence_quotes))
                if re.search(r"(?:该|此|这一)(?:实验室|部门|机构|基地|中心)", joined):
                    matched_index = index
                    break
        if matched_index is None:
            site_merged.append(event)
        else:
            site_merged[matched_index] = _combine_events(
                site_merged[matched_index], event
            )
    merged = site_merged

    target_return_events = [
        event
        for event in merged
        if event.event_type == "executive_change"
        and event.event_status == "target"
        and any(
            "重返" in claim_by_id[claim_id].action_text
            for claim_id in event.claim_ids
            if claim_id in claim_by_id
        )
    ]
    merged = [
        event
        for event in merged
        if not (
            event.event_type == "executive_change"
            and event.event_status == "completed"
            and any(
                "重返" in claim_by_id[claim_id].action_text
                for claim_id in event.claim_ids
                if claim_id in claim_by_id
            )
            and any(
                target.subject_entity_id == event.subject_entity_id
                and min(
                    abs(
                        span_by_id[left].char_start
                        - span_by_id[right].char_start
                    )
                    for left in target.span_ids
                    if left in span_by_id
                    for right in event.span_ids
                    if right in span_by_id
                )
                <= 400
                for target in target_return_events
            )
        )
    ]

    capacity_span_keys = {
        (event.subject_entity_id, span_id)
        for event in merged
        if event.event_type == "factory_or_capacity"
        for span_id in event.span_ids
    }
    merged = [
        event
        for event in merged
        if not (
            event.event_type == "technical_milestone"
            and re.search(r"规模化量产|产能|产量", "".join(event.evidence_quotes))
            and any(
                (event.subject_entity_id, span_id) in capacity_span_keys
                for span_id in event.span_ids
            )
        )
    ]
    merged = [
        event
        for event in merged
        if not (
            event.event_type == "major_order"
            and "未来支出" not in "".join(event.evidence_quotes)
            and re.search(r"承诺.{0,80}投资", "".join(event.evidence_quotes))
            and any(
                (event.subject_entity_id, span_id) in capacity_span_keys
                for span_id in event.span_ids
            )
        )
    ]
    suppress_ipo_keys: set[tuple[str, tuple[str, ...]]] = set()
    for event in merged:
        if event.event_type != "merger_acquisition":
            continue
        evidence = "".join(event.evidence_quotes)
        if (
            ("控制权" in evidence or "控股股东" in evidence)
            and ("复牌" in evidence or "停牌" in evidence)
        ):
            suppress_ipo_keys.add((event.subject_entity_id, event.span_ids))
    merged = [
        event
        for event in merged
        if not (
            event.event_type == "ipo_or_listing"
            and any(
                subject_id == event.subject_entity_id
                and bool(set(span_ids) & set(event.span_ids))
                for subject_id, span_ids in suppress_ipo_keys
            )
        )
    ]
    merged = [
        event
        for event in merged
        if not (
            event.event_type == "customer_validation"
            and re.search(
                r"(?:融资|资金|本轮融资|融资资金)[^。！？；\n]{0,100}"
                r"(?:用于|投向|投入|支持|重点投向)[^。！？；\n]{0,160}"
                r"(?:工程化验证|技术研发|基础模型|数据闭环|研发团队|"
                r"高端人才|人才引进|平台建设)",
                "".join(event.evidence_quotes),
            )
            and not re.search(
                r"客户|车企|医院|高校|用户|订单|交付|商业化|销售",
                "".join(event.evidence_quotes),
            )
        )
    ]

    # Multiple host claims can share one immutable funding-use sentence. Keep
    # distinct signal families, but collapse repeated model decisions within
    # one family and let an explicit plan status win over a misleading
    # completed response.
    operational_types = frozenset(
        {
            "workforce_cluster",
            "research_or_ip",
            "project_buildout",
            "global_expansion",
        }
    )
    status_priority = {
        "completed": 0,
        "cumulative": 1,
        "started": 2,
        "target": 3,
    }
    operational_merged: list[SemanticEvent] = []
    for event in merged:
        if event.event_type not in operational_types:
            operational_merged.append(event)
            continue
        match_index: int | None = None
        for index, previous in enumerate(operational_merged):
            if (
                previous.event_type != event.event_type
                or previous.subject_entity_id != event.subject_entity_id
                or not set(previous.span_ids).intersection(event.span_ids)
            ):
                continue
            match_index = index
            break
        if match_index is None:
            operational_merged.append(event)
            continue
        previous = operational_merged[match_index]
        combined = _combine_events(previous, event)
        status = max(
            (previous.event_status, event.event_status),
            key=lambda value: status_priority.get(value, -1),
        )
        operational_merged[match_index] = replace(
            combined,
            event_status=status,
        )
    merged = operational_merged
    funding_use_signal_spans = {
        span_id
        for event in merged
        if event.event_type in {"research_or_ip", "workforce_cluster"}
        for span_id in event.span_ids
        if re.search(
            r"(?:融资|资金|本轮融资|融资资金)[^。！？；\n]{0,100}"
            r"(?:用于|投向|投入|支持|重点投向)",
            "".join(event.evidence_quotes),
        )
    }
    project_trimmed: list[SemanticEvent] = []
    for event in merged:
        if event.event_type != "project_buildout":
            project_trimmed.append(event)
            continue
        kept_claim_ids = tuple(
            claim_id
            for claim_id in event.claim_ids
            if not (
                claim_id in claim_by_id
                and claim_by_id[claim_id].span_id in funding_use_signal_spans
                and re.search(
                    r"产业化平台建设|平台建设",
                    claim_by_id[claim_id].action_text,
                )
            )
        )
        if not kept_claim_ids:
            continue
        kept_span_ids = tuple(
            dict.fromkeys(
                claim_by_id[claim_id].span_id
                for claim_id in kept_claim_ids
                if claim_id in claim_by_id
            )
        )
        kept_quotes = tuple(
            dict.fromkeys(
                span_by_id[span_id].text
                for span_id in kept_span_ids
                if span_id in span_by_id
            )
        )
        project_trimmed.append(
            replace(
                event,
                claim_ids=kept_claim_ids,
                span_ids=kept_span_ids or event.span_ids,
                evidence_quotes=kept_quotes or event.evidence_quotes,
                event_summary=(kept_quotes[0] if kept_quotes else event.event_summary),
            )
        )
    merged = project_trimmed
    return sorted(
        merged,
        key=lambda item: (
            item.canonical_company,
            item.event_type,
            item.event_status,
            item.event_summary,
        ),
    )


class ClaimCentricSemanticProcessor:
    def __init__(
        self,
        runner: PromptRunner,
        *,
        model_identity: str,
        system_prompt: str = SYSTEM_PROMPT,
        few_shot: Mapping[str, Any] = _FEW_SHOT,
        prompt_version: str = PROMPT_VERSION,
        contract_version: str = CONTRACT_VERSION,
    ) -> None:
        self.runner = runner
        self.model_identity = model_identity
        self.system_prompt = system_prompt
        self.few_shot = dict(few_shot)
        self.prompt_version = prompt_version
        self.contract_version = contract_version
        self.last_audit: dict[str, Any] = {}

    @property
    def cache_key(self) -> str:
        return f"{self.prompt_version}|{self.model_identity}"

    def process(
        self,
        channel: SourceChannel,
        article: CleanArticle,
        rule_events: list[SemanticEvent],
        legacy_candidates: Sequence[Mapping[str, Any]],
        *,
        source_body: str | None = None,
    ) -> list[SemanticEvent]:
        del channel  # The claim ledger is grounded in the immutable article.
        immutable_body = article.clean_body if source_body is None else source_body
        if len(immutable_body) != len(article.clean_body):
            raise ValueError("source body and scoped body offsets differ")
        entity_ledger = build_article_entity_ledger(
            article, legacy_candidates, rule_events
        )
        action_ledger = build_action_span_ledger(
            article, entity_ledger, legacy_candidates
        )
        route = route_document(article)
        audit: dict[str, Any] = {
            "source_id": article.index.source_id,
            "source_article_id": article.index.source_article_id,
            "prompt_version": self.prompt_version,
            "claim_contract_version": self.contract_version,
            "model_identity": self.model_identity,
            "cache_key": self.cache_key,
            "entity_count": len(entity_ledger.entities),
            "eligible_entity_count": len(entity_ledger.eligible()),
            "action_span_count": len(action_ledger.spans),
            "candidate_count": len(action_ledger.claims),
            "batch_count": len(action_ledger.batches()),
            "document_type": route.document_type,
            "document_route_reason": route.reason,
            "document_family": route.document_family,
            "processing_mode": route.processing_mode,
            "route_gate_confidence": route.gate_confidence,
            "route_llm_gate_required": route.llm_gate_required,
            "route_gate_signals": list(route.gate_signals),
            "document_unit_ids": [unit.unit_id for unit in route.units],
            "accepted_claim_ids": [],
            "model_accepted_claim_ids": [],
            "suppressed_claim_ids": [],
            "rejected_claim_ids": [],
            "failed_claim_ids": [],
            "host_fallback_claim_ids": [],
            "batch_statuses": [],
            "first_responses": [],
            "repair_responses": [],
            "validation_issues": [],
            "infrastructure_errors": [],
            "skipped_claim_ids_due_to_infrastructure": [],
            "rejection_reason_counts": {},
            "status": "accepted",
        }
        self.last_audit = audit
        if not action_ledger.claims:
            audit["status"] = "no_claims"
            audit["strict_claim_contract_ready"] = True
            audit["final_event_count"] = 0
            return []

        claim_by_id = action_ledger.claims_by_id()
        accepted_events: list[SemanticEvent] = []
        batches = action_ledger.batches()
        for batch_number, batch in enumerate(batches, start=1):
            prompt = _batch_prompt(
                batch,
                action_ledger,
                entity_ledger,
                contract_version=self.contract_version,
                few_shot=self.few_shot,
                route=route,
            )
            response = ""
            repair = ""
            final_decisions: dict[str, dict[str, Any]] = {}
            batch_events: list[SemanticEvent] = []
            invalid: dict[str, str] = {}
            try:
                response = self.runner.run(
                    prompt,
                    session_id=(
                        f"aggregate-v27:{article.index.source_id}:"
                        f"{article.index.source_article_id}:batch-{batch_number}"
                    ),
                    system_prompt=self.system_prompt,
                )
                first_decisions = _validate_batch_decisions(
                    _parse_json(response), batch
                )
            except Exception as error:
                first_error = f"{type(error).__name__}:{error}"
                invalid = {claim.claim_id: first_error for claim in batch}
                if _is_infrastructure_error(error):
                    remaining = [
                        claim.claim_id
                        for pending_batch in batches[batch_number - 1 :]
                        for claim in pending_batch
                    ]
                    skipped = remaining[len(batch) :]
                    audit["failed_claim_ids"].extend(remaining)
                    audit["skipped_claim_ids_due_to_infrastructure"].extend(skipped)
                    audit["infrastructure_errors"].append(
                        f"batch {batch_number}:{first_error}"
                    )
                    audit["validation_issues"].append(
                        f"batch {batch_number}:infrastructure_failure:{first_error}"
                    )
                    audit["batch_statuses"].append("infrastructure_failed")
                    audit["first_responses"].append(response)
                    audit["repair_responses"].append("")
                    break
            else:
                for claim in batch:
                    decision = first_decisions[claim.claim_id]
                    try:
                        event = _decision_to_event(
                            decision,
                            claim,
                            article,
                            action_ledger,
                            entity_ledger,
                            prompt_version=self.prompt_version,
                        )
                    except Exception as error:
                        invalid[claim.claim_id] = (
                            f"{type(error).__name__}:{error}"
                        )
                        continue
                    final_decisions[claim.claim_id] = decision
                    if event is not None:
                        batch_events.append(event)

            if invalid:
                repair_batch = tuple(
                    claim for claim in batch if claim.claim_id in invalid
                )
                try:
                    repair_input = _batch_prompt(
                        repair_batch,
                        action_ledger,
                        entity_ledger,
                        contract_version=self.contract_version,
                        few_shot=self.few_shot,
                    )
                    repair_prompt = (
                        "修复上一份JSON，使每个claim_id恰好裁决一次。只返回完整JSON对象。\n"
                        f"input={repair_input}\nprior_output={response[:4000]}\n"
                        f"errors={json.dumps(invalid, ensure_ascii=False)}"
                    )
                    repair = self.runner.run(
                        repair_prompt,
                        session_id=(
                            f"aggregate-v27-repair:{article.index.source_id}:"
                            f"{article.index.source_article_id}:batch-{batch_number}"
                        ),
                        system_prompt=self.system_prompt,
                    )
                    repair_decisions = _validate_batch_decisions(
                        _parse_json(repair),
                        repair_batch,
                        # MiniMax occasionally repeats already-valid decisions
                        # copied from prior_output.  They are outside the narrow
                        # repair contract but must not poison the failed Claim's
                        # otherwise valid retry.
                        ignore_unknown=True,
                    )
                except Exception as repair_error:
                    audit["failed_claim_ids"].extend(invalid)
                    audit["validation_issues"].append(
                        f"batch {batch_number}:repair "
                        f"{type(repair_error).__name__}:{repair_error};"
                        f"initial={json.dumps(invalid, ensure_ascii=False)}"
                    )
                else:
                    still_invalid: dict[str, str] = {}
                    for claim in repair_batch:
                        decision = repair_decisions[claim.claim_id]
                        initial_issue = invalid.get(claim.claim_id, "")
                        if decision.get("decision") == "reject" and (
                            "host-mandatory claim rejected" in initial_issue
                            or "action subject is host-grounded"
                            in initial_issue
                        ):
                            fallback = _host_locked_rejection_fallback(
                                {
                                    "decision": "reject",
                                    "reason_code": (
                                        "subject_mismatch"
                                        if "action subject is host-grounded"
                                        in initial_issue
                                        else "event_type_mismatch"
                                    ),
                                },
                                claim,
                                action_ledger,
                                entity_ledger,
                            )
                            if fallback is not None:
                                decision = fallback
                                audit["host_fallback_claim_ids"].append(
                                    claim.claim_id
                                )
                        try:
                            event = _decision_to_event(
                                decision,
                                claim,
                                article,
                                action_ledger,
                                entity_ledger,
                                prompt_version=self.prompt_version,
                            )
                        except Exception as error:
                            fallback = _host_locked_rejection_fallback(
                                decision,
                                claim,
                                action_ledger,
                                entity_ledger,
                            )
                            if fallback is not None:
                                event = _decision_to_event(
                                    fallback,
                                    claim,
                                    article,
                                    action_ledger,
                                    entity_ledger,
                                    prompt_version=self.prompt_version,
                                )
                                final_decisions[claim.claim_id] = fallback
                                batch_events.append(event)
                                audit["host_fallback_claim_ids"].append(
                                    claim.claim_id
                                )
                                continue
                            still_invalid[claim.claim_id] = (
                                f"{type(error).__name__}:{error}"
                            )
                            continue
                        final_decisions[claim.claim_id] = decision
                        if event is not None:
                            batch_events.append(event)
                    if still_invalid:
                        audit["failed_claim_ids"].extend(still_invalid)
                        audit["validation_issues"].append(
                            f"batch {batch_number}:repair_projection="
                            f"{json.dumps(still_invalid, ensure_ascii=False)}"
                        )

            failed_in_batch = {
                claim.claim_id for claim in batch
            } - set(final_decisions)
            if failed_in_batch:
                audit["batch_statuses"].append(
                    "failed" if not final_decisions else "partial"
                )
            elif any(
                claim.claim_id in audit["host_fallback_claim_ids"]
                for claim in batch
            ):
                audit["batch_statuses"].append("host_fallback")
            elif repair:
                audit["batch_statuses"].append("repaired")
            else:
                audit["batch_statuses"].append("accepted")

            for claim_id, decision in final_decisions.items():
                if decision["decision"] == "accept":
                    audit["accepted_claim_ids"].append(claim_id)
                else:
                    audit["rejected_claim_ids"].append(claim_id)
                    reason = str(decision.get("reason_code") or "")
                    counts = audit["rejection_reason_counts"]
                    counts[reason] = counts.get(reason, 0) + 1
            accepted_events.extend(batch_events)
            audit["first_responses"].append(response)
            audit["repair_responses"].append(repair)

        terminal = {
            *audit["accepted_claim_ids"],
            *audit["rejected_claim_ids"],
            *audit["failed_claim_ids"],
        }
        expected = set(claim_by_id)
        if terminal != expected:
            missing = sorted(expected - terminal)
            audit["failed_claim_ids"].extend(missing)
            audit["validation_issues"].append(
                f"host_terminal_state_gap:{','.join(missing)}"
            )
        events = _merge_events(accepted_events, action_ledger)
        span_by_id = action_ledger.spans_by_id()
        claim_by_id = action_ledger.claims_by_id()
        restored_events: list[SemanticEvent] = []
        for event in events:
            quotes = tuple(
                immutable_body[span_by_id[span_id].char_start : span_by_id[span_id].char_end]
                for span_id in event.span_ids
                if span_id in span_by_id
            )
            atomic_quotes = (
                tuple(
                    immutable_body[
                        claim_by_id[claim_id].action_char_start : claim_by_id[
                            claim_id
                        ].action_char_end
                    ]
                    for claim_id in event.claim_ids
                    if claim_id in claim_by_id
                    and 0 <= claim_by_id[claim_id].action_char_start
                    < claim_by_id[claim_id].action_char_end
                    <= len(immutable_body)
                )
                if event.event_type == "customer_validation"
                and any(
                    re.search(
                        r"累计出货|规模化交付|量产交付|客户验证",
                        claim_by_id[claim_id].action_text,
                    )
                    for claim_id in event.claim_ids
                    if claim_id in claim_by_id
                )
                else ()
            )
            quotes = tuple(dict.fromkeys((*quotes, *atomic_quotes)))
            if event.event_type == "customer_validation" and len(event.claim_ids) >= 3:
                # A single source span can contain several accepted customer
                # actions.  Keep each high-value atomic clause citable instead
                # of retaining only the first long paragraph.
                quotes = tuple(
                    dict.fromkeys(
                        (
                            *quotes,
                            *(
                                immutable_body[
                                    claim_by_id[claim_id].action_char_start : claim_by_id[
                                        claim_id
                                    ].action_char_end
                                ]
                                for claim_id in event.claim_ids
                                if claim_id in claim_by_id
                                and 0 <= claim_by_id[claim_id].action_char_start
                                < claim_by_id[claim_id].action_char_end
                                <= len(immutable_body)
                            ),
                        )
                    )
                )
            restored_events.append(
                replace(
                    event,
                    event_summary=(quotes[0][:500] if quotes else event.event_summary),
                    evidence_quotes=(quotes or event.evidence_quotes),
                )
            )
        events = restored_events
        # A model-accepted Claim can be intentionally removed by deterministic
        # post-merge rules (headline/detail dedupe, technical-vs-capacity
        # suppression, etc.).  Keep that distinction explicit: ``accepted``
        # is now the final materialized set, while ``model_accepted`` preserves
        # the raw adjudication outcome for auditability.
        model_accepted_claim_ids = sorted(set(audit["accepted_claim_ids"]))
        materialized_claim_ids = sorted(
            {
                claim_id
                for event in restored_events
                for claim_id in event.claim_ids
            }
        )
        audit["model_accepted_claim_ids"] = model_accepted_claim_ids
        audit["suppressed_claim_ids"] = sorted(
            set(model_accepted_claim_ids) - set(materialized_claim_ids)
        )
        audit["accepted_claim_ids"] = materialized_claim_ids
        audit["rejected_claim_ids"] = sorted(set(audit["rejected_claim_ids"]))
        audit["failed_claim_ids"] = sorted(set(audit["failed_claim_ids"]))
        audit["strict_claim_contract_ready"] = not audit["failed_claim_ids"]
        audit["final_event_count"] = len(events)
        audit["status"] = (
            "partial" if audit["failed_claim_ids"] else "accepted"
        )
        audit["ledger_sha256"] = sha1(
            json.dumps(
                action_ledger.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return events


__all__ = [
    "CONTRACT_VERSION",
    "PROMPT_VERSION",
    "ClaimCentricSemanticProcessor",
    "ClaimDecisionError",
]
