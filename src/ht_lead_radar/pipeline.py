from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Iterable
from urllib.parse import urlparse

from .models import CompanyLead, Evidence, OutreachRoute, ScoreComponent
from .role_inference import matched_topics_for_evidence, roles_for
from .taxonomy import classify_seniority


WEIGHTS: dict[str, tuple[float, float]] = {
    "factory_or_capacity": (24, 120),
    "major_order": (18, 60),
    "funding": (16, 60),
    "global_expansion": (12, 60),
    "data_or_model": (12, 90),
    "technical_milestone": (10, 90),
    "executive_change": (10, 45),
    "partnership": (10, 75),
    "job_ad": (3, 14),
}

UPSTREAM_PHASES = {"strategy_capital", "build_organize"}
COMMERCIAL_EVENTS = {
    "factory_or_capacity",
    "major_order",
    "funding",
    "global_expansion",
}
ROLE_FUNCTION_TERMS = (
    "研发",
    "算法",
    "产品",
    "数据",
    "临床",
    "医学",
    "注册",
    "法规",
    "质量",
    "制造",
    "量产",
    "运营",
    "供应链",
    "交付",
    "客户",
    "商业化",
    "战略",
    "组织",
    "投资",
    "海外",
    "全球",
    "政府",
    "项目",
    "基地",
    "厂务",
    "ehs",
    "工艺",
    "工程",
    "试验",
    "验证",
    "总装",
    "采购",
    "科研",
    "产业合作",
    "解决方案",
    "生态合作",
)


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    for pattern in (
        r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})",
        r"(20\d{2})[-/](\d{1,2})",
    ):
        match = re.search(pattern, value)
        if match:
            groups = [int(item) for item in match.groups()]
            if len(groups) == 2:
                groups.append(1)
            try:
                return date(groups[0], groups[1], groups[2])
            except ValueError:
                return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _decayed_weight(item: Evidence, as_of: date) -> float:
    base, half_life = WEIGHTS.get(item.event_type, (4, 60))
    occurred = _parse_date(item.event_date)
    age = max((as_of - occurred).days, 0) if occurred else 30
    return base * math.pow(2, -age / half_life)


def _route_from_dict(item: dict) -> OutreachRoute:
    return OutreachRoute(
        kind=item["kind"],
        target=item["target"],
        path="",
        evidence_url=item["evidence_url"],
        grade=item.get("grade", "B"),
        note=item["note"],
    )


