"""Reusable deterministic industry-signal extraction for aggregate adapters."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from .entities import canonical_company_name, is_company_like
from .finance_rules import FundingRuleConfig, extract_funding_events
from .models import CleanArticle, SemanticEvent, SourceChannel


_EVENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "executive_change",
        re.compile(
            r"任命|获任|出任|履新|上任|换帅|接任|辞任|离任|"
            r"新任.{0,12}(?:董事长|总裁|CEO|首席执行官|负责人)"
        ),
    ),
    (
        "factory_or_capacity",
        re.compile(r"扩产|投产|产能|新建.{0,10}(?:工厂|产线)|生产基地|工厂开工"),
    ),
    (
        "procurement_tender",
        re.compile(
            r"\u62db\u6807|\u91c7\u8d2d\u516c\u544a|\u91c7\u8d2d\u9879\u76ee|"
            r"\u4e2d\u6807\u5019\u9009|\u4e2d\u6807\u901a\u77e5|"
            r"\u6280\u672f\u7ade\u6807|"
            r"(?:\u573a\u666f|\u8d5b\u961f|\u65b9\u6848|\u9009\u624b)"
            r".{0,10}\u5f81\u96c6|"
            r"\u5f81\u96c6.{0,10}"
            r"(?:\u573a\u666f|\u8d5b\u961f|\u65b9\u6848|\u9009\u624b)"
        ),
    ),
    (
        "major_order",
        re.compile(r"中标|获得.{0,12}订单|签订.{0,12}合同|订单金额|采购合同"),
    ),
    (
        "partnership",
        re.compile(
            r"战略合作|签署.{0,12}合作|联合研发|达成.{0,12}合作|合作协议|"
            r"宣布与.{0,30}合作|与.{0,30}合作打造"
        ),
    ),
    (
        "customer_validation",
        re.compile(r"客户导入|获得定点|定点项目|通过验收|完成交付|首批交付|客户验证"),
    ),
    (
        "new_site_or_entity",
        re.compile(r"成立.{0,12}(?:子公司|合资公司)|落户|研发中心|区域总部|新基地"),
    ),
    (
        "regulatory_or_clinical",
        re.compile(r"获批|批准上市|临床试验|注册证|进入临床|IND获批"),
    ),
    (
        "policy_or_standard",
        re.compile(
            r"发布.{0,16}(?:政策|标准|指南|名单|行动方案)|"
            r"入选.{0,16}名单|试点|"
            r"(?:提出|印发).{0,20}(?:规划|行动方案|政策|标准|指南)"
        ),
    ),
    (
        "merger_acquisition",
        re.compile(r"收购|并购|要约购买|取得.{0,12}控股权|资产重组"),
    ),
    (
        "ipo_or_listing",
        re.compile(r"IPO|上市申请|提交招股书|辅导备案|挂牌上市|通过聆讯"),
    ),
    (
        "enterprise_system",
        re.compile(r"上线.{0,12}(?:ERP|MES|PLM|CRM)|数字化系统|企业系统"),
    ),
    (
        "technical_milestone",
        re.compile(
            r"发布.{0,24}(?:芯片|机器人|火箭|卫星|模型|产品|引擎|平台|软件|系统|工具)|"
            r"推出.{0,24}(?:AI\s*Agent|引擎|平台|软件|系统|工具)|"
            r"首款|首次.{0,16}(?:试飞|点火|验证|量产)|"
            r"实现.{0,16}(?:量产|突破|验证)|出货.{0,8}突破|"
            r"技术突破|规模化量产"
        ),
    ),
)
_FUTURE = re.compile(
    r"\u62df|\u8ba1\u5212|\u5c06\u4e8e|\u5373\u5c06|\u542f\u52a8|"
    r"\u5f00\u5de5|\u5efa\u8bbe\u4e2d|\u5f81\u96c6|\u62db\u52df|\u62a5\u540d"
)
_BACKGROUND_EXAMPLE = re.compile(r"时[，,]?也|例如|比如|作为.{0,12}案例")
_NON_OPERATIONAL_CONTEXT = re.compile(
    r"投融资平台.{0,30}天使轮到IPO"
)
_HISTORICAL = re.compile(r"此前|早在|曾于|历史上|去年|上年度")
_QUOTED = re.compile(r"[「『“\"【〔]([^」』”\"】〕]{2,50})[」』”\"】〕]")
_LEGAL = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·（）()]{4,60}"
    r"(?:有限责任公司|股份有限公司|有限公司|集团))"
)
_GENERIC = re.compile(
    r"(?:政策|标准|指南|名单|项目|产品|技术|行业|企业|公司)$|"
    r"^(?:近日|日前|据悉|消息称|公告显示|根据)"
)
_INDUSTRY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("semiconductor", re.compile(r"芯片|半导体|晶圆|光刻|封装|激光器", re.I)),
    (
        "embodied_intelligence",
        re.compile(r"机器人|具身智能|灵巧手|触觉|运动控制|机器视觉", re.I),
    ),
    ("commercial_space", re.compile(r"航天|卫星|火箭|发射|太空|SpaceX", re.I)),
    ("fusion_energy", re.compile(r"核聚变|聚变|超导|托卡马克|等离子体", re.I)),
    (
        "brain_computer_interface",
        re.compile(r"脑机接口|神经意图|神经调控|类脑|SNN", re.I),
    ),
    (
        "advanced_manufacturing",
        re.compile(r"装备|材料|传感器|量产|产能|工业|制造|涂层", re.I),
    ),
    (
        "artificial_intelligence",
        re.compile(r"人工智能|大模型|AI\b|Agent|模型|数据", re.I),
    ),
    ("biotech", re.compile(r"医药|医疗|临床|药物|生物科技|诊断", re.I)),
)
_PHASES = {
    "executive_change": "strategy_capital",
    "factory_or_capacity": "build_organize",
    "procurement_tender": "scale_delivery",
    "major_order": "scale_delivery",
    "partnership": "strategy_capital",
    "customer_validation": "scale_delivery",
    "new_site_or_entity": "build_organize",
    "regulatory_or_clinical": "scale_delivery",
    "policy_or_standard": "strategy_capital",
    "merger_acquisition": "strategy_capital",
    "ipo_or_listing": "strategy_capital",
    "enterprise_system": "build_organize",
    "technical_milestone": "scale_delivery",
}


CompanyResolver = Callable[
    [CleanArticle, str, str],
    tuple[str, tuple[str, ...]],
]


@dataclass(frozen=True)
class IndustryRuleConfig:
    processor: str
    event_types: tuple[str, ...] = ()
    company_resolver: CompanyResolver | None = None


def extract_industry_events(
    channel: SourceChannel,
    article: CleanArticle,
    *,
    config: IndustryRuleConfig,
) -> list[SemanticEvent]:
    """Extract evidence-bound operational signals from a clean article."""

    text = _clean(
        " ".join(
            (article.index.title, article.index.summary, article.clean_body)
        )
    )
    tags = tuple(
        tag for tag, pattern in _INDUSTRY_RULES if pattern.search(text)
    ) or ("other",)
    allowed = frozenset(config.event_types or channel.event_prior)
    structured = str(article.index.structured_data.get("company") or "").strip()
    primary = structured if _valid_company(structured) else _title_company(
        article.index.title
    )
    output: dict[tuple[str, str, str], SemanticEvent] = {}
    last_company = primary
    for sentence in _sentences(text):
        if (
            _HISTORICAL.search(sentence)
            or _BACKGROUND_EXAMPLE.search(sentence)
            or _NON_OPERATIONAL_CONTEXT.search(sentence)
        ):
            continue
        for event_type, pattern in _EVENT_PATTERNS:
            if allowed and event_type not in allowed:
                continue
            match = pattern.search(sentence)
            if not match:
                continue
            if (
                event_type == "new_site_or_entity"
                and re.search(
                    r"国家|国务院|政府|部委|规划|行动方案|政策|标准|指南",
                    sentence,
                )
            ):
                continue
            if (
                event_type == "partnership"
                and re.search(
                    r"已进入.{0,30}供应链|"
                    r"建立了.{0,30}(?:长期|稳定).{0,20}合作",
                    sentence,
                )
            ):
                continue
            if config.company_resolver:
                company, mentions = config.company_resolver(
                    article,
                    sentence,
                    event_type,
                )
            else:
                company, mentions = _company_for_sentence(
                    article,
                    sentence,
                    match,
                    primary,
                    last_company,
                )
            if not company:
                continue
            last_company = company
            status = (
                "started"
                if (
                    _FUTURE.search(sentence)
                    or (
                        event_type == "partnership"
                        and re.search(
                            r"宣布与.{0,30}合作打造|联合训练",
                            sentence,
                        )
                    )
                )
                else "completed"
            )
            quote = sentence[:500]
            event = SemanticEvent(
                source_id=channel.source_id,
                source_article_id=article.index.source_article_id,
                canonical_url=article.index.canonical_url,
                company_mentions=mentions or (company,),
                canonical_company=company,
                event_type=event_type,
                event_date=article.index.published_at[:10],
                industry_tags=tags,
                event_summary=quote[:300],
                evidence_quotes=(quote,),
                confidence="high" if company == primary and primary else "medium",
                processor=config.processor,
                content_hash=article.content_hash,
                phase=_PHASES[event_type],
                event_status=status,
            )
            key = (company, event_type, status)
            previous = output.get(key)
            previous_quote = (
                previous.evidence_quotes[0] if previous is not None else ""
            )
            current_subject = bool(
                company in quote
                or re.search(
                    r"\u8be5\u516c\u53f8|\u8be5\u4f01\u4e1a|"
                    r"\u8be5\u673a\u6784|\u5176",
                    quote,
                )
            )
            previous_subject = bool(
                company in previous_quote
                or re.search(
                    r"\u8be5\u516c\u53f8|\u8be5\u4f01\u4e1a|"
                    r"\u8be5\u673a\u6784|\u5176",
                    previous_quote,
                )
            )
            if previous is None or (
                current_subject,
                len(quote),
            ) > (
                previous_subject,
                len(previous_quote),
            ):
                output[key] = event
    return list(output.values())


def extract_media_events(
    channel: SourceChannel,
    article: CleanArticle,
    *,
    config: IndustryRuleConfig,
    funding_processor: str,
) -> list[SemanticEvent]:
    """Combine explicit funding and operational rules for media streams."""

    events = [
        *extract_funding_events(
            channel,
            article,
            config=FundingRuleConfig(processor=funding_processor),
        ),
        *extract_industry_events(channel, article, config=config),
    ]
    output: dict[tuple[str, str, str, str], SemanticEvent] = {}
    for event in events:
        key = (
            event.canonical_company,
            event.event_type,
            event.funding_round,
            event.event_status,
        )
        output.setdefault(key, event)
    return list(output.values())

def _title_company(title: str) -> str:
    leading_acronym = re.match(
        r"^([A-Z][A-Z0-9-]{2,15})(?=[\u4e00-\u9fff])",
        title,
    )
    if (
        leading_acronym
        and re.search(
            r"\u5f81\u96c6|\u62db\u52df|\u62a5\u540d|\u542f\u52a8",
            title,
        )
    ):
        return leading_acronym.group(1)
    for match in _QUOTED.finditer(title):
        candidate = match.group(1).strip()
        if _valid_company(candidate):
            return candidate
    legal = _LEGAL.search(title)
    if legal:
        return legal.group(1)
    prefix = re.split(
        r"任命|获任|出任|履新|扩产|投产|中标|签订|"
        r"战略合作|发布|收购|并购|IPO|成立|落户",
        title,
        maxsplit=1,
    )[0].strip(" ：:,")
    return prefix if _valid_company(prefix) else ""


def _company_for_sentence(
    article: CleanArticle,
    sentence: str,
    match: re.Match[str],
    primary: str,
    last_company: str,
) -> tuple[str, tuple[str, ...]]:
    prefix = sentence[: match.start()]
    quoted = [
        item.group(1).strip()
        for item in _QUOTED.finditer(prefix)
        if _valid_company(item.group(1).strip())
    ]
    if quoted:
        return quoted[-1], (quoted[-1],)
    legal = list(_LEGAL.finditer(prefix))
    if legal:
        company = legal[-1].group(1)
        return company, (company,)
    explicit_mixed = re.findall(
        r"([A-Za-z][A-Za-z0-9-]{1,30}中国)"
        r"(?:在[\u4e00-\u9fff]{2,8})?"
        r"(?=还?$|$)",
        prefix,
    )
    if explicit_mixed:
        return explicit_mixed[-1], (explicit_mixed[-1],)
    acronyms = [
        value
        for value in re.findall(
            r"(?<![A-Za-z0-9])([A-Z][A-Z0-9-]{2,15})(?=[\u4e00-\u9fff])",
            prefix,
        )
        if value
        not in {
            "CEO",
            "CTO",
            "CFO",
            "COO",
            "IPO",
            "ERP",
            "MES",
            "PLM",
            "CRM",
        }
    ]
    if acronyms:
        return acronyms[-1], (acronyms[-1],)
    segment = re.split(r"[閵嗗偊绱掗敍鐕傜幢閿?,]", prefix)[-1].strip()
    segment = re.sub(
        r"^(?:鏉╂垶妫﹟閺冦儱澧爘閹诡喗鍊潀濞戝牊浼呯粔鐨橀崗顒€鎲￠弰鍓с仛)\s*",
        "",
        segment,
    )
    segment = re.sub(
        r"(?:在[\u4e00-\u9fff]{2,8}|还|正式|今日)$",
        "",
        segment,
    )
    segment = re.sub(
        r"^(?:\d{1,2}月\d{1,2}日[，,]?)",
        "",
        segment,
    )
    segment = re.sub(r"^(?:游戏引擎公司|公司)", "", segment)
    segment = canonical_company_name(segment)
    if _valid_company(segment):
        return segment, (segment,)
    if primary and primary in sentence and _valid_company(primary):
        return primary, (primary,)
    if last_company and is_company_like(last_company):
        return last_company, (last_company,)
    return "", ()
def _sentences(text: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"(?<=[。！？；])", text)
        if item.strip()
    ]


def _valid_company(value: str) -> bool:
    return bool(is_company_like(value) and not _GENERIC.search(value))


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


__all__ = [
    "IndustryRuleConfig",
    "extract_industry_events",
    "extract_media_events",
]
