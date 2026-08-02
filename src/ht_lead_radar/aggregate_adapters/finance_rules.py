"""Reusable deterministic funding-event extraction for aggregate adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Callable

from .body_scope import clean_semantic_body_scope
from .entities import canonical_company_name, is_company_like
from .models import CleanArticle, SemanticEvent, SourceChannel


_COMPLETED = re.compile(
    r"(?:完成|获得|获(?!悉)|斩获|宣布完成|官宣完成).{0,120}融资"
)
_STARTED = re.compile(
    r"(?:启动|开启|开始).{0,80}(?:融资|(?<![A-Z])[A-Z](?:\+{1,2})?轮)"
    r"|(?<![A-Z])[A-Z](?:\+{1,2})?轮(?:（[^）]{0,24}）)?"
    r".{0,20}(?:已)?(?:提前)?(?:开始|启动|开启)"
)
_ROUND = re.compile(
    r"(Pre-IPO(?:轮)?|Pre-[A-Z](?:\+{1,2})?(?:轮)?|"
    r"(?<![A-Z])[A-Z](?:\+{1,2})?轮|"
    r"天使(?:\+{1,2})?轮|种子(?:\+{1,2})?轮|战略融资)"
)
_AMOUNT = re.compile(
    r"((?:近|超|逾|数)?\s*\d+(?:\.\d+)?\s*"
    r"(?:万|千万|亿)\s*(?:元|美元|人民币)"
    r"|(?:近|超|逾)?\s*(?:数千万元|数亿元|千万级|亿元级|"
    r"千万元|亿元))"
)
_QUOTED = re.compile(r"[「『“\"【〔]([^」』”\"】〕]{2,40})[」』”\"】〕]")
_LEGAL = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9·（）()]{4,60}"
    r"(?:有限责任公司|股份有限公司|有限公司))"
)
_PREFIX = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9·（）()\- ]{2,40}?)"
    r"(?:已|正式|成功|宣布|官宣)*(?:完成|获得|获(?!悉)|斩获|启动|开启)"
)
_NON_TRANSACTIONAL_FUNDING = re.compile(
    r"\u83b7\u5956\u5373\u878d\u8d44|\u53c2\u8d5b\u5373\u8def\u6f14|"
    r"\u5956\u91d1\u6c60|\u878d\u8d44\u8f85\u5bfc|"
    r"\u610f\u5411\u878d\u8d44|\u8d44\u672c\u63a8\u8350|"
    r"\u5bf9\u63a5.{0,20}\u878d\u8d44|\u878d\u8d44\u673a\u9047|"
    r"\u6295\u878d\u8d44\u5e73\u53f0|"
    r"(?:\u4ece|\u8986\u76d6)\u5929\u4f7f\u8f6e\u5230IPO"
)
_HISTORICAL = re.compile(
    r"该轮融资之前|此前.{0,20}已于|曾经.{0,20}分别于|"
    r"在该轮融资之前"
)
_GENERIC_COMPANY = re.compile(
    r"(?:企业|品牌|项目|领域)(?:已于.*)?$|"
    r"^(?:同步|本次|此次|同时|投融资|融资|值得一提|原定\d+月)"
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


CompanyResolver = Callable[
    [CleanArticle, str, re.Match[str], str],
    tuple[str, tuple[str, ...]],
]


@dataclass(frozen=True)
class FundingRuleConfig:
    processor: str
    company_resolver: CompanyResolver | None = None


def extract_funding_events(
    channel: SourceChannel,
    article: CleanArticle,
    *,
    config: FundingRuleConfig,
) -> list[SemanticEvent]:
    """Extract every current funding event without using external knowledge."""

    parts = (
        (article.index.title, 1),
        (article.index.summary, 2),
        (clean_semantic_body_scope(article.clean_body), 3),
    )
    full_text = _clean(" ".join(value for value, _ in parts))
    tags = tuple(
        tag for tag, pattern in _INDUSTRY_RULES if pattern.search(full_text)
    ) or ("other",)
    primary, primary_mentions = _primary_company(article)
    last_company = primary
    candidates: dict[
        tuple[str, str, str],
        tuple[SemanticEvent, int],
    ] = {}
    for part, source_priority in parts:
        sentences = _sentences(_clean(part))
        for sentence_position, sentence in enumerate(sentences):
            if _NON_TRANSACTIONAL_FUNDING.search(sentence):
                continue
            evidence_sentence = sentence
            if sentence_position + 1 < len(sentences):
                followup = sentences[sentence_position + 1]
                if (
                    re.match(r"^(?:本轮|本次|此次)融资", followup)
                    and (_AMOUNT.search(followup) or "投资" in followup)
                ):
                    evidence_sentence = f"{sentence}{followup}"
            assertions = [
                *((match.start(), "completed", match) for match in _COMPLETED.finditer(sentence)),
                *((match.start(), "started", match) for match in _STARTED.finditer(sentence)),
            ]
            assertions.sort(key=lambda item: item[0])
            for _, status, assertion in assertions:
                if status == "started" and re.search(
                    r"即将|计划|拟|预计|将",
                    sentence[max(0, assertion.start() - 48) : assertion.start()],
                ):
                    # A future marker turns a syntactic "started financing"
                    # match into a planned/target event (for example,
                    # "即将启动新一轮融资").  Keep "已提前开始" as started.
                    status = "target"
                if _is_historical_assertion(
                    sentence,
                    assertion,
                    article.index.published_at,
                ):
                    continue
                if config.company_resolver:
                    company, mentions = config.company_resolver(
                        article,
                        sentence,
                        assertion,
                        last_company,
                    )
                else:
                    company, mentions = _company_for_event(
                        article,
                        sentence,
                        assertion,
                        last_company,
                        primary,
                        primary_mentions,
                    )
                if not company:
                    continue
                last_company = company
                round_name = _round_for_assertion(sentence, assertion, status)
                amount, cumulative = _amounts_for_assertion(evidence_sentence, assertion)
                quote = evidence_sentence[:500]
                event = SemanticEvent(
                    source_id=channel.source_id,
                    source_article_id=article.index.source_article_id,
                    canonical_url=article.index.canonical_url,
                    company_mentions=mentions or (company,),
                    canonical_company=company,
                    event_type="funding",
                    event_date=article.index.published_at[:10],
                    industry_tags=tags,
                    funding_round=round_name,
                    funding_amount=amount,
                    cumulative_funding_amount=cumulative,
                    event_summary=quote[:300],
                    evidence_quotes=(quote,),
                    confidence="high" if company == primary and primary else "medium",
                    processor=config.processor,
                    content_hash=article.content_hash,
                    phase=(
                        "build_organize"
                        if status == "completed"
                        else "strategy_capital"
                    ),
                    event_status=status,
                )
                key = (company, round_name, status)
                previous = candidates.get(key)
                quality = (
                    source_priority,
                    int(bool(amount)),
                    int(bool(cumulative)),
                    len(quote),
                )
                if previous is None or quality > _quality(previous):
                    candidates[key] = (event, source_priority)
    return [
        event
        for event, _ in _resolve_conflicts(candidates).values()
    ]


def _primary_company(article: CleanArticle) -> tuple[str, tuple[str, ...]]:
    structured = canonical_company_name(
        str(article.index.structured_data.get("company") or "").strip()
    )
    if _valid_company(structured):
        return structured, (structured,)
    title = article.index.title
    prefix = _PREFIX.search(title)
    if prefix and _valid_company(prefix.group(1).strip()):
        company = prefix.group(1).strip()
        return company, (company,)
    for match in reversed(list(_QUOTED.finditer(title))):
        company = match.group(1).strip()
        if _valid_company(company):
            return company, (company,)
    return "", ()


def _company_for_event(
    article: CleanArticle,
    sentence: str,
    assertion: re.Match[str],
    last_company: str,
    primary: str,
    primary_mentions: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    if primary and primary in sentence:
        return primary, primary_mentions
    prefix = sentence[: assertion.start()]
    quoted = [
        match.group(1).strip()
        for match in _QUOTED.finditer(prefix)
        if _valid_company(match.group(1).strip())
    ]
    if quoted:
        return quoted[-1], (quoted[-1],)
    legal = list(_LEGAL.finditer(prefix))
    if legal:
        company = legal[-1].group(1)
        return company, (company,)
    segment = re.split(r"[。！？；：:,]", prefix)[-1].strip()
    segment = re.sub(
        r"^(?:来源：?[\u4e00-\u9fffA-Za-z0-9]+|"
        r"36氪获悉|据悉|近日|日前|今年|"
        r"\d{4}年|\d{1,2}月\d{1,2}日)\s*",
        "",
        segment,
    )
    segment = re.sub(r"(?:已|正式|成功|宣布|官宣)\s*$", "", segment).strip()
    segment = canonical_company_name(segment)
    if _valid_company(segment):
        return segment, (segment,)
    if last_company:
        return last_company, (last_company,)
    return "", ()


def _is_historical_assertion(
    sentence: str,
    assertion: re.Match[str],
    published_at: str,
) -> bool:
    prefix = sentence[max(0, assertion.start() - 90) : assertion.start()]
    local = sentence[
        max(0, assertion.start() - 50) : min(len(sentence), assertion.end() + 40)
    ]
    if _HISTORICAL.search(prefix):
        return True
    if re.search(
        r"\u56de\u987e|\u8ffd\u6eaf|\u5f53\u65f6|"
        r"\u4ec5\u95f4\u9694.{0,12}(?:\u4e2a\u6708|\u5929)|"
        r"\u65f6\u9694.{0,12}(?:\u4e2a\u6708|\u5929)",
        local,
    ):
        return True
    try:
        published = datetime.fromisoformat(published_at[:10]).date()
    except ValueError:
        return False
    explicit_years = [
        int(value)
        for value in re.findall(r"(20\d{2})\u5e74", prefix)
    ]
    if explicit_years and explicit_years[-1] < published.year:
        return True
    dated = list(
        re.finditer(
            r"(?P<month>\d{1,2})\u6708"
            r"(?:(?P<day>\d{1,2})\u65e5|"
            r"(?P<period>\u521d|\u4e2d\u65ec|\u5e95))?",
            prefix,
        )
    )
    if not dated:
        return False
    marker = dated[-1]
    month = int(marker.group("month"))
    day_text = marker.group("day")
    if month != published.month:
        return True
    if day_text:
        return int(day_text) < published.day - 1
    period = marker.group("period")
    if period == "\u521d":
        return published.day > 7
    if period == "\u4e2d\u65ec":
        return published.day > 20
    return False


def _round_for_assertion(
    sentence: str,
    assertion: re.Match[str],
    status: str,
) -> str:
    matches = list(_ROUND.finditer(sentence))
    if not matches:
        return ""
    if status == "started":
        within = [
            item
            for item in matches
            if assertion.start() <= item.start() <= assertion.end()
        ]
        if within:
            return within[-1].group(1)
    midpoint = (assertion.start() + assertion.end()) / 2
    return min(
        matches,
        key=lambda item: abs(((item.start() + item.end()) / 2) - midpoint),
    ).group(1)


def _amounts_for_assertion(
    sentence: str,
    assertion: re.Match[str],
) -> tuple[str, str]:
    current: list[tuple[float, str]] = []
    cumulative: list[tuple[float, str]] = []
    midpoint = (assertion.start() + assertion.end()) / 2
    for match in _AMOUNT.finditer(sentence):
        before = sentence[max(0, match.start() - 18) : match.start()]
        after = sentence[match.end() : match.end() + 12]
        if (
            re.search(r"(?:估值|投前|投后).{0,6}$", before)
            or re.match(r"(?:的)?(?:投前|投后)?估值", after)
            or re.match(r".{0,4}(?:订单|营收|利润|交付|小时)", after)
        ):
            continue
        total_funding_context = bool(
            re.search(
                r"\u603b\u878d\u8d44(?:\u89c4\u6a21|\u989d|\u91d1\u989d)?",
                before,
            )
        )
        target = (
            cumulative
            if total_funding_context or re.search(
                r"累计(?:融资)?(?:额|金额)?|(?:\d+轮|多轮).{0,16}$",
                before,
            )
            else current
        )
        distance = abs(((match.start() + match.end()) / 2) - midpoint)
        target.append((distance, match.group(1)))
    return (
        min(current)[1] if current else "",
        min(cumulative)[1] if cumulative else "",
    )


def _resolve_conflicts(
    candidates: dict[
        tuple[str, str, str],
        tuple[SemanticEvent, int],
    ],
) -> dict[tuple[str, str, str], tuple[SemanticEvent, int]]:
    resolved = dict(candidates)
    for key, (_, priority) in tuple(candidates.items()):
        company, round_name, status = key
        opposite = (
            company,
            round_name,
            "started" if status == "completed" else "completed",
        )
        other = resolved.get(opposite)
        if other and other[1] > priority:
            resolved.pop(key, None)
            continue
        if not round_name and any(
            candidate_company == company
            and candidate_status == status
            and candidate_round
            for candidate_company, candidate_round, candidate_status in resolved
        ):
            resolved.pop(key, None)
    return resolved


def _quality(value: tuple[SemanticEvent, int]) -> tuple[int, int, int, int]:
    event, priority = value
    return (
        priority,
        int(bool(event.funding_amount)),
        int(bool(event.cumulative_funding_amount)),
        len(event.evidence_quotes[0]) if event.evidence_quotes else 0,
    )


def _sentences(text: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"(?<=[。！？；])", text)
        if item.strip()
    ]


def _valid_company(candidate: str) -> bool:
    if (
        not is_company_like(candidate)
        or _GENERIC_COMPANY.search(candidate)
    ):
        return False
    if candidate.startswith("\u539f\u5b9a"):
        return False
    if re.search(
        r"(?:\u4e0b\u4e00\u8f6e|\u4e0a\u4e00\u8f6e|"
        r"\u672c\u8f6e|\u65b0\u4e00\u8f6e)$",
        candidate,
    ):
        return False
    return not re.search(
        r"(?:估值|三个月内|数月内|半年内|一年内|"
        r"连续|累计|融资后|领投|参投)",
        candidate,
    )


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


__all__ = [
    "FundingRuleConfig",
    "extract_funding_events",
]