def build_leads(
    direction: str,
    evidence: Iterable[Evidence],
    metadata: dict | None = None,
    as_of: date | None = None,
    minimum_score: float = 0,
    limit: int = 20,
    source_topics: Iterable[str] = (),
) -> list[CompanyLead]:
    effective_date = as_of or date.today()
    grouped: dict[str, list[Evidence]] = defaultdict(list)
    for item in evidence:
        grouped[item.company].append(item)

    routes_by_company = (metadata or {}).get("routes", {})
    ad_checks = (metadata or {}).get("ad_checks", {})
    verifications = (metadata or {}).get("verification", {})
    leads: list[CompanyLead] = []

    for company, items in grouped.items():
        upstream = [
            item
            for item in items
            if item.phase in UPSTREAM_PHASES and item.event_type != "job_ad"
        ]
        director_ads = [
            item
            for item in items
            if item.event_type == "job_ad"
            and classify_seniority(item.title, item.snippet)[1]
        ]
        event_types = {item.event_type for item in upstream}
        company_context = " ".join(f"{item.title} {item.snippet}" for item in upstream)
        matched_topics = matched_topics_for_evidence(items, source_topics)
        role_direction = matched_topics[0] if matched_topics else direction
        roles = roles_for(
            role_direction,
            event_types,
            company_context=company_context,
        )
        related_ads = [
            item for item in director_ads if _job_ad_matches_roles(item, roles)
        ]
        target_gate = bool(roles) and all(
            _is_director_plus_role(role) for role in roles
        )
        upstream_gate = _upstream_precedes_job_ads(upstream, related_ads)
        if not target_gate or not upstream_gate:
            continue

        weights_by_type: dict[str, list[float]] = defaultdict(list)
        seen_events: set[str] = set()
        for item in upstream:
            event_key = (
                item.event_id
                or f"{item.event_type}|{item.event_date}|{item.source_url}"
            )
            if event_key in seen_events:
                continue
            seen_events.add(event_key)
            weights_by_type[item.event_type].append(
                _decayed_weight(item, effective_date)
            )
        need_score = 0.0
        for values in weights_by_type.values():
            ordered = sorted(values, reverse=True)
            need_score += ordered[0] + sum(ordered[1:]) * 0.25
        need_score = min(need_score, 32)
        role_score = min(17 + len(event_types) * 3, 25)
        timing_stage = "ad_live" if related_ads else "pre_ad"
        timing_score = 5 if related_ads else 15
        commercial_score = min(
            len(event_types & COMMERCIAL_EVENTS) * 4
            + (4 if "factory_or_capacity" in event_types else 0),
            10,
        )
        unique_sources = {item.source_url for item in upstream}
        unique_domains = {urlparse(item.source_url).netloc.lower() for item in upstream}
        grades = {item.source_grade for item in upstream}
        confidence_score = (
            10
            if len(unique_domains) >= 3 and "A" in grades
            else 8
            if len(unique_domains) >= 2 and "B" in grades
            else 5
        )
        corroboration_bonus = (
            3 if len(unique_domains) >= 2 and len(event_types) >= 2 else 0
        )
        advertised_penalty = -10 if related_ads else 0
        score_components = [
            ScoreComponent(
                key="need",
                label="需求信号",
                points=round(need_score, 1),
                reason=f"{len(seen_events)} 个独立上游事件，按事件类型和时间衰减计分",
                evidence_urls=tuple(
                    dict.fromkeys(item.source_url for item in upstream)
                ),
            ),
            ScoreComponent(
                key="role",
                label="总监级岗位逻辑",
                points=round(role_score, 1),
                reason="、".join(roles),
                evidence_urls=tuple(
                    dict.fromkeys(item.source_url for item in upstream)
                ),
            ),
            ScoreComponent(
                key="timing",
                label="介入时机",
                points=round(timing_score, 1),
                reason="尚未发现相关公开广告"
                if not related_ads
                else "已发现相关广告，窗口变窄",
                evidence_urls=tuple(item.source_url for item in related_ads),
            ),
            ScoreComponent(
                key="commercial",
                label="商业化强度",
                points=round(commercial_score, 1),
                reason=f"商业事件：{', '.join(sorted(event_types & COMMERCIAL_EVENTS)) or '无'}",
                evidence_urls=tuple(
                    dict.fromkeys(
                        item.source_url
                        for item in upstream
                        if item.event_type in COMMERCIAL_EVENTS
                    )
                ),
            ),
            ScoreComponent(
                key="confidence",
                label="证据可信度",
                points=round(confidence_score + corroboration_bonus, 1),
                reason=f"{len(unique_domains)} 个独立域名；最高来源等级 {min(grades) if grades else '未知'}",
                evidence_urls=tuple(sorted(unique_sources)),
            ),
        ]
        if advertised_penalty:
            score_components.append(
                ScoreComponent(
                    key="advertised_penalty",
                    label="公开招聘扣分",
                    points=advertised_penalty,
                    reason="目标岗位或同职能总监级广告已经公开",
                    evidence_urls=tuple(item.source_url for item in related_ads),
                )
            )
        score = min(sum(component.points for component in score_components), 99)
        if score < minimum_score:
            continue

        confidence = (
            "A"
            if len(unique_domains) >= 3 and "A" in grades
            else "B"
            if len(unique_domains) >= 2 and "B" in grades
            else "C"
        )
        routes = [_route_from_dict(item) for item in routes_by_company.get(company, [])]
        risk_notes: list[str] = []
        if related_ads:
            risk_notes.append("已发现相关总监级公开职位，竞争窗口变窄。")
        check = ad_checks.get(company)
        if check and check.get("matching_results", 0) == 0:
            checked_at = check.get("checked_at", "未知日期")
            risk_notes.append(
                f"截至 {checked_at} 的指定来源检索中，未发现与预测岗位同职能的总监级广告；这不是全网无广告的证明。"
            )
            other_ads = check.get("other_director_ads", [])
            if other_ads:
                other_ads_text = "、".join(other_ads)
                risk_notes.append(
                    f"已发现其他职能的总监级广告：{other_ads_text}；说明企业已进入部分公开招聘阶段。"
                )
        if len(unique_sources) < 2:
            risk_notes.append("独立证据不足，必须补充研究后再触达。")
        verification = verifications.get(company)
        if verification:
            if verification.get("status") == "ok":
                risk_notes.append(
                    f"Metaso 已限额核验 1 次，找到 {verification.get('matching_results', 0)} 条同公司匹配结果；"
                    "固定信源仍是 Lead 发现依据。"
                )
            else:
                risk_notes.append(
                    "Metaso 限额核验未完成；不影响固定信源发现结果，但触达前需要人工复核。"
                )

        lead_time = _lead_time(upstream, related_ads)
        known_people = list(
            dict.fromkeys(person for item in items for person in item.people)
        )
        known_organizations = list(
            dict.fromkeys(org for item in items for org in item.organizations)
        )
        basic_research = {
            "external_investors_or_institutions": known_organizations,
            "matched_source_topics": matched_topics,
            "public_people_in_evidence": known_people,
            "internal_roles_to_research": [
                roles[0] if roles else "业务部门负责人",
                "HRBP/人才招聘负责人",
                "创始团队/CEO办公室",
            ],
            "depth": "basic",
        }
        leads.append(
            CompanyLead(
                company=company,
                direction=direction,
                score=round(score, 1),
                confidence_grade=confidence,
                timing_stage=timing_stage,
                target_roles=roles,
                hiring_thesis=_hiring_thesis(
                    company,
                    role_direction,
                    event_types,
                    roles,
                ),
                evidence=sorted(items, key=lambda item: item.event_date, reverse=True),
                outreach_routes=routes,
                risk_notes=risk_notes,
                lead_time_days=lead_time,
                gates={
                    "director_plus": target_gate,
                    "has_upstream_signal": bool(upstream),
                    "upstream_precedes_job_ad": upstream_gate,
                },
                score_components=score_components,
                industry_layer=(metadata or {})
                .get("industry_layers", {})
                .get(company, "core"),
                mainland_relevance=(metadata or {})
                .get("mainland_relevance", {})
                .get(company, "中国大陆招聘市场相关"),
                request_mode=(metadata or {}).get("request_mode", "market_scan"),
                basic_research=basic_research,
            )
        )

    return sorted(leads, key=lambda lead: (-lead.score, lead.company))[: max(limit, 0)]


