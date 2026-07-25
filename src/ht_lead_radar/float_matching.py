"""Pure, explainable Candidate Float ranking.

The Float layer deliberately sits *after* the two Market Scan hard gates.  It
does not discover companies, call the network, write candidate data, or
generate outreach copy.  It only re-ranks already eligible ``CompanyLead``
objects against an ephemeral ``CandidateProfile``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from typing import Any, Iterable
from urllib.parse import urlparse

from .models import CompanyLead
from .requests import CandidateProfile


_COMPANY_NEED_MAXIMA = {
    "need": (32.0, 18.0),
    "role": (25.0, 7.0),
    "commercial": (10.0, 5.0),
    "confidence": (13.0, 5.0),
}

_MISSING_LABELS = {
    "role_title": "候选人当前职位/核心职能尚未提供",
    "core_business_context": "候选人的核心业务场景尚未提供",
    "candidate_geography_preference": "候选人的工作地域偏好尚未提供",
    "leadership_scope": "候选人的团队规模、预算或组织搭建责任尚未提供",
}

_SENIORITY_TERMS = (
    "高级副总裁",
    "执行副总裁",
    "副总裁",
    "总经理",
    "总监",
    "负责人",
    "平台主管",
    "总师",
    "首席",
    "director",
    "head",
    "vicepresident",
    "vp",
    "cxo",
    "cto",
    "coo",
    "ceo",
)

_UPSTREAM_PHASES = {"strategy_capital", "build_organize"}


@dataclass(frozen=True)
class FloatScoreComponent:
    """One of the four required, independently explainable Float dimensions."""

    key: str
    label: str
    points: float
    max_points: float
    reasons: tuple[str, ...]
    evidence_urls: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()


@dataclass(frozen=True)
class FloatMatch:
    """Analysis-only Float result.

    The source ``CandidateProfile`` is intentionally not retained on this
    object.  ``to_dict`` therefore cannot accidentally serialize the runtime
    profile or assign it a persistent identifier.
    """

    rank: int
    company: str
    float_score: float
    market_scan_score: float
    confidence_grade: str
    target_roles: tuple[str, ...]
    score_components: tuple[FloatScoreComponent, ...]
    match_reasons: tuple[str, ...]
    candidate_selling_points: tuple[str, ...]
    risks_or_conflicts_to_verify: tuple[str, ...]
    missing_information: tuple[str, ...]
    evidence_that_would_change_ranking: tuple[str, ...]
    evidence_urls: tuple[str, ...]
    deep_research_required: bool = True
    analysis_only: bool = True
    candidate_profile_persisted: bool = False
    outreach_generated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def rank_candidate_float(
    candidate: CandidateProfile,
    leads: Iterable[CompanyLead],
    *,
    as_of: date | None = None,
    limit: int = 20,
) -> list[FloatMatch]:
    """Re-rank eligible Market Scan leads for an ephemeral candidate.

    An explicitly failed hard gate is never relaxed.  Empty legacy ``gates``
    dictionaries are accepted for backwards compatibility; callers using the
    current pipeline receives all three gate values.
    """

    effective_date = as_of or date.today()
    provisional: list[FloatMatch] = []
    for lead in leads:
        if lead.gates and not (
            lead.gates.get("director_plus", False)
            and lead.gates.get("has_upstream_signal", False)
            and lead.gates.get("upstream_precedes_job_ad", True)
        ):
            continue

        need = _score_company_need(lead)
        fit, fit_context = _score_candidate_match(candidate, lead)
        timing = _score_timing(lead, effective_date)
        relationships = _score_public_relationship_researchability(lead)
        components = (need, fit, timing, relationships)
        score = round(min(100.0, max(0.0, sum(item.points for item in components))), 1)

        missing = _missing_information(candidate, lead, fit_context)
        risks = _risks_to_verify(candidate, lead, fit_context, relationships)
        provisional.append(FloatMatch(
            rank=0,
            company=lead.company,
            float_score=score,
            market_scan_score=lead.score,
            confidence_grade=lead.confidence_grade,
            target_roles=tuple(lead.target_roles),
            score_components=components,
            match_reasons=_unique(
                (
                    *need.reasons,
                    *fit.reasons,
                    *timing.reasons,
                    *relationships.reasons,
                )
            ),
            candidate_selling_points=_candidate_selling_points(candidate, fit_context),
            risks_or_conflicts_to_verify=risks,
            missing_information=missing,
            evidence_that_would_change_ranking=_ranking_change_evidence(
                candidate,
                lead,
                fit_context,
                relationships,
            ),
            evidence_urls=_unique(
                url
                for component in components
                for url in component.evidence_urls
            ),
        ))

    ordered = sorted(
        provisional,
        key=lambda item: (-item.float_score, -item.market_scan_score, item.company),
    )[: min(max(limit, 0), 20)]
    return [replace(item, rank=index) for index, item in enumerate(ordered, 1)]


def _score_company_need(lead: CompanyLead) -> FloatScoreComponent:
    relevant = {
        item.key: item
        for item in lead.score_components
        if item.key in _COMPANY_NEED_MAXIMA
    }
    urls: list[str] = []
    reasons: list[str] = []
    if relevant:
        points = 0.0
        for key, (source_max, target_max) in _COMPANY_NEED_MAXIMA.items():
            item = relevant.get(key)
            if item is None:
                continue
            contribution = min(max(item.points, 0.0), source_max) / source_max * target_max
            points += contribution
            reasons.append(f"{item.label}：{item.reason}")
            urls.extend(item.evidence_urls)
    else:
        # Compatibility fallback for hand-built/legacy leads.  The result says
        # exactly what happened instead of pretending a detailed decomposition
        # was available.
        points = min(max(lead.score, 0.0), 99.0) / 99.0 * 35.0
        reasons.append("该 Lead 缺少 Market Scan 细分项，暂按原始分数等比例折算公司需求分")
        urls.extend(item.source_url for item in lead.evidence)

    if lead.hiring_thesis:
        reasons.append(f"岗位假设：{lead.hiring_thesis}")
    return FloatScoreComponent(
        key="company_need",
        label="公司需求",
        points=round(min(points, 35.0), 1),
        max_points=35.0,
        reasons=_unique(reasons),
        evidence_urls=_unique(urls),
    )


def _score_candidate_match(
    candidate: CandidateProfile,
    lead: CompanyLead,
) -> tuple[FloatScoreComponent, dict[str, Any]]:
    corpus_parts = [
        lead.direction,
        *lead.target_roles,
        lead.hiring_thesis,
        *(item.title for item in lead.evidence),
        *(item.snippet for item in lead.evidence),
    ]
    corpus = " ".join(part for part in corpus_parts if part)
    role_corpus = " ".join((*lead.target_roles, lead.hiring_thesis))
    reasons: list[str] = []
    uncertainties: list[str] = []
    urls = [item.source_url for item in lead.evidence]
    points = 0.0

    title_similarity = 0.0
    if candidate.role_title:
        candidate_function = _remove_seniority(candidate.role_title)
        title_similarity = max(
            (_text_similarity(candidate_function, target) for target in lead.target_roles),
            default=0.0,
        )
        title_similarity = max(title_similarity, _text_similarity(candidate_function, role_corpus))
        if title_similarity >= 0.72:
            points += 8.0
            reasons.append(
                f"候选人已提供职位“{candidate.role_title}”与预测岗位职能高度吻合"
            )
        elif title_similarity >= 0.42:
            points += 5.0
            reasons.append(
                f"候选人已提供职位“{candidate.role_title}”与预测岗位存在可迁移职能"
            )
        else:
            uncertainties.append(
                f"候选人职位“{candidate.role_title}”与预测岗位尚未形成可验证的直接职能匹配"
            )
    else:
        uncertainties.append("候选人职位缺失，未计职位匹配分")

    capability_matches: list[str] = []
    capability_inferred = "core_capabilities_from_role_title" in candidate.inferred_fields
    for capability in candidate.core_capabilities:
        if _text_similarity(capability, corpus) < 0.42:
            continue
        capability_matches.append(capability)
        points += 1.5 if capability_inferred else 3.0
    points = min(points, 18.0)
    if capability_matches:
        qualifier = "由职位名称推断、必须核实" if capability_inferred else "输入中已明确提供"
        reasons.append(
            f"能力匹配（{qualifier}）：{'、'.join(capability_matches[:4])}"
        )
    elif candidate.core_capabilities:
        uncertainties.append("已提供/推断的核心能力与公司公开岗位假设未形成直接文本证据")

    industry_matches = [
        item
        for item in (*candidate.industry_experience, *candidate.desired_directions)
        if _text_similarity(item, corpus) >= 0.5
    ]
    industry_points = min(len(_unique(industry_matches)) * 3.0, 6.0)
    points += industry_points
    if industry_matches:
        reasons.append(f"行业/业务场景相符：{'、'.join(_unique(industry_matches))}")
    elif candidate.industry_experience:
        uncertainties.append("候选人已提供的行业经历与公司方向尚未证实直接重合")

    if candidate.leadership_scope:
        points += 4.0
        reasons.append(
            f"已提供领导范围支持总监级责任假设：{'、'.join(candidate.leadership_scope[:3])}"
        )
    else:
        uncertainties.append("缺少团队、预算或组织搭建范围，未计领导力匹配分")
    if candidate.seniority == "director_plus":
        points += 3.0
        reasons.append("已提供职位名称达到总监级以上")
    else:
        uncertainties.append("候选人当前职级未证实达到总监级以上")

    desired_matches = [
        item for item in candidate.desired_directions
        if _text_similarity(item, corpus) >= 0.5
    ]
    if desired_matches:
        points += 2.0
        reasons.append(f"候选人目标方向相符：{'、'.join(desired_matches)}")

    geography_matches = [
        item for item in candidate.geography_preferences
        if _text_similarity(item, lead.mainland_relevance) >= 0.65
    ]
    geography_unknown = bool(candidate.geography_preferences and not geography_matches)
    if geography_matches:
        points += 2.0
        reasons.append(f"公开地域信息与偏好相符：{'、'.join(geography_matches)}")
    elif geography_unknown:
        uncertainties.append("公司具体用人地点不足，不能把地域偏好视为已经匹配")

    exclusion_conflicts = [
        item for item in candidate.exclusions
        if _text_similarity(item, corpus) >= 0.5
    ]
    if exclusion_conflicts:
        points -= min(10.0, 5.0 * len(exclusion_conflicts))
        uncertainties.append(
            f"候选人排除项可能冲突：{'、'.join(exclusion_conflicts)}"
        )

    context = {
        "title_similarity": title_similarity,
        "capability_matches": tuple(capability_matches),
        "capability_inferred": capability_inferred,
        "industry_matches": _unique(industry_matches),
        "desired_matches": _unique(desired_matches),
        "geography_matches": _unique(geography_matches),
        "geography_unknown": geography_unknown,
        "exclusion_conflicts": _unique(exclusion_conflicts),
    }
    return FloatScoreComponent(
        key="candidate_match",
        label="候选人匹配",
        points=round(min(max(points, 0.0), 35.0), 1),
        max_points=35.0,
        reasons=_unique(reasons) or ("候选人事实不足，本项没有正向匹配结论",),
        evidence_urls=_unique(urls),
        uncertainties=_unique(uncertainties),
    ), context


def _score_timing(lead: CompanyLead, as_of: date) -> FloatScoreComponent:
    reasons: list[str] = []
    uncertainties: list[str] = []
    if lead.timing_stage == "pre_ad":
        points = 14.0
        reasons.append("仍处于公开招聘广告之前，介入窗口相对更早")
    elif lead.timing_stage == "ad_live":
        points = 4.0
        reasons.append("相关公开招聘已经出现，介入窗口明显收窄")
        uncertainties.append("需核实招聘是否已进入供应商或候选人面试阶段")
    else:
        points = 8.0
        reasons.append(f"时机阶段为 {lead.timing_stage}，按中性窗口计分")
        uncertainties.append("时机阶段需要深度研究确认")

    upstream = [
        item
        for item in lead.evidence
        if item.phase in _UPSTREAM_PHASES and item.event_type != "job_ad"
    ]
    dated = [
        parsed
        for item in upstream
        if (parsed := _parse_date(item.event_date)) is not None
    ]
    if dated:
        age = max((as_of - max(dated)).days, 0)
        if age <= 30:
            points += 4.0
            reasons.append(f"最近一条上游信号距分析日 {age} 天")
        elif age <= 90:
            points += 3.0
            reasons.append(f"最近一条上游信号仍在 90 天强化窗口内（{age} 天）")
        elif age <= 180:
            points += 1.0
            reasons.append(f"最近一条上游信号在 180 天观察窗内（{age} 天）")
        else:
            uncertainties.append(f"最近一条可解析上游信号已过去 {age} 天")
    else:
        uncertainties.append("上游信号日期无法解析，未计时效加分")

    distinct_events = {
        item.event_id or f"{item.event_type}|{item.event_date}|{item.source_url}"
        for item in upstream
    }
    if len(distinct_events) >= 2:
        points += 2.0
        reasons.append(f"存在 {len(distinct_events)} 个独立上游事件，可交叉判断窗口")
    elif distinct_events:
        points += 1.0
        reasons.append("当前只有一个独立上游事件")
    return FloatScoreComponent(
        key="timing",
        label="介入时机",
        points=round(min(points, 20.0), 1),
        max_points=20.0,
        reasons=_unique(reasons),
        evidence_urls=_unique(item.source_url for item in upstream),
        uncertainties=_unique(uncertainties),
    )


def _score_public_relationship_researchability(
    lead: CompanyLead,
) -> FloatScoreComponent:
    research = lead.basic_research or {}
    people = _unique(
        (
            *(research.get("public_people_in_evidence") or ()),
            *(person for item in lead.evidence for person in item.people),
        )
    )
    institutions = _unique(
        (
            *(research.get("external_investors_or_institutions") or ()),
            *(org for item in lead.evidence for org in item.organizations),
        )
    )
    verified_routes = [
        route for route in lead.outreach_routes
        if route.evidence_url and route.target
    ]
    domains = {
        urlparse(item.source_url).netloc.lower()
        for item in lead.evidence
        if urlparse(item.source_url).netloc
    }
    reasons: list[str] = []
    uncertainties: list[str] = []
    points = 0.0
    if people:
        points += 3.0
        reasons.append(f"公开证据中出现可研究人物：{'、'.join(people[:4])}")
    else:
        uncertainties.append("公开证据中尚未出现具名内部决策者或主导投资人")
    if institutions:
        points += 2.0
        reasons.append(f"公开证据中出现机构线索：{'、'.join(institutions[:4])}")
    else:
        uncertainties.append("尚未识别可复核的投资机构或关系机构")
    if verified_routes:
        points += min(3.0, float(len({route.evidence_url for route in verified_routes})))
        reasons.append(f"已有 {len(verified_routes)} 条带公开依据的关系路径可供深研")
    else:
        uncertainties.append("尚无带公开依据的关系路径")
    if len(domains) >= 2:
        points += 2.0
        reasons.append(f"{len(domains)} 个公开域名可用于交叉研究")
    elif domains:
        points += 1.0
        reasons.append("当前只有一个公开域名可用于关系研究")
    return FloatScoreComponent(
        key="public_relationship_researchability",
        label="公开关系可研究性/触达可行性",
        points=round(min(points, 10.0), 1),
        max_points=10.0,
        reasons=_unique(reasons) or ("尚无足够公开关系线索，本项不作正向判断",),
        evidence_urls=_unique(
            (
                *(item.source_url for item in lead.evidence),
                *(route.evidence_url for route in verified_routes),
            )
        ),
        uncertainties=_unique(uncertainties),
    )


def _candidate_selling_points(
    candidate: CandidateProfile,
    context: dict[str, Any],
) -> tuple[str, ...]:
    points: list[str] = []
    if candidate.role_title:
        points.append(f"已提供职位：{candidate.role_title}")
    if context["capability_matches"]:
        prefix = "待核实推断能力" if context["capability_inferred"] else "已提供且与公司需求匹配的能力"
        points.append(f"{prefix}：{'、'.join(context['capability_matches'][:4])}")
    if context["industry_matches"]:
        points.append(f"已提供且相符的行业/业务场景：{'、'.join(context['industry_matches'])}")
    if candidate.leadership_scope:
        points.append(f"已提供领导范围：{'、'.join(candidate.leadership_scope[:3])}")
    if context["geography_matches"]:
        points.append(f"已提供且公开信息可支持的地域偏好：{'、'.join(context['geography_matches'])}")
    return _unique(points) or ("当前候选人事实不足，暂不形成正向卖点结论",)


def _risks_to_verify(
    candidate: CandidateProfile,
    lead: CompanyLead,
    context: dict[str, Any],
    relationships: FloatScoreComponent,
) -> tuple[str, ...]:
    risks: list[str] = []
    if context["capability_inferred"]:
        risks.append("核心能力由职位名称规则推断，不是候选人已确认事实")
    if context["title_similarity"] < 0.42 and candidate.role_title:
        risks.append("候选人现职与公司预测岗位的职能匹配较弱")
    if context["exclusion_conflicts"]:
        risks.append(f"候选人排除项可能与公司机会冲突：{'、'.join(context['exclusion_conflicts'])}")
    if context["geography_unknown"]:
        risks.append("候选人有地域偏好，但公司具体用人地点尚未证实")
    if lead.timing_stage == "ad_live":
        risks.append("相关岗位或同职能广告已经公开，成功窗口可能已变窄")
    if lead.confidence_grade == "C":
        risks.append("公司需求证据置信度为 C，岗位假设需优先复核")
    risks.extend(relationships.uncertainties)
    risks.extend(lead.risk_notes)
    return _unique(risks)


def _missing_information(
    candidate: CandidateProfile,
    lead: CompanyLead,
    context: dict[str, Any],
) -> tuple[str, ...]:
    missing = [
        _MISSING_LABELS.get(item, f"候选人字段尚未提供：{item}")
        for item in candidate.missing_critical_fields
    ]
    if not candidate.core_capabilities:
        missing.append("候选人的可验证核心能力/代表成果尚未提供")
    elif context["capability_inferred"]:
        missing.append("职位名称推断出的核心能力尚未由候选人经历或成果证实")
    if candidate.geography_preferences and context["geography_unknown"]:
        missing.append("公司目标岗位的具体办公地点/出差要求尚未证实")
    if not lead.outreach_routes:
        missing.append("公司内部 Hiring Manager、HR 与创始团队的公开关系证据尚未补齐")
    missing.extend(
        (
            "候选人的可入职时间尚未纳入当前 CandidateProfile",
            "候选人的薪酬与职级底线尚未纳入当前 CandidateProfile",
        )
    )
    return _unique(missing)


def _ranking_change_evidence(
    candidate: CandidateProfile,
    lead: CompanyLead,
    context: dict[str, Any],
    relationships: FloatScoreComponent,
) -> tuple[str, ...]:
    changes: list[str] = [
        f"公司正式确认或否认 {'、'.join(lead.target_roles) or '预测总监级岗位'} 的招聘 mandate",
        "出现更近的融资、扩产、订单、交付或组织变动证据",
        "发现相关总监级公开职位、候选人已进入面试或供应商已经进场（通常会下调时机分）",
        "核实候选人的真实团队规模、预算责任和可量化成果",
    ]
    if context["capability_inferred"] or not context["capability_matches"]:
        changes.append("候选人提供可验证项目，确认或推翻职位名称所暗示的核心能力")
    if candidate.industry_experience and not context["industry_matches"]:
        changes.append("获得候选人能力可跨行业迁移的项目证据，或确认行业经验不可迁移")
    if candidate.geography_preferences:
        changes.append("确认公司具体用人地点、出差要求与候选人地域约束是否相容")
    if relationships.points < relationships.max_points:
        changes.append("找到具名 Hiring Manager、HR、创始人或主导投资人的公开来源并确认其角色")
    if candidate.exclusions:
        changes.append("确认候选人排除项与公司业务、文化、地域或岗位范围是否真实冲突")
    return _unique(changes)


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    for pattern in (
        r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})",
        r"(20\d{2})[-/](\d{1,2})",
    ):
        match = re.search(pattern, value)
        if match:
            values = [int(item) for item in match.groups()]
            if len(values) == 2:
                values.append(1)
            try:
                return date(values[0], values[1], values[2])
            except ValueError:
                return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _remove_seniority(value: str) -> str:
    normalized = _normalize(value)
    for term in _SENIORITY_TERMS:
        normalized = normalized.replace(_normalize(term), "")
    return normalized


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())


def _text_similarity(left: str, right: str) -> float:
    """Conservative lexical similarity; no semantic facts are invented."""

    a = _normalize(left)
    b = _normalize(right)
    if not a or not b:
        return 0.0
    if len(a) >= 2 and a in b:
        return 1.0
    if len(b) >= 2 and b in a:
        return min(1.0, len(b) / len(a) + 0.2)
    if a.isascii() and b.isascii():
        words_a = set(re.findall(r"[a-z0-9]+", left.lower()))
        words_b = set(re.findall(r"[a-z0-9]+", right.lower()))
        return len(words_a & words_b) / max(min(len(words_a), len(words_b)), 1)
    grams_a = _ngrams(a)
    grams_b = _ngrams(b)
    return len(grams_a & grams_b) / max(min(len(grams_a), len(grams_b)), 1)


def _ngrams(value: str, size: int = 2) -> set[str]:
    if len(value) < size:
        return {value}
    return {value[index:index + size] for index in range(len(value) - size + 1)}


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", str(value)).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            output.append(cleaned)
    return tuple(output)


__all__ = [
    "FloatMatch",
    "FloatScoreComponent",
    "rank_candidate_float",
]
