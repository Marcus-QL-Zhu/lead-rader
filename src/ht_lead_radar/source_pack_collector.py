"""Incremental collector for validated fixed-source packs.

Only public, directly retrievable HTML and standard feed formats are handled.
Browser-only, blocked, or disabled sources are never fetched here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
import html
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Iterable, Mapping
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .collectors import _event_date_from_text, infer_event
from .models import Evidence
from .source_packs import DEFAULT_REGISTRY_PATH, SourceDefinition, SourcePackRegistry


DIRECT_ADAPTERS = frozenset(
    {
        "direct_html",
        "html_list",
        "html_homepage_list",
        "rss",
        "json_feed",
    }
)

_EVENT_TERMS = re.compile(
    r"融资|增资|投资|入股|募资|工厂|基地|产线|扩产|产能|量产|交付|"
    r"订单|中标|定点|采购|招标|预算|意向|项目|申报|揭榜|试点|"
    r"政策|指南|标准|名单|获批|注册|临床|试验|发布|首发|突破|"
    r"样机|迭代|合作|签约|任命|履新|加盟|出任|接任|换帅|人事调整|出海|海外|投产|建设|"
    r"环评|环境影响|发射|首飞|点火|试车|投运|专有权|登记|"
    r"并购|收购|控制权|合资|分拆|上市辅导|招股书|区域总部|子公司|"
    r"事业部|客户验证|供应商认证|渠道|经销商|研究院|知识产权|"
    r"数字化转型|信息化|\bERP\b|\bMES\b|\bPLM\b|\bCRM\b|"
    r"funding|raises?|raised|venture round|seed round|series [a-f]"
)

_EARLY_EVENT_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "procurement_tender",
        "strategy_capital",
        re.compile(r"公开招标|招标公告|采购公告|竞争性磋商|竞争性谈判"),
    ),
    (
        "procurement_intention",
        "strategy_capital",
        re.compile(r"采购意向|拟采购|预算金额|采购预算|计划采购"),
    ),
    (
        "eia_or_permit",
        "strategy_capital",
        re.compile(r"环境影响|环评|拟审查|建设项目受理|批复"),
    ),
    (
        "project_buildout",
        "strategy_capital",
        re.compile(r"项目启动|项目开工|建设项目|项目落地|竣工|投运"),
    ),
    (
        "project_call",
        "build_organize",
        re.compile(r"项目申报|申报指南|揭榜挂帅|征集项目|项目指南|试点示范"),
    ),
    (
        "regulatory_or_clinical",
        "build_organize",
        re.compile(r"获批|注册证|临床试验|临床研究|受理|审评|审批"),
    ),
    (
        "policy_or_standard",
        "build_organize",
        re.compile(r"政策|指导意见|行动方案|管理办法|标准|规范|白皮书"),
    ),
    (
        "award_or_supplier",
        "strategy_capital",
        re.compile(r"中标|成交|供应商|入围|定点|合同"),
    ),
    (
        "technical_milestone",
        "build_organize",
        re.compile(r"发射|首飞|点火|试车|装置建成|专有权|布图设计登记"),
    ),
)

_EVENT_SIGNAL_COMPATIBILITY: Mapping[str, frozenset[str]] = {
    "factory_or_capacity": frozenset(
        {
            "factory",
            "capacity_expansion",
            "project_buildout",
            "device_buildout",
            "mass_production",
            "capacity_milestone",
            "facility_opening",
        }
    ),
    "major_order": frozenset(
        {
            "order",
            "major_contract",
            "contract_award",
            "customer_validation",
            "delivery",
            "project_execution",
            "procurement_tender",
            "equipment_purchase",
            "application_project",
        }
    ),
    "funding": frozenset(
        {
            "funding",
            "investment",
            "investor",
            "investor_comment",
            "fund_launch",
            "merger_acquisition",
        }
    ),
    "global_expansion": frozenset({"market_expansion", "company_activity"}),
    "data_or_model": frozenset({"technology_milestone", "technology_asset"}),
    "technical_milestone": frozenset(
        {
            "technology_milestone",
            "product_launch",
            "launch",
            "test",
            "project_milestone",
            "technology_asset",
            "company_activity",
            "industry_event",
            "award",
        }
    ),
    "executive_change": frozenset({"executive_change", "leadership"}),
    "merger_acquisition": frozenset(
        {
            "merger_acquisition",
            "investment",
            "company_activity",
        }
    ),
    "joint_venture_or_spinout": frozenset(
        {
            "merger_acquisition",
            "partnership",
            "company_activity",
        }
    ),
    "ipo_or_listing": frozenset(
        {
            "ipo",
            "listing",
            "company_activity",
            "investment",
        }
    ),
    "new_site_or_entity": frozenset(
        {
            "regional_hq",
            "new_subsidiary",
            "company_activity",
            "facility_opening",
            "market_expansion",
        }
    ),
    "customer_validation": frozenset(
        {
            "customer_validation",
            "product_validation",
            "delivery",
            "contract_award",
            "supply_chain",
        }
    ),
    "channel_expansion": frozenset(
        {
            "market_expansion",
            "partnership",
            "supply_chain",
            "company_activity",
        }
    ),
    "research_or_ip": frozenset(
        {
            "research_program",
            "technology_asset",
            "technology_milestone",
            "partnership",
            "company_activity",
        }
    ),
    "enterprise_system": frozenset(
        {
            "digital_transformation",
            "company_activity",
            "project_buildout",
        }
    ),
    "workforce_cluster": frozenset(
        {
            "talent_program",
            "company_activity",
        }
    ),
    "partnership": frozenset(
        {
            "partnership",
            "international_cooperation",
            "supply_chain",
        }
    ),
    "job_ad": frozenset({"talent_program"}),
    "procurement_intention": frozenset(
        {
            "procurement_intention",
            "planned_budget",
            "future_project",
            "procurement_tender",
            "equipment_purchase",
            "project_buildout",
            "application_project",
        }
    ),
    "procurement_tender": frozenset(
        {
            "procurement_tender",
            "equipment_purchase",
            "project_buildout",
            "application_project",
            "planned_budget",
            "future_project",
        }
    ),
    "eia_or_permit": frozenset(
        {
            "eia_acceptance",
            "eia_approval",
            "factory",
            "capacity_expansion",
            "project_buildout",
            "regulatory_action",
        }
    ),
    "project_buildout": frozenset(
        {
            "project_buildout",
            "factory",
            "capacity_expansion",
            "device_buildout",
            "facility_opening",
            "future_project",
            "application_project",
        }
    ),
    "project_call": frozenset(
        {
            "project_call",
            "pilot_program",
            "pilot_platform",
            "research_program",
            "supplier_call",
            "award_list",
            "policy",
        }
    ),
    "regulatory_or_clinical": frozenset(
        {
            "regulatory_approval",
            "clinical",
            "clinical_trial_registration",
            "clinical_site",
            "principal_investigator",
            "product_validation",
            "registration",
            "medical_device_guidance",
            "eia_acceptance",
            "eia_approval",
        }
    ),
    "policy_or_standard": frozenset(
        {
            "policy",
            "standard",
            "regulatory_action",
            "medical_device_guidance",
        }
    ),
    "award_or_supplier": frozenset(
        {
            "contract_award",
            "customer_validation",
            "delivery",
            "project_execution",
            "major_contract",
            "order",
            "supplier_call",
            "supply_chain",
        }
    ),
}

_OFFICIAL_LEGAL_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "集团公司",
    "集团",
)

_STRUCTURED_COMPANY = re.compile(
    r"(?:建设单位|建设主体|项目单位|招标人|采购人|中标人|中标供应商|成交供应商|"
    r"供应商|申请人|申报单位|承建单位|实施单位|企业名称|公司名称)"
    r"\s*(?:[:：]|为)\s*"
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()\-]{2,60}?"
    r"(?:股份有限公司|有限责任公司|有限公司|集团公司|集团|研究院|研究所|大学))"
)
_QUOTED_COMPANY = re.compile(
    r"[“「『《](?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()\- ]{2,40})[”」』》]"
)
_LEADING_COMPANY = re.compile(
    r"^(?:20\d{2}年\d{1,2}月\d{1,2}日\s*)?"
    r"(?:(?:首发|独家|重磅|快讯|融资消息|会员资讯)\s*[｜|丨：:]\s*)*"
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()\- ]{2,40}?)"
    r"\s*(?:完成|获得|获|宣布|计划|启动|签署|签约|拿下|发布|开启|拟|"
    r"迎来|任命|更换|换帅|履新|出任|接任|加盟|落地|开工|投产)"
)
_LEGAL_COMPANY = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()\-]{2,60}?"
    r"(?:股份有限公司|有限责任公司|有限公司|集团公司))"
)
_FUNDING_SUBJECT_PATTERNS = (
    re.compile(
        r"(?:^|[｜|丨：:]\s*|[“「『《])"
        r"(?:20\d{2}年\d{1,2}月\d{1,2}日\s*)?"
        r"(?:(?:首发|独家|重磅|快讯|融资消息|会员资讯)\s*[｜|丨：:]\s*)*"
        r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()\- ]{2,60}?)"
        r"(?:[”」』》])?\s*(?:完成|获得|获).{0,30}?(?:融资|投资|增资)"
    ),
    re.compile(
        r"(?:领投|投资|增资|入股)"
        r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()\-]{2,60}?"
        r"(?:股份有限公司|有限责任公司|有限公司|集团公司))"
        r".{0,20}?(?:融资|增资|入股|$)"
    ),
)
_EMPLOYER_FIRST_EXECUTIVE = re.compile(
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()\- ]{2,40}?)"
    r"(?:宣布|任命|聘任|委任).{0,20}?(?:出任|担任|为)"
    r".{0,12}?(?:董事长|总裁|副总裁|总经理|CEO|CTO|COO|首席|董事|总监)"
)
_EXECUTIVE_COMPANY = re.compile(
    r"(?:出任|担任|接任|获任命为|任命为|履新|加盟)"
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·（）()\- ]{2,40}?)"
    r"(?:中国区|大中华区|亚太区|全球)?"
    r"(?:董事长|总裁|副总裁|总经理|CEO|CTO|COO|首席|董事|总监)"
)


@dataclass(frozen=True)
class DiscoveredDocument:
    title: str
    url: str
    summary: str = ""
    published_at: str = ""


@dataclass(frozen=True)
class FetchResult:
    url: str
    status_code: int
    body: bytes
    content_type: str
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False


@dataclass(frozen=True)
class SourceDocumentObservation:
    source_id: str
    source_name: str
    source_grade: str
    industry_tags: tuple[str, ...]
    signal_types: tuple[str, ...]
    title: str
    source_url: str
    published_at: str
    observed_at: str
    content_hash: str
    text_excerpt: str
    company_candidates: tuple[str, ...]
    event_type: str
    topic: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._anchor_text: list[str] = []
        self._capture_title = False
        self._capture_h1 = False
        self._title_text: list[str] = []
        self._h1_text: list[str] = []
        self._all_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "a":
            values = dict(attrs)
            self._href = str(values.get("href") or "").strip()
            self._anchor_text = []
        elif lowered == "title":
            self._capture_title = True
        elif lowered == "h1" and not self._h1_text:
            self._capture_h1 = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "a" and self._href:
            text = _compact_text(" ".join(self._anchor_text))
            if text:
                self.links.append((self._href, text))
            self._href = ""
            self._anchor_text = []
        elif lowered == "title":
            self._capture_title = False
        elif lowered == "h1":
            self._capture_h1 = False

    def handle_data(self, data: str) -> None:
        cleaned = _compact_text(data)
        if not cleaned:
            return
        self._all_text.append(cleaned)
        if self._href:
            self._anchor_text.append(cleaned)
        if self._capture_title:
            self._title_text.append(cleaned)
        if self._capture_h1:
            self._h1_text.append(cleaned)

    @property
    def page_title(self) -> str:
        return _compact_text(" ".join(self._h1_text or self._title_text))

    @property
    def page_text(self) -> str:
        return _compact_text(" ".join(self._all_text))


def _compact_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _decode_body(body: bytes, content_type: str) -> str:
    match = re.search(r"charset\s*=\s*([^;\s]+)", content_type, re.I)
    encodings = [match.group(1).strip("'") if match else ""]
    encodings.extend(["utf-8", "gb18030"])
    for encoding in dict.fromkeys(item for item in encodings if item):
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ET.Element, names: Iterable[str]) -> str:
    wanted = set(names)
    for child in element:
        if _local_name(child.tag) in wanted:
            return _compact_text("".join(child.itertext()))
    return ""


def _parse_html_documents(
    result: FetchResult,
    source: SourceDefinition,
) -> tuple[list[DiscoveredDocument], str]:
    text = _decode_body(result.body, result.content_type)
    parser = _LinkParser()
    parser.feed(text)
    if source.adapter == "direct_html":
        title = parser.page_title or source.name
        return [
            DiscoveredDocument(
                title=title,
                url=result.url,
                summary=parser.page_text,
                published_at=_event_date_from_text(parser.page_text, ""),
            )
        ], parser.page_text

    documents: list[DiscoveredDocument] = []
    seen: set[str] = set()
    for href, title in parser.links:
        url = urllib.parse.urljoin(result.url, href)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        normalized = urllib.parse.urlunparse(parsed._replace(fragment=""))
        if normalized in seen:
            continue
        seen.add(normalized)
        documents.append(
            DiscoveredDocument(
                title=title,
                url=normalized,
                published_at=_event_date_from_text(title, ""),
            )
        )
    return documents, parser.page_text


def _parse_rss_documents(result: FetchResult) -> list[DiscoveredDocument]:
    try:
        root = ET.fromstring(result.body)
    except ET.ParseError as exc:
        raise ValueError(f"invalid RSS/Atom XML: {exc}") from exc
    documents: list[DiscoveredDocument] = []
    for element in root.iter():
        if _local_name(element.tag) not in {"item", "entry"}:
            continue
        title = _child_text(element, {"title"})
        link = _child_text(element, {"link"})
        if not link:
            for child in element:
                if _local_name(child.tag) == "link" and child.attrib.get("href"):
                    link = str(child.attrib["href"])
                    break
        summary = _child_text(
            element,
            {"description", "summary", "content", "content:encoded"},
        )
        published = _child_text(
            element,
            {"pubdate", "published", "updated", "date"},
        )
        url = urllib.parse.urljoin(result.url, link)
        parsed = urllib.parse.urlparse(url)
        if title and parsed.scheme in {"http", "https"} and parsed.netloc:
            documents.append(
                DiscoveredDocument(
                    title=title,
                    url=urllib.parse.urlunparse(parsed._replace(fragment="")),
                    summary=summary,
                    published_at=_event_date_from_text(
                        f"{published} {summary}", published
                    ),
                )
            )
    return documents


def _parse_json_feed_documents(result: FetchResult) -> list[DiscoveredDocument]:
    try:
        payload = json.loads(_decode_body(result.body, result.content_type))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON Feed: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("JSON Feed must contain an items array")
    documents: list[DiscoveredDocument] = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            continue
        title = _compact_text(str(item.get("title") or ""))
        raw_url = str(item.get("url") or item.get("external_url") or "").strip()
        summary = _compact_text(
            str(
                item.get("summary")
                or item.get("content_text")
                or item.get("content_html")
                or ""
            )
        )
        published = str(item.get("date_published") or item.get("date_modified") or "")
        url = urllib.parse.urljoin(result.url, raw_url)
        parsed = urllib.parse.urlparse(url)
        if title and parsed.scheme in {"http", "https"} and parsed.netloc:
            documents.append(
                DiscoveredDocument(
                    title=title,
                    url=urllib.parse.urlunparse(parsed._replace(fragment="")),
                    summary=summary,
                    published_at=_event_date_from_text(
                        f"{published} {summary}", published
                    ),
                )
            )
    return documents


def _canonical_official_owner(owner: str) -> str:
    cleaned = re.sub(r"[（(][^）)]*[）)]", "", owner).strip()
    for suffix in _OFFICIAL_LEGAL_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    return cleaned


def _plausible_explicit_company(name: str) -> bool:
    normalized = _compact_text(name).strip(" ，,。；;：:")
    if re.match(r"^20\d{2}年\d{1,2}月\d{1,2}日", normalized):
        return False
    exact_noise = {
        "公司",
        "企业",
        "项目",
        "团队",
        "行业",
        "赛道",
        "领域",
        "市场",
        "具身智能",
        "半导体",
        "商业航天",
        "脑机接口",
        "机器人",
        "某公司",
        "中国区",
        "大中华区",
        "亚太区",
        "全球",
    }
    noise_fragments = (
        "该项目",
        "本项目",
        "重大项目",
        "行业",
        "赛道",
        "领域",
        "最新消息",
        "重磅",
        "通知",
        "公告",
        "方案",
        "指南",
        "标准",
        "报告",
        "白皮书",
    )
    non_company_endings = (
        "项目",
        "基地",
        "产业园",
        "工程",
        "计划",
        "产品",
        "装置",
        "中心",
        "实验室",
        "研究院",
        "研究所",
        "大学",
    )
    return (
        2 <= len(normalized) <= 40
        and normalized not in exact_noise
        and not any(fragment in normalized for fragment in noise_fragments)
        and not normalized.endswith(non_company_endings)
        and bool(re.search(r"[\u4e00-\u9fffA-Za-z]", normalized))
    )


def _company_candidates(
    source: SourceDefinition, title: str, body: str
) -> tuple[str, ...]:
    if source.source_type == "company_official":
        owner = _canonical_official_owner(source.owner)
        return (owner,) if len(owner) >= 2 else ()
    text = f"{title} {body}"
    found: list[str] = []
    found.extend(
        match.group("name").strip(" ，,。；;")
        for match in _STRUCTURED_COMPANY.finditer(text)
    )
    if _EVENT_TERMS.search(text):
        funding_title = bool(re.search(r"融资|募资|领投|战略投资|增资|入股", title))
        if funding_title:
            for pattern in _FUNDING_SUBJECT_PATTERNS:
                for match in pattern.finditer(title):
                    candidate = match.group("name").strip(" ，,。；;")
                    if _plausible_explicit_company(candidate):
                        found.append(candidate)
        else:
            for match in _LEGAL_COMPANY.finditer(title):
                candidate = match.group("name").strip(" ，,。；;")
                if _plausible_explicit_company(candidate):
                    found.append(candidate)
            for match in _QUOTED_COMPANY.finditer(title):
                candidate = match.group("name").strip(" ，,。；;")
                if _plausible_explicit_company(candidate):
                    found.append(candidate)
        leading = _LEADING_COMPANY.search(title)
        if (
            leading
            and not re.search(r"出任|任命|履新|接任|换帅|人事调整", title)
            and _plausible_explicit_company(leading.group("name"))
        ):
            found.append(leading.group("name").strip(" ，,。；;"))
        for match in _EMPLOYER_FIRST_EXECUTIVE.finditer(title):
            candidate = match.group("name").strip(" ，,。；;")
            if _plausible_explicit_company(candidate):
                found.append(candidate)
        for match in _EXECUTIVE_COMPANY.finditer(title):
            candidate = match.group("name").strip(" ，,。；;")
            if _plausible_explicit_company(candidate):
                found.append(candidate)
    return tuple(dict.fromkeys(name for name in found if 2 <= len(name) <= 60))


def _infer_source_event(text: str) -> tuple[str, str]:
    event_type, phase = infer_event(text)
    if event_type != "other":
        return event_type, phase
    for candidate_type, candidate_phase, pattern in _EARLY_EVENT_RULES:
        if pattern.search(text):
            return candidate_type, candidate_phase
    return "other", "build_organize"


def _event_supported(source: SourceDefinition, event_type: str) -> bool:
    compatible = _EVENT_SIGNAL_COMPATIBILITY.get(event_type, frozenset())
    return bool(compatible.intersection(source.signal_types))


def _topic_terms(registry: SourcePackRegistry, topic: str) -> tuple[str, ...]:
    terms = [topic.strip()]
    terms.extend(
        item.strip()
        for item in re.split(r"[、,/|与和及]", topic)
        if len(item.strip()) >= 2
    )
    for pack_id in registry.matching_pack_ids(topic):
        if pack_id == "generic-cn":
            continue
        pack = registry.get_pack(pack_id)
        terms.extend(pack.aliases)
        terms.extend(tag.replace("_", " ") for tag in pack.industry_tags)
    return tuple(
        dict.fromkeys(term.lower() for term in terms if len(term.strip()) >= 2)
    )


def _document_relevant(
    source: SourceDefinition,
    text: str,
    topic_terms: tuple[str, ...],
    generic_source_ids: frozenset[str],
) -> bool:
    lowered = text.lower()
    if not _EVENT_TERMS.search(lowered):
        return False
    if source.id in generic_source_ids:
        if source.source_type == "global_financing_media" and "融资" in topic_terms:
            return True
        return any(term in lowered for term in topic_terms)
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _scope_key(topic: str) -> str:
    return sha256(topic.strip().lower().encode("utf-8")).hexdigest()[:16]


class SourcePackCollector:
    """Collect Evidence from the registry's stable, directly readable sources."""

    provider_name = "validated fixed-source packs"
    supports_search = False

    def __init__(
        self,
        registry_path: str | Path = DEFAULT_REGISTRY_PATH,
        state_db: str | Path = "data/source-pack-state.sqlite3",
        timeout: float = 20.0,
        *,
        registry: SourcePackRegistry | None = None,
        user_agent: str = "HT-Lead-Radar/0.1 (+fixed-source monitoring)",
        detail_fetch: bool = True,
        max_bytes: int = 5_000_000,
        urlopen: Callable[..., Any] | None = None,
        dedicated_llm_runner: Any | None = None,
    ) -> None:
        self.registry = registry or SourcePackRegistry.load(registry_path)
        self.state_db = state_db
        self.timeout = timeout
        self.user_agent = user_agent
        self.detail_fetch = detail_fetch
        self.max_bytes = max_bytes
        self._urlopen = urlopen or urllib.request.urlopen
        self._dedicated_llm_runner = dedicated_llm_runner
        if str(state_db) != ":memory:":
            Path(state_db).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(state_db))
        self._connection.row_factory = sqlite3.Row
        self._initialize_state()
        self.last_run_summary: dict[str, Any] = {}

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SourcePackCollector":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _initialize_state(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_pack_http_state (
                source_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                cache_scope TEXT NOT NULL,
                etag TEXT NOT NULL DEFAULT '',
                last_modified TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                checked_at TEXT NOT NULL,
                success_at TEXT NOT NULL DEFAULT '',
                status_code INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (source_id, source_url, cache_scope)
            );
            CREATE TABLE IF NOT EXISTS source_pack_documents (
                source_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                topic TEXT NOT NULL,
                title TEXT NOT NULL,
                published_at TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                company_candidates_json TEXT NOT NULL,
                event_type TEXT NOT NULL,
                observation_json TEXT NOT NULL,
                PRIMARY KEY (source_id, source_url, topic)
            );
            CREATE TABLE IF NOT EXISTS source_pack_evidence (
                source_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                topic TEXT NOT NULL,
                company TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_date TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                PRIMARY KEY (
                    source_id, source_url, topic, company, event_type, event_id
                )
            );
            CREATE TABLE IF NOT EXISTS source_pack_source_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                discovered_count INTEGER NOT NULL DEFAULT 0,
                observation_count INTEGER NOT NULL DEFAULT 0,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                detail_error_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_source_pack_evidence_topic
            ON source_pack_evidence(topic, last_seen_at);
            CREATE INDEX IF NOT EXISTS idx_source_pack_documents_topic
            ON source_pack_documents(topic, last_seen_at);
            """
        )
        self._migrate_source_pack_evidence_event_identity()
        self._connection.commit()

    @staticmethod
    def _evidence_identity(payload: Mapping[str, Any]) -> str:
        explicit = str(payload.get("event_id") or "").strip()
        if explicit:
            return explicit
        slots = payload.get("event_slots")
        canonical_slots = slots if isinstance(slots, Mapping) else {}
        canonical = json.dumps(
            {
                "company": str(payload.get("company") or ""),
                "event_type": str(payload.get("event_type") or ""),
                "event_date": str(payload.get("event_date") or ""),
                "title": str(payload.get("title") or ""),
                "snippet": str(payload.get("snippet") or ""),
                "event_slots": canonical_slots,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def _migrate_source_pack_evidence_event_identity(self) -> None:
        tables = {
            str(row["name"])
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        has_legacy = "source_pack_evidence_legacy" in tables
        columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(source_pack_evidence)"
            ).fetchall()
        }
        if "event_id" in columns and not has_legacy:
            return
        if has_legacy and "event_id" not in columns:
            raise sqlite3.DatabaseError(
                "source_pack_evidence migration has incompatible tables"
            )

        source_table = (
            "source_pack_evidence_legacy" if has_legacy else "source_pack_evidence"
        )
        rows = self._connection.execute(f"SELECT * FROM {source_table}").fetchall()
        prepared: list[tuple[str, ...]] = []
        for row in rows:
            payload = json.loads(str(row["evidence_json"]))
            event_id = self._evidence_identity(payload)
            payload["event_id"] = event_id
            prepared.append(
                (
                    str(row["source_id"]),
                    str(row["source_url"]),
                    str(row["topic"]),
                    str(row["company"]),
                    str(row["event_type"]),
                    event_id,
                    str(row["event_date"]),
                    str(row["first_seen_at"]),
                    str(row["last_seen_at"]),
                    json.dumps(payload, ensure_ascii=False),
                )
            )

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if not has_legacy:
                self._connection.execute(
                    """
                    ALTER TABLE source_pack_evidence
                    RENAME TO source_pack_evidence_legacy
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE source_pack_evidence (
                        source_id TEXT NOT NULL,
                        source_url TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        company TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        event_id TEXT NOT NULL,
                        event_date TEXT NOT NULL DEFAULT '',
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        evidence_json TEXT NOT NULL,
                        PRIMARY KEY (
                            source_id, source_url, topic, company,
                            event_type, event_id
                        )
                    )
                    """
                )
            self._connection.executemany(
                """
                INSERT OR REPLACE INTO source_pack_evidence (
                    source_id, source_url, topic, company, event_type, event_id,
                    event_date, first_seen_at, last_seen_at, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                prepared,
            )
            self._connection.execute("DROP TABLE source_pack_evidence_legacy")
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_source_pack_evidence_topic
                ON source_pack_evidence(topic, last_seen_at)
                """
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def _conditional_headers(
        self, source: SourceDefinition, url: str, scope: str
    ) -> dict[str, str]:
        row = self._connection.execute(
            """
            SELECT etag, last_modified
            FROM source_pack_http_state
            WHERE source_id = ? AND source_url = ? AND cache_scope = ?
            """,
            (source.id, url, scope),
        ).fetchone()
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html, application/rss+xml, application/atom+xml, application/feed+json, application/json;q=0.9, */*;q=0.5",
        }
        if row and row["etag"]:
            headers["If-None-Match"] = str(row["etag"])
        if row and row["last_modified"]:
            headers["If-Modified-Since"] = str(row["last_modified"])
        return headers

    def _record_http(
        self,
        source: SourceDefinition,
        url: str,
        scope: str,
        *,
        status_code: int,
        etag: str = "",
        last_modified: str = "",
        content_hash: str = "",
        error: str = "",
        success: bool = False,
    ) -> None:
        now = _utc_now()
        self._connection.execute(
            """
            INSERT INTO source_pack_http_state (
                source_id, source_url, cache_scope, etag, last_modified,
                content_hash, checked_at, success_at, status_code, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, source_url, cache_scope) DO UPDATE SET
                etag = CASE WHEN excluded.etag <> '' THEN excluded.etag ELSE etag END,
                last_modified = CASE
                    WHEN excluded.last_modified <> '' THEN excluded.last_modified
                    ELSE last_modified
                END,
                content_hash = CASE
                    WHEN excluded.content_hash <> '' THEN excluded.content_hash
                    ELSE content_hash
                END,
                checked_at = excluded.checked_at,
                success_at = CASE
                    WHEN excluded.success_at <> '' THEN excluded.success_at
                    ELSE success_at
                END,
                status_code = excluded.status_code,
                error = excluded.error
            """,
            (
                source.id,
                url,
                scope,
                etag,
                last_modified,
                content_hash,
                now,
                now if success else "",
                status_code,
                error[:1000],
            ),
        )
        self._connection.commit()

    def _fetch(self, source: SourceDefinition, url: str, topic: str) -> FetchResult:
        scope = _scope_key(topic)
        request = urllib.request.Request(
            url,
            headers=self._conditional_headers(source, url, scope),
            method="GET",
        )
        try:
            with self._urlopen(request, timeout=self.timeout) as response:
                status_value = getattr(response, "status", None)
                status = int(
                    status_value if status_value is not None else response.getcode()
                )
                try:
                    body = response.read(self.max_bytes + 1)
                except TypeError:
                    body = response.read()
                if len(body) > self.max_bytes:
                    raise ValueError(f"response exceeds {self.max_bytes} bytes")
                headers = response.headers
                etag = str(headers.get("ETag") or "")
                modified = str(headers.get("Last-Modified") or "")
                content_type = str(headers.get("Content-Type") or "")
                final_url = str(response.geturl() or url)
                digest = sha256(body).hexdigest()
                self._record_http(
                    source,
                    url,
                    scope,
                    status_code=status,
                    etag=etag,
                    last_modified=modified,
                    content_hash=digest,
                    success=True,
                )
                return FetchResult(
                    url=final_url,
                    status_code=status,
                    body=body,
                    content_type=content_type,
                    etag=etag,
                    last_modified=modified,
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                headers = exc.headers or {}
                self._record_http(
                    source,
                    url,
                    scope,
                    status_code=304,
                    etag=str(headers.get("ETag") or ""),
                    last_modified=str(headers.get("Last-Modified") or ""),
                    success=True,
                )
                return FetchResult(
                    url=url,
                    status_code=304,
                    body=b"",
                    content_type=str(headers.get("Content-Type") or ""),
                    etag=str(headers.get("ETag") or ""),
                    last_modified=str(headers.get("Last-Modified") or ""),
                    not_modified=True,
                )
            self._record_http(
                source,
                url,
                scope,
                status_code=int(exc.code),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        except Exception as exc:
            self._record_http(
                source,
                url,
                scope,
                status_code=0,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def _discover(
        self,
        source: SourceDefinition,
        result: FetchResult,
    ) -> list[DiscoveredDocument]:
        if result.not_modified:
            return []
        if source.adapter in {"direct_html", "html_list", "html_homepage_list"}:
            documents, _ = _parse_html_documents(result, source)
            return documents
        if source.adapter == "rss":
            return _parse_rss_documents(result)
        if source.adapter == "json_feed":
            return _parse_json_feed_documents(result)
        return []

    @staticmethod
    def _detail_text(result: FetchResult) -> str:
        if not result.body:
            return ""
        decoded = _decode_body(result.body, result.content_type)
        content_type = result.content_type.lower()
        if "html" in content_type or "<html" in decoded[:500].lower():
            parser = _LinkParser()
            parser.feed(decoded)
            return parser.page_text
        if "json" in content_type:
            try:
                return _compact_text(
                    json.dumps(json.loads(decoded), ensure_ascii=False)
                )
            except json.JSONDecodeError:
                return _compact_text(decoded)
        return _compact_text(decoded)

    def _store_observation(self, observation: SourceDocumentObservation) -> None:
        payload = json.dumps(observation.to_dict(), ensure_ascii=False)
        now = observation.observed_at
        self._connection.execute(
            """
            INSERT INTO source_pack_documents (
                source_id, source_url, topic, title, published_at,
                first_seen_at, last_seen_at, content_hash,
                company_candidates_json, event_type, observation_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, source_url, topic) DO UPDATE SET
                title = excluded.title,
                published_at = excluded.published_at,
                last_seen_at = excluded.last_seen_at,
                content_hash = excluded.content_hash,
                company_candidates_json = excluded.company_candidates_json,
                event_type = excluded.event_type,
                observation_json = excluded.observation_json
            """,
            (
                observation.source_id,
                observation.source_url,
                observation.topic,
                observation.title,
                observation.published_at,
                now,
                now,
                observation.content_hash,
                json.dumps(observation.company_candidates, ensure_ascii=False),
                observation.event_type,
                payload,
            ),
        )
        self._connection.commit()

    def _store_evidence(
        self,
        source: SourceDefinition,
        topic: str,
        evidence: Evidence,
        *,
        commit: bool = True,
    ) -> None:
        now = _utc_now()
        payload = asdict(evidence)
        event_id = self._evidence_identity(payload)
        payload["event_id"] = event_id
        self._connection.execute(
            """
            INSERT INTO source_pack_evidence (
                source_id, source_url, topic, company, event_type, event_id,
                event_date, first_seen_at, last_seen_at, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                source_id, source_url, topic, company, event_type, event_id
            )
            DO UPDATE SET
                event_date = excluded.event_date,
                last_seen_at = excluded.last_seen_at,
                evidence_json = excluded.evidence_json
            """,
            (
                source.id,
                evidence.source_url,
                topic,
                evidence.company,
                evidence.event_type,
                event_id,
                evidence.event_date,
                now,
                now,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        if commit:
            self._connection.commit()

    @staticmethod
    def _dedicated_evidence_relevant(
        source: SourceDefinition,
        evidence: Evidence,
        topic_terms: tuple[str, ...],
        generic_source_ids: frozenset[str],
    ) -> bool:
        text = " ".join(
            (
                evidence.company,
                evidence.title,
                evidence.snippet,
                evidence.source_excerpt,
            )
        )
        if _document_relevant(
            source,
            text,
            topic_terms,
            generic_source_ids,
        ):
            return True
        normalized_tags = {
            str(tag).strip().lower().replace(" ", "_")
            for tag in evidence.industry_tags
            if str(tag).strip() and str(tag).strip().lower() != "generic"
        }
        normalized_topic_terms = {
            term.strip().lower().replace(" ", "_")
            for term in topic_terms
            if term.strip()
        }
        return bool(normalized_tags.intersection(normalized_topic_terms))

    def _process_document(
        self,
        source: SourceDefinition,
        document: DiscoveredDocument,
        topic: str,
        topic_terms: tuple[str, ...],
        generic_source_ids: frozenset[str],
    ) -> tuple[bool, int, bool]:
        seed_text = _compact_text(f"{document.title} {document.summary}")
        seed_relevant = _document_relevant(
            source, seed_text, topic_terms, generic_source_ids
        )
        may_need_government_detail = (
            source.id in generic_source_ids
            and source.source_type in {"government", "government_industrial_park"}
            and bool(_EVENT_TERMS.search(seed_text))
        )
        if not seed_relevant and not may_need_government_detail:
            return False, 0, False

        body = document.summary
        detail_error = False
        if self.detail_fetch and document.url != source.url:
            try:
                detail = self._fetch(source, document.url, topic)
                if not detail.not_modified:
                    body = self._detail_text(detail) or body
            except Exception:
                detail_error = True

        text = _compact_text(f"{document.title} {body}")
        if not _document_relevant(source, text, topic_terms, generic_source_ids):
            return False, 0, detail_error
        event_type, phase = _infer_source_event(text)
        published_at = _event_date_from_text(text, document.published_at)
        companies = _company_candidates(source, document.title, body)
        observed_at = _utc_now()
        digest = sha256(text.encode("utf-8")).hexdigest()
        observation = SourceDocumentObservation(
            source_id=source.id,
            source_name=source.name,
            source_grade=source.grade,
            industry_tags=source.industry_tags,
            signal_types=source.signal_types,
            title=document.title,
            source_url=document.url,
            published_at=published_at,
            observed_at=observed_at,
            content_hash=digest,
            text_excerpt=text[:800],
            company_candidates=companies,
            event_type=event_type,
            topic=topic,
        )
        self._store_observation(observation)

        if (
            event_type == "other"
            or not companies
            or not _event_supported(source, event_type)
        ):
            return True, 0, detail_error

        evidence_count = 0
        document_id = sha256(f"{source.id}|{document.url}".encode("utf-8")).hexdigest()
        source_group = urllib.parse.urlparse(document.url).netloc.lower()
        for company in companies:
            event_id = sha256(
                f"{document_id}|{company}|{event_type}".encode("utf-8")
            ).hexdigest()
            evidence = Evidence(
                company=company,
                event_type=event_type,
                phase=phase,
                event_date=published_at,
                title=document.title,
                snippet=text[:500],
                source_url=document.url,
                source_name=f"{source.name} [{source.id}]",
                source_grade=source.grade,
                direction=topic,
                source_id=source.id,
                industry_tags=source.industry_tags,
                document_id=document_id,
                event_id=event_id,
                independent_source_group=source_group,
            )
            self._store_evidence(source, topic, evidence)
            evidence_count += 1
        return True, evidence_count, detail_error

    def _record_source_run(
        self,
        source_id: str,
        topic: str,
        started_at: str,
        *,
        status: str,
        discovered_count: int = 0,
        observation_count: int = 0,
        evidence_count: int = 0,
        detail_error_count: int = 0,
        error: str = "",
    ) -> dict[str, Any]:
        finished_at = _utc_now()
        self._connection.execute(
            """
            INSERT INTO source_pack_source_runs (
                source_id, topic, started_at, finished_at, status,
                discovered_count, observation_count, evidence_count,
                detail_error_count, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                topic,
                started_at,
                finished_at,
                status,
                discovered_count,
                observation_count,
                evidence_count,
                detail_error_count,
                error[:1000],
            ),
        )
        self._connection.commit()
        return {
            "source_id": source_id,
            "status": status,
            "discovered_count": discovered_count,
            "observation_count": observation_count,
            "evidence_count": evidence_count,
            "detail_error_count": detail_error_count,
            "error": error[:1000],
            "started_at": started_at,
            "finished_at": finished_at,
        }

    def collect(
        self,
        topic: str,
        year: int = 0,
        limit_per_query: int = 10,
    ) -> list[Evidence]:
        if limit_per_query < 1:
            raise ValueError("limit_per_query must be positive")
        selection = self.registry.select(topic)
        normalized_topic = selection.topic
        terms = _topic_terms(self.registry, normalized_topic)
        generic_ids = frozenset(self.registry.get_pack("generic-cn").source_ids)
        health: dict[str, dict[str, Any]] = {}
        dedicated_coordinator = None
        dedicated_semantic_mode = "rules_only"
        dedicated_semantic_error = ""

        from .aggregate_adapters.coordinator import (
            DedicatedAggregateCoordinator,
            PublicHttpFetcher,
        )
        from .aggregate_adapters.registry import DedicatedAdapterRegistry

        dedicated_registry = DedicatedAdapterRegistry.defaults()
        if dedicated_registry.source_ids.intersection(
            source.id for source in selection.sources
        ):
            runner = self._dedicated_llm_runner
            if (
                runner is not False
                and runner is None
                and os.environ.get("LEAD_RADAR_AGGREGATE_LLM", "1")
                not in {"0", "false", "False"}
            ):
                try:
                    from .openclaw_llm import OpenClawConfiguredLLMRunner

                    runner = OpenClawConfiguredLLMRunner()
                except Exception as exc:
                    dedicated_semantic_error = f"{type(exc).__name__}: {exc}"
                    runner = None
            if runner is not None and runner is not False:
                dedicated_semantic_mode = "minimax"
            dedicated_listing_urls = [
                dedicated_registry.for_source(source_id)
                .channel_for(source_id)
                .url
                for source_id in dedicated_registry.source_ids
            ]
            shared_listing_urls = {
                url
                for url in dedicated_listing_urls
                if dedicated_listing_urls.count(url) > 1
            }
            dedicated_coordinator = DedicatedAggregateCoordinator(
                state_db=self.state_db,
                registry=dedicated_registry,
                fetch=PublicHttpFetcher(
                    timeout=self.timeout,
                    max_bytes=self.max_bytes,
                    user_agent=self.user_agent,
                    urlopen=self._urlopen,
                    shared_get_urls=shared_listing_urls,
                ),
                llm_runner=runner if runner is not False else None,
                acceptance_dir=os.environ.get("LEAD_RADAR_AGGREGATE_ACCEPTANCE_DIR"),
            )

        for source in selection.sources:
            started_at = _utc_now()
            if (
                dedicated_coordinator is not None
                and source.id in dedicated_registry.source_ids
            ):
                result = dedicated_coordinator.collect_source(
                    source.id,
                    normalized_topic,
                )
                relevant_evidence = [
                    item
                    for item in result.evidence
                    if self._dedicated_evidence_relevant(
                        source,
                        item,
                        terms,
                        generic_ids,
                    )
                ]
                if result.run.status == "ok":
                    with self._connection:
                        self._connection.execute(
                            """
                            DELETE FROM source_pack_evidence
                            WHERE source_id = ? AND topic = ?
                            """,
                            (source.id, normalized_topic),
                        )
                        for item in relevant_evidence:
                            self._store_evidence(
                                source,
                                normalized_topic,
                                item,
                                commit=False,
                            )
                elif result.run.status == "partial":
                    with self._connection:
                        for item in relevant_evidence:
                            self._store_evidence(
                                source,
                                normalized_topic,
                                item,
                                commit=False,
                            )
                health[source.id] = self._record_source_run(
                    source.id,
                    normalized_topic,
                    started_at,
                    status=result.run.status,
                    discovered_count=result.run.listing_count,
                    observation_count=result.run.detail_success_count,
                    evidence_count=len(relevant_evidence),
                    detail_error_count=result.run.detail_failure_count,
                    error=result.run.error,
                )
                continue
            if source.adapter not in DIRECT_ADAPTERS:
                health[source.id] = self._record_source_run(
                    source.id,
                    normalized_topic,
                    started_at,
                    status="unsupported_adapter",
                )
                continue
            discovered_count = 0
            observation_count = 0
            evidence_count = 0
            detail_error_count = 0
            try:
                listing = self._fetch(source, source.url, normalized_topic)
                if listing.not_modified:
                    health[source.id] = self._record_source_run(
                        source.id,
                        normalized_topic,
                        started_at,
                        status="not_modified",
                    )
                    continue
                documents = self._discover(source, listing)
                discovered_count = len(documents)
                for document in documents:
                    observed, emitted, detail_error = self._process_document(
                        source,
                        document,
                        normalized_topic,
                        terms,
                        generic_ids,
                    )
                    observation_count += int(observed)
                    evidence_count += emitted
                    detail_error_count += int(detail_error)
                    if observation_count >= limit_per_query:
                        break
                health[source.id] = self._record_source_run(
                    source.id,
                    normalized_topic,
                    started_at,
                    status="ok",
                    discovered_count=discovered_count,
                    observation_count=observation_count,
                    evidence_count=evidence_count,
                    detail_error_count=detail_error_count,
                )
            except Exception as exc:
                health[source.id] = self._record_source_run(
                    source.id,
                    normalized_topic,
                    started_at,
                    status="error",
                    discovered_count=discovered_count,
                    observation_count=observation_count,
                    evidence_count=evidence_count,
                    detail_error_count=detail_error_count,
                    error=f"{type(exc).__name__}: {exc}",
                )

        for source in selection.disabled_sources:
            health[source.id] = self._record_source_run(
                source.id,
                normalized_topic,
                _utc_now(),
                status="disabled",
            )
        evidence = self.load_recent(
            normalized_topic,
            year=year,
            source_ids=tuple(source.id for source in selection.sources),
        )
        if dedicated_coordinator is not None:
            from .aggregate_adapters.storage import (
                AggregateStateStore,
                normalize_company_alias,
            )

            with AggregateStateStore(self.state_db) as aggregate_store:
                alias_map = aggregate_store.canonical_alias_map()
            evidence = [
                replace(
                    item,
                    company=alias_map.get(
                        normalize_company_alias(item.company),
                        item.company,
                    ),
                )
                for item in evidence
            ]
        statuses = [item["status"] for item in health.values()]
        self.last_run_summary = {
            "topic": normalized_topic,
            "year": year,
            "pack_ids": list(selection.pack_ids),
            "unmatched_topic": selection.unmatched_topic,
            "sources": health,
            "selected_source_count": len(selection.sources),
            "disabled_source_count": len(selection.disabled_sources),
            "fetched_source_count": sum(
                status not in {"disabled", "unsupported_adapter"} for status in statuses
            ),
            "failed_source_count": statuses.count("error"),
            "evidence_count": len(evidence),
            "errors": [
                f"{source_id}: {item['error']}"
                for source_id, item in health.items()
                if item["status"] == "error"
            ],
            "dedicated_aggregate": (
                dedicated_coordinator.health()
                if dedicated_coordinator is not None
                else {}
            ),
            "dedicated_semantic_mode": dedicated_semantic_mode,
            "dedicated_semantic_error": dedicated_semantic_error,
        }
        return evidence

    def load_recent(
        self,
        topic: str,
        days: int = 365,
        year: int = 0,
        source_ids: tuple[str, ...] | None = None,
    ) -> list[Evidence]:
        if source_ids is not None and not source_ids:
            return []
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        source_filter = ""
        parameters: list[Any] = [topic.strip(), cutoff]
        if source_ids is not None:
            source_filter = " AND source_id IN ({})".format(
                ",".join("?" for _ in source_ids)
            )
            parameters.extend(source_ids)
        rows = self._connection.execute(
            """
            SELECT evidence_json, first_seen_at
            FROM source_pack_evidence
            WHERE topic = ? AND last_seen_at >= ?
            """
            + source_filter
            + """
            ORDER BY event_date DESC, company ASC
            """,
            parameters,
        ).fetchall()
        output: list[Evidence] = []
        seen: set[tuple[str, str, str, str]] = set()
        for row in rows:
            payload = json.loads(str(row["evidence_json"]))
            event_date = str(payload.get("event_date") or "")
            if year and (not event_date or not event_date.startswith(f"{year:04d}-")):
                continue
            if not year and event_date and event_date < cutoff:
                continue
            if not event_date:
                payload["event_date"] = str(row["first_seen_at"])[:10]
            payload["people"] = tuple(payload.get("people") or ())
            payload["organizations"] = tuple(payload.get("organizations") or ())
            payload["statement_ids"] = tuple(payload.get("statement_ids") or ())
            payload["industry_tags"] = tuple(payload.get("industry_tags") or ())
            item = Evidence(**payload)
            key = (
                item.company,
                item.source_url,
                item.event_type,
                item.event_id or self._evidence_identity(payload),
            )
            if key not in seen:
                seen.add(key)
                output.append(item)
        output.sort(
            key=lambda item: (item.event_date, item.source_grade, item.company),
            reverse=True,
        )
        return output

    def load_observations(
        self,
        topic: str,
        days: int = 365,
        limit: int = 500,
    ) -> list[SourceDocumentObservation]:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = self._connection.execute(
            """
            SELECT observation_json
            FROM source_pack_documents
            WHERE topic = ? AND last_seen_at >= ?
            ORDER BY published_at DESC, last_seen_at DESC
            LIMIT ?
            """,
            (topic.strip(), cutoff, limit),
        ).fetchall()
        observations: list[SourceDocumentObservation] = []
        for row in rows:
            payload = json.loads(str(row["observation_json"]))
            payload["industry_tags"] = tuple(payload.get("industry_tags") or ())
            payload["signal_types"] = tuple(payload.get("signal_types") or ())
            payload["company_candidates"] = tuple(
                payload.get("company_candidates") or ()
            )
            observations.append(SourceDocumentObservation(**payload))
        return observations

    def source_health_summary(self, topic: str | None = None) -> dict[str, Any]:
        if topic:
            rows = self._connection.execute(
                """
                SELECT *
                FROM source_pack_source_runs
                WHERE topic = ?
                ORDER BY id DESC
                """,
                (topic.strip(),),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM source_pack_source_runs ORDER BY id DESC"
            ).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            source_id = str(row["source_id"])
            if source_id in latest:
                continue
            latest[source_id] = {
                "source_id": source_id,
                "topic": str(row["topic"]),
                "status": str(row["status"]),
                "started_at": str(row["started_at"]),
                "finished_at": str(row["finished_at"]),
                "discovered_count": int(row["discovered_count"]),
                "observation_count": int(row["observation_count"]),
                "evidence_count": int(row["evidence_count"]),
                "detail_error_count": int(row["detail_error_count"]),
                "error": str(row["error"]),
            }
        statuses = [item["status"] for item in latest.values()]
        from .aggregate_adapters.storage import AggregateStateStore

        with AggregateStateStore(self.state_db) as aggregate_store:
            dedicated_aggregate = aggregate_store.health()
        return {
            "sources": latest,
            "source_count": len(latest),
            "healthy_count": sum(
                status in {"ok", "not_modified"} for status in statuses
            ),
            "failed_count": statuses.count("error"),
            "unsupported_count": statuses.count("unsupported_adapter"),
            "disabled_count": statuses.count("disabled"),
            "dedicated_aggregate": dedicated_aggregate,
        }


__all__ = [
    "DIRECT_ADAPTERS",
    "DiscoveredDocument",
    "FetchResult",
    "SourceDocumentObservation",
    "SourcePackCollector",
]