def build_late_opportunities(
    direction: str,
    evidence: Iterable[Evidence],
) -> list[dict]:
    """Return job-ad-only director-level companies as an appendix, never main Top 20."""
    grouped: dict[str, list[Evidence]] = defaultdict(list)
    for item in evidence:
        grouped[item.company].append(item)
    output: list[dict] = []
    for company, items in grouped.items():
        upstream = [
            item
            for item in items
            if item.phase in UPSTREAM_PHASES and item.event_type != "job_ad"
        ]
        ads = [item for item in items if item.event_type == "job_ad"]
        if upstream or not ads:
            continue
        director_ads = [
            item for item in ads if classify_seniority(item.title, item.snippet)[1]
        ]
        if not director_ads:
            continue
        output.append(
            {
                "company": company,
                "reason": "只有公开招聘广告，没有招聘前上游信号；不进入主 Top 20。",
                "ads": [item.source_url for item in director_ads],
            }
        )
    return sorted(output, key=lambda item: item["company"])


def _is_director_plus_role(role: str) -> bool:
    return any(
        term in role.lower()
        for term in (
            "总监",
            "总经理",
            "总裁",
            "平台主管",
            "总师",
            "首席",
            "director",
            "head",
            "vp",
            "cxo",
        )
    )


def _job_ad_matches_roles(item: Evidence, roles: list[str]) -> bool:
    """Return whether a Director advert matches a predicted role function."""

    text = f"{item.title} {item.snippet}".casefold()
    role_terms = {
        term.casefold()
        for role in roles
        for term in ROLE_FUNCTION_TERMS
        if term.casefold() in role.casefold()
    }
    return bool(role_terms) and any(term in text for term in role_terms)


def _upstream_precedes_job_ads(
    upstream: list[Evidence],
    ads: list[Evidence],
) -> bool:
    """Require proof that an upstream event predates the first known job ad.

    Without an ad, any upstream event satisfies the timing gate. Once an ad
    exists, every ad must have a parseable date and at least one upstream event
    must have a parseable date strictly before the earliest ad. This fails
    closed when an undated ad makes the first-ad boundary unknowable.
    """
    if not upstream:
        return False
    if not ads:
        return True
    ad_dates = [_parse_date(item.event_date) for item in ads]
    if any(value is None for value in ad_dates):
        return False
    first_ad = min(value for value in ad_dates if value is not None)
    return any(
        occurred < first_ad
        for item in upstream
        if (occurred := _parse_date(item.event_date)) is not None
    )


def _lead_time(upstream: list[Evidence], ads: list[Evidence]) -> int | None:
    if not ads:
        return None
    upstream_dates = [
        value for item in upstream if (value := _parse_date(item.event_date))
    ]
    ad_dates = [value for item in ads if (value := _parse_date(item.event_date))]
    if not upstream_dates or not ad_dates:
        return None
    return (min(ad_dates) - min(upstream_dates)).days


def _hiring_thesis(
    company: str, direction: str, event_types: set[str], roles: list[str]
) -> str:
    drivers: list[str] = []
    if "funding" in event_types:
        drivers.append("新增资本可支持组织扩张")
    if "factory_or_capacity" in event_types:
        drivers.append("量产/产能建设要求建立制造与交付管理体系")
    if "major_order" in event_types:
        drivers.append("订单和客户交付会放大供应链、质量与项目管理压力")
    if "data_or_model" in event_types:
        drivers.append("数据和模型建设需要独立的平台型领导者")
    if "global_expansion" in event_types:
        drivers.append("海外扩张需要区域业务与交付负责人")
    if "partnership" in event_types:
        drivers.append("产业合作正在从技术验证转向规模化落地")
    driver_text = "；".join(drivers[:3]) or "多个上游事件正在形成新的组织责任"
    roles_text = "、".join(roles)
    return f"{company}在{direction}方向出现多源上游信号：{driver_text}。因此未来 30–90 天最可能新增或升级的领导岗位为：{roles_text}。"
