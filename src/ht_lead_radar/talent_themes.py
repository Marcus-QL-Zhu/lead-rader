"""Cluster evidence-bound company hypotheses into reusable talent themes."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Mapping

from .talent_pool import (
    DraftBundle,
    SourceLead,
    TalentPoolDraft,
    canonical_payload_hash,
    validate_liepin_payload,
)


FUNCTION_FAMILIES = (
    ("质量与验证", ("质量", "验证", "测试", "可靠性", "认证")),
    ("研发与算法", ("研发", "算法", "技术", "运动控制", "控制", "总师")),
    ("制造与供应链", ("量产", "制造", "工艺", "交付", "供应链", "采购")),
    ("产品与商业化", ("产品", "商业化", "销售", "客户", "市场", "解决方案")),
    ("临床与法规", ("临床", "医学", "注册", "法规", "合规")),
    ("战略与组织", ("战略", "运营", "组织", "人力", "财务")),
)


def _family(title: str) -> str:
    for family, markers in FUNCTION_FAMILIES:
        if any(marker in title for marker in markers):
            return family
    return "其他具体职能"


def _similar(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if left["specific_title"] == right["specific_title"]:
        return True
    if _family(left["specific_title"]) != _family(right["specific_title"]):
        return False
    left_terms = set(left["specificity_terms"])
    right_terms = set(right["specificity_terms"])
    union = left_terms | right_terms
    return bool(union) and len(left_terms & right_terms) / len(union) >= 0.5


def _source_lead(lead: Mapping[str, Any], titles: list[str]) -> SourceLead:
    evidence = [
        item
        for item in lead.get("evidence") or ()
        if isinstance(item, Mapping)
    ]
    return SourceLead(
        company=str(lead.get("company") or "").strip(),
        score=float(lead.get("score") or 0),
        role_hypotheses=tuple(dict.fromkeys(titles)),
        evidence_urls=tuple(
            dict.fromkeys(
                str(item.get("source_url") or "").strip()
                for item in evidence
                if str(item.get("source_url") or "").strip()
            )
        ),
        event_types=tuple(
            dict.fromkeys(
                str(item.get("event_type") or "").strip()
                for item in evidence
                if str(item.get("event_type") or "").strip()
            )
        ),
    )


def build_talent_themes(
    report: Mapping[str, Any],
    company_demands: tuple[dict[str, Any], ...],
    *,
    target_count: int,
) -> tuple[dict[str, Any], ...]:
    leads = [
        item
        for item in report.get("leads") or ()
        if isinstance(item, Mapping)
    ]
    candidates: list[dict[str, Any]] = []
    for demand in company_demands:
        lead_index = int(demand["lead_index"])
        lead = leads[lead_index - 1]
        for hypothesis in demand["hypotheses"]:
            if not str(hypothesis.get("city") or "").strip():
                continue
            candidates.append(
                {
                    **hypothesis,
                    "lead_index": lead_index,
                    "company": demand["company"],
                    "lead_score": float(lead.get("score") or 0),
                }
            )
    candidates.sort(
        key=lambda item: (
            item["horizon"] != "near_term",
            -item["lead_score"],
            item["specific_title"],
        )
    )
    groups: list[list[dict[str, Any]]] = []
    for candidate in candidates:
        group = next(
            (
                existing
                for existing in groups
                if _similar(existing[0], candidate)
            ),
            None,
        )
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)
    groups.sort(
        key=lambda group: (
            group[0]["horizon"] != "near_term",
            -len({item["lead_index"] for item in group}),
            -len(
                {
                    ref
                    for item in group
                    for ref in item["evidence_refs"]
                }
            ),
            -max(item["lead_score"] for item in group),
            group[0]["specific_title"],
        )
    )
    themes: list[dict[str, Any]] = []
    for group in groups[: max(min(target_count, 10), 0)]:
        anchor = max(
            group,
            key=lambda item: (
                item["lead_score"],
                len(item["specific_title"]),
            ),
        )
        terms = list(
            dict.fromkeys(
                term
                for item in group
                for term in item["specificity_terms"]
            )
        )[:8]
        cities = [item["city"] for item in group if item["city"]]
        city = Counter(cities).most_common(1)[0][0] if cities else "上海"
        city_basis = (
            next(item["city_basis"] for item in group if item["city"] == city)
            if cities
            else "公开证据未确认城市；发布草稿暂用单城市默认值，需人工复核"
        )
        identity = "\x1f".join(
            sorted(item["hypothesis_id"] for item in group)
        )
        themes.append(
            {
                "theme_id": "theme_"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12],
                "recommended_title": anchor["specific_title"],
                "role_family": _family(anchor["specific_title"]),
                "shared_mandate": anchor["mandate"],
                "why_now": anchor["why_now"],
                "horizon": anchor["horizon"],
                "specificity_terms": terms,
                "key_outcomes": list(
                    dict.fromkeys(
                        outcome
                        for item in group
                        for outcome in item["key_outcomes"]
                    )
                )[:5],
                "must_have_signals": list(
                    dict.fromkeys(
                        signal
                        for item in group
                        for signal in item["must_have_signals"]
                    )
                )[:5],
                "preferred_signals": list(
                    dict.fromkeys(
                        signal
                        for item in group
                        for signal in item["preferred_signals"]
                    )
                )[:3],
                "city": city,
                "city_basis": city_basis,
                "source_hypothesis_ids": [
                    item["hypothesis_id"] for item in group
                ],
                "source_lead_indices": list(
                    dict.fromkeys(item["lead_index"] for item in group)
                ),
                "evidence_refs": list(
                    dict.fromkeys(
                        ref
                        for item in group
                        for ref in item["evidence_refs"]
                    )
                ),
            }
        )
    return tuple(themes)


def _numbered(items: list[str]) -> str:
    return "；".join(
        f"{index}.{item}" for index, item in enumerate(items, start=1)
    )


def _payload_example(theme: Mapping[str, Any]) -> dict[str, Any]:
    title = str(theme["recommended_title"])
    outcomes = [str(item) for item in theme["key_outcomes"]]
    must_have = [str(item) for item in theme["must_have_signals"]]
    scope = (
        f"岗位使命：{theme['shared_mandate']}。"
        f"核心职责：{_numbered(outcomes)}。"
        f"任职要求：{_numbered(must_have)}。"
        f"机会亮点：{theme['why_now']}。"
    )[:500]
    payload = {
        "position_name": title,
        "position_scope": scope,
        "cities": [str(theme["city"])],
        "seniority": "10年以上",
        "work_experience_years": [10],
        "education": "本科",
        "salary_low": "50k",
        "salary_high": "70k",
        "salary_months": "15个月",
        "must_have_signals": must_have,
        "preferred_signals": list(theme["preferred_signals"]) or [
            "有同类业务阶段的组织建设经验"
        ],
        "benefits": [
            "参与关键业务能力从验证走向规模化",
            "承担真实的团队和业务结果责任",
        ],
        "hard_rejects": ["仅有个人贡献者经历且无团队管理责任"],
        "target_count": 10,
        "job_type": "全职",
        "recruit_count": 1,
        "languages": ["中文"],
    }
    validate_liepin_payload(payload)
    return payload


def build_theme_draft_bundle(
    report: Mapping[str, Any],
    company_demands: tuple[dict[str, Any], ...],
    themes: tuple[dict[str, Any], ...],
) -> DraftBundle:
    manifest = report.get("manifest") or {}
    run_date = str(manifest.get("as_of") or "")
    direction = str(manifest.get("direction") or "")
    run_id = str(manifest.get("run_id") or "")
    leads = [
        item
        for item in report.get("leads") or ()
        if isinstance(item, Mapping)
    ]
    drafts: list[TalentPoolDraft] = []
    for theme in themes:
        source_leads = tuple(
            _source_lead(
                leads[index - 1],
                [
                    hypothesis["specific_title"]
                    for demand in company_demands
                    if demand["lead_index"] == index
                    for hypothesis in demand["hypotheses"]
                    if hypothesis["hypothesis_id"]
                    in theme["source_hypothesis_ids"]
                ],
            )
            for index in theme["source_lead_indices"]
        )
        payload = _payload_example(theme)
        identity = "\x1f".join(
            (run_date, direction, str(theme["theme_id"]))
        )
        drafts.append(
            TalentPoolDraft(
                draft_id="tp_"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
                run_date=run_date,
                direction=direction,
                talent_persona=f"能够承担“{theme['shared_mandate']}”的总监级人才",
                role_family=str(theme["role_family"]),
                seniority="Director+",
                attraction_angle=str(theme["why_now"]),
                recommended_title=str(theme["recommended_title"]),
                why_now=str(theme["why_now"]),
                source_leads=source_leads,
                source_role_hypotheses=tuple(
                    str(item) for item in theme["source_hypothesis_ids"]
                ),
                public_payload=payload,
                payload_hash=canonical_payload_hash(payload),
                expires_at="",
            )
        )
    return DraftBundle(
        schema_version=4,
        run_date=run_date,
        direction=direction,
        source_run_id=run_id,
        drafts=tuple(drafts),
        generation_provider="direct-llm-evidence-bound-themes",
        company_demand_analysis=company_demands,
        talent_themes=themes,
    )


def theme_payload_example(theme: Mapping[str, Any]) -> dict[str, Any]:
    return _payload_example(theme)


__all__ = [
    "build_talent_themes",
    "build_theme_draft_bundle",
    "theme_payload_example",
]
