"""General public-search discovery for arbitrary industry requests.

Fixed source packs remain the primary path.  This collector is the bounded
fallback used when the selected packs do not yield enough attributable
company events.  It intentionally favours precision over extracting every
capitalised phrase from a search result.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable

from .collectors import (
    BingRSSCollector,
    SearchResult,
    _event_date_from_text,
    grade_source,
    infer_event,
)
from .models import Evidence, OutreachRoute


COMPANY_SUFFIXES = (
    "机器人",
    "科技",
    "智能",
    "航天",
    "火箭",
    "卫星",
    "能源",
    "聚变",
    "半导体",
    "微电子",
    "芯片",
    "医疗",
    "医药",
    "生物",
    "仪器",
    "材料",
    "电子",
    "光电",
    "电气",
    "系统",
    "装备",
    "工业",
    "集团",
    "股份",
    "有限公司",
)

GENERIC_NAMES = {
    "科技公司",
    "上市公司",
    "相关公司",
    "项目公司",
    "半导体行业",
    "人工智能",
    "商业航天",
    "核聚变",
    "脑机接口",
    "具身智能",
    "智能制造",
}

EVENT_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "project_approval",
        "strategy_capital",
        r"项目备案|项目核准|立项|建设许可|开工许可|重大项目|重点项目",
    ),
    (
        "land_or_environment",
        "strategy_capital",
        r"环评|环境影响评价|用地|拿地|土地出让|规划公示|能评",
    ),
    (
        "regulatory_approval",
        "strategy_capital",
        r"获批|批准|注册证|许可证|适航|受理|备案凭证|临床试验许可",
    ),
    (
        "industrial_fund",
        "strategy_capital",
        r"产业基金|政府引导基金|专项资金|补助资金|扶持资金",
    ),
    (
        "clinical_milestone",
        "build_organize",
        r"临床试验|临床入组|首例植入|伦理审批|医疗器械注册",
    ),
)


def plausible_company(name: str) -> bool:
    cleaned = name.strip("“”「」『』，,:：；;（）() ")
    if not 2 <= len(cleaned) <= 30 or cleaned in GENERIC_NAMES:
        return False
    if any(term in cleaned for term in ("哪些", "多少", "盘点", "排名", "行业", "市场份额")):
        return False
    return cleaned.endswith(COMPANY_SUFFIXES) or bool(
        re.fullmatch(r"[A-Z][A-Za-z0-9·._-]{1,23}", cleaned)
    )


def extract_company_names(text: str) -> list[str]:
    suffix_pattern = "|".join(map(re.escape, COMPANY_SUFFIXES))
    patterns = (
        rf"[“「『]([^”」』]{{2,30}}?(?:{suffix_pattern}))[”」』]",
        rf"([\u4e00-\u9fffA-Za-z0-9·（）()]{{2,26}}?(?:{suffix_pattern}))"
        r"(?=\s|，|,|。|；|;|：|:|完成|宣布|获得|获|签署|发布|投产|交付)",
        r"(?:首发[｜|]\s*)?([\u4e00-\u9fffA-Za-z0-9·（）()]{2,26}?)"
        r"(?=完成.{0,12}(?:融资|募资)|获得.{0,12}(?:融资|投资)|"
        r"宣布.{0,12}(?:融资|扩产|建厂))",
    )
    output: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            candidate = match.group(1).strip("“”「」『』，,:：；;（）() ")
            if plausible_company(candidate) and candidate not in output:
                output.append(candidate)
    return output


def infer_upstream_event(text: str) -> tuple[str, str]:
    inferred = infer_event(text)
    if inferred[0] != "other":
        return inferred
    for event_type, phase, pattern in EVENT_PATTERNS:
        if re.search(pattern, text, flags=re.I):
            return event_type, phase
    return inferred


def attributable_context(company: str, result: SearchResult) -> str:
    """Return only sentences that name the company.

    A headline naming the company may safely carry its own snippet.  Otherwise
    another company named in the headline blocks attribution.
    """

    title = result.title or ""
    snippet = result.snippet or ""
    if company.casefold() in title.casefold():
        return f"{title} {snippet}".strip()
    title_companies = extract_company_names(title)
    if title_companies and company not in title_companies:
        return ""
    sentences = re.split(r"(?<=[。！？；!?;])\s*|[\r\n]+", snippet)
    local = [
        sentence for sentence in sentences
        if company.casefold() in sentence.casefold()
    ]
    return " ".join(local)


class PlannedSearchCollector:
    """Bounded two-pass discovery using planner-generated queries."""

    supports_search = True

    def __init__(
        self,
        provider: BingRSSCollector,
        queries: Iterable[str],
        *,
        candidate_limit: int = 24,
        discovery_query_limit: int = 10,
    ):
        self.provider = provider
        self.provider_name = provider.provider_name
        self.queries = tuple(dict.fromkeys(query.strip() for query in queries if query.strip()))
        self.candidate_limit = max(candidate_limit, 1)
        self.discovery_query_limit = max(discovery_query_limit, 1)

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self.provider.search(query, limit=limit)

    def collect(
        self,
        direction: str,
        year: int = 0,
        limit_per_query: int = 10,
    ) -> list[Evidence]:
        effective_year = year or date.today().year
        discovery_queries = self.queries[: self.discovery_query_limit] or (
            f"{direction} 融资 扩产 量产 订单 {effective_year}",
            f"{direction} 项目 环评 获批 战略合作 {effective_year}",
        )
        candidates: list[str] = []
        for query in discovery_queries:
            for result in self.search(query, limit=limit_per_query):
                for company in extract_company_names(f"{result.title} {result.snippet}"):
                    if company not in candidates:
                        candidates.append(company)
                    if len(candidates) >= self.candidate_limit:
                        break
                if len(candidates) >= self.candidate_limit:
                    break
            if len(candidates) >= self.candidate_limit:
                break

        evidence: list[Evidence] = []
        seen: set[tuple[str, str]] = set()
        for company in candidates:
            company_queries = (
                f"{company} 融资 项目 获批 扩产 量产 订单 {effective_year}",
                f"{company} 环评 工厂 基地 临床 适航 战略合作 {effective_year}",
            )
            for query in company_queries:
                for result in self.search(query, limit=limit_per_query):
                    if not result.url or (company, result.url) in seen:
                        continue
                    local = attributable_context(company, result)
                    if not local:
                        continue
                    event_type, phase = infer_upstream_event(local)
                    if event_type in {"other", "job_ad"}:
                        continue
                    seen.add((company, result.url))
                    evidence.append(Evidence(
                        company=company,
                        event_type=event_type,
                        phase=phase,
                        event_date=_event_date_from_text(local, result.published_at),
                        title=result.title[:240],
                        snippet=result.snippet[:800],
                        source_url=result.url,
                        source_name=self.provider_name,
                        source_grade=grade_source(result.url),
                        direction=direction,
                    ))
        return evidence

    def collect_routes(
        self,
        company: str,
        direction: str,
        limit_per_query: int = 8,
    ) -> list[OutreachRoute]:
        return self.provider.collect_routes(
            company,
            direction,
            limit_per_query=limit_per_query,
        )


__all__ = [
    "PlannedSearchCollector",
    "attributable_context",
    "extract_company_names",
    "infer_upstream_event",
    "plausible_company",
]
