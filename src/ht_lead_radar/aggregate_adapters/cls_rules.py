"""Conservative supplemental rules for the high-frequency CLS telegraph."""

from __future__ import annotations

from dataclasses import replace
import re

from .entities import canonical_company_name, is_company_like
from .models import CleanArticle, SemanticEvent, SourceChannel


_LEGAL_SUFFIX = r"(?:\u80a1\u4efd\u6709\u9650\u516c\u53f8|\u6709\u9650\u8d23\u4efb\u516c\u53f8|\u6709\u9650\u516c\u53f8)"
_LEGAL = re.compile(rf"([A-Za-z0-9\u4e00-\u9fff\u00b7\uff08\uff09()\-]{{2,60}}?{_LEGAL_SUFFIX})")
_ATTRIBUTION = re.compile(
    r"^(?:\u3010[^\u3011]{2,100}\u3011|\u8d22\u8054\u793e\d{1,2}\u6708\d{1,2}\u65e5\u7535[\uff0c,:]?|"
    r"\u300a\u79d1\u521b\u677f\u65e5\u62a5\u300b\d{1,2}\u65e5\u8baf[\uff0c,:]?|"
    r"\u636e\u6089[\uff0c,:]?|\u77e5\u60c5\u4eba\u58eb\u79f0[\uff0c,:]?|"
    r"\u5229\u5f17\u83ab\u5c14\u8bc1\u5238\u663e\u793a[\uff0c,:]?)"
)
_NON_COMPANY = re.compile(
    r"\u663e\u793a|\u63d0\u4ea4|\u9012\u8868|\u5728\u6e2f\u4ea4\u6240|"
    r"\u5168\u9762\u63a8\u8fdb|\u770b\u9f99\u5934|\u795d\u8d3a|\u7b79\u5907|"
    r"\u57ce\u533a|\u8bd5\u70b9\u533a|\u7126\u70b9\u80a1|\u8be5\u9879\u76ee|"
    r"\u516c\u53f8\u8463\u4e8b\u4f1a|\u4e1a\u7ee9\u4eae\u773c"
)
_NEGATIVE = re.compile(
    r"\u64a4\u56de|\u7ec8\u6b62|\u53d6\u6d88|\u5426\u8ba4|\u8f9f\u8c23|"
    r"\u5c1a\u65e0|\u672a\u83b7\u6279|\u98ce\u9669\u8b66\u793a|"
    r"\u9000\u5e02|\u9020\u5047|\u5904\u7f5a"
)
_EDITORIAL_NOISE = re.compile(
    r"\u884c\u4e1a\u62a5\u544a|\u62a5\u544a\u4ec5\u8ba8\u8bba|"
    r"\u5e02\u573a\u89c4\u6a21|\u8ba2\u5355\u91d1\u989d\u7edf\u8ba1"
)
_PUBLIC_HOUSING = re.compile(r"\u4fdd\u79df\u623f|\u6536\u8d2d\u4e8c\u624b\u623f|\u4e2d\u5fc3\u57ce\u533a|\u8bd5\u70b9\u533a")
_INVESTMENT_SITE = re.compile(
    r"\u7b7e\u7f72.{0,24}\u6295\u8d44\u5408\u4f5c\u534f\u8bae.*"
    r"(?:\u6295\u8d44|\u5efa\u8bbe).{0,40}(?:\u751f\u4ea7\u57fa\u5730|\u4ea7\u7ebf|\u5de5\u5382|\u9879\u76ee)"
)
_ROUND = re.compile(r"((?:Pre-?IPO|[A-H])\u8f6e)", re.I)
_AMOUNT = re.compile(r"((?:\u8d85|\u7ea6|\u8fd1)?\d+(?:\.\d+)?(?:\u4ebf|\u4e07)(?:\u7f8e\u5143|\u5143|\u4eba\u6c11\u5e01|\u65b0\u53f0\u5e01))")

_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("major_order", "completed", re.compile(
        r"\u6536\u5230.{0,80}\u91c7\u8d2d\u8ba2\u5355|"
        r"\u7b7e\u7f72.{0,80}(?:\u670d\u52a1)?\u5408\u540c|"
        r"\u7b7e\u8ba2.{0,48}(?:\u957f\u671f)?.{0,20}\u4f9b\u8d27\u534f\u8bae|"
        r"\u83b7\u5f97.{0,24}\u91cd\u5927\u9879\u76ee")),
    ("major_order", "target", re.compile(r"\u62df.{0,24}\u59d4\u6258.{0,36}\u5efa\u9020")),
    ("factory_or_capacity", "started", re.compile(
        r"\u6b63\u5f0f\u52a8\u5de5|"
        r"(?:\u8463\u4e8b\u4f1a)?.{0,12}\u5df2\u6279\u51c6.{0,48}\u6269\u4ea7\u8ba1\u5212|"
        r"(?:\u8463\u4e8b\u4f1a)?.{0,12}\u5df2\u6279\u51c6.{0,72}\u6295\u8d44\u8ba1\u5212.{0,48}(?:\u63d0\u5347|\u63d0\u9ad8|\u6269\u5927).{0,16}\u4ea7\u80fd")),
    ("factory_or_capacity", "target", re.compile(
        r"(?:\u5c06|\u8ba1\u5212)\u6295\u8d44.{0,100}\u5efa\u8bbe|"
        r"\u62df.{0,48}(?:\u6295\u8d44|\u52df\u96c6\u8d44\u91d1).{0,100}(?:\u5efa\u8bbe|\u751f\u4ea7\u57fa\u5730|\u751f\u4ea7\u7ebf|\u5236\u9020\u9879\u76ee)|"
        r"\u5c06.{0,48}\u65b0\u5efa.{0,24}(?:\u6676\u5706\u5382|\u751f\u4ea7\u7ebf|\u57fa\u5730)|"
        r"\u5c06\u4e8e.{0,40}\u6269\u5927\u751f\u4ea7|\u6269\u5145.{0,28}\u4ea7\u80fd|"
        r"\u91cf\u4ea7\u51fa\u8d27.{0,48}\u6269\u5927\u751f\u4ea7|"
        r"\u7528\u4e8e.{0,50}(?:\u667a\u80fd\u5236\u9020|\u751f\u4ea7).{0,30}(?:\u57fa\u5730|\u9879\u76ee)")),
    ("new_site_or_entity", "completed", re.compile(rf"{_LEGAL_SUFFIX}\u6210\u7acb.{{0,80}}\u7ecf\u8425\u8303\u56f4")),
    ("new_site_or_entity", "target", re.compile(r"(?:\u62df|\u5c06)?.{0,36}\u8bbe\u7acb.{0,30}\u63a7\u80a1\u5b50\u516c\u53f8")),
    ("regulatory_or_clinical", "completed", re.compile(
        r"(?:\u6536\u5230|\u53d6\u5f97).{0,110}(?:\u6ce8\u518c\u8bc1|\u53d7\u7406\u901a\u77e5\u4e66)|"
        r"\u7533\u8bf7\u83b7\u53d7\u7406|\u83b7\u5f97.{0,60}(?:\u6838\u51c6\u6279\u590d|\u6d4b\u8bd5\u8bb8\u53ef)")),
    ("policy_or_standard", "completed", re.compile(r"\u6279\u51c6\u53d1\u5e03.{0,110}\u56fd\u5bb6\u6807\u51c6")),
    ("technical_milestone", "completed", re.compile(r"\u6837\u54c1.{0,36}\u5df2\u4ea4\u4ed8|\u73b0\u5df2.{0,44}\u53d1\u5e03")),
    ("technical_milestone", "target", re.compile(
        r"\u9884\u8ba1.{0,36}\u91cf\u4ea7|\u5c06\u4e8e.{0,72}(?:\u53d1\u5e03|\u6b63\u5f0f\u63a8\u51fa)|"
        r"\u8ba1\u5212\u4e8e.{0,44}\u5b9e\u73b0.{0,24}\u91cf\u4ea7")),
    ("funding", "completed", re.compile(
        r"\u5df2\u5b8c\u6210.{0,40}(?:\u8f6e)?\u878d\u8d44|"
        r"\u65b0\u589e.{0,100}\u4e3a\u80a1\u4e1c.{0,100}\u6ce8\u518c\u8d44\u672c.{0,30}(?:\u589e\u81f3|\u589e\u52a0)")),
    ("funding", "target", re.compile(r"\u9884\u8ba1\u5c06\u5411.{2,48}\u63d0\u4f9b.{0,30}\u8d44\u91d1")),
    ("ipo_or_listing", "started", re.compile(r"\u5411\u6e2f\u4ea4\u6240\u63d0\u4ea4\u4e0a\u5e02\u7533\u8bf7\u4e66|\u5728\u6e2f\u4ea4\u6240\u63d0\u4ea4IPO\u7533\u8bf7")),
    ("ipo_or_listing", "target", re.compile(r"\u8003\u8651.{0,30}IPO")),
    ("merger_acquisition", "target", re.compile(r"(?:\u62df|\u6b63\u8003\u8651).{0,72}\u6536\u8d2d")),
    ("partnership", "completed", re.compile(r"\u4e0e.{2,44}\u5c55\u5f00\u5408\u4f5c")),
)

_PHASE = {
    "major_order": "scale_delivery", "factory_or_capacity": "build_organize",
    "new_site_or_entity": "build_organize", "regulatory_or_clinical": "scale_delivery",
    "policy_or_standard": "strategy_capital", "technical_milestone": "scale_delivery",
    "funding": "build_organize", "ipo_or_listing": "strategy_capital",
    "merger_acquisition": "strategy_capital", "partnership": "strategy_capital",
}


def _clean_candidate(value: str) -> str:
    value = _ATTRIBUTION.sub("", value).strip(" \uff0c,:\u3010\u3011")
    for marker in ("\u6240\u5c5e", "\u65d7\u4e0b", "\u5373", "\u5b50\u516c\u53f8"):
        if marker in value:
            value = value.rsplit(marker, 1)[-1]
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+\-]{1,60}", value):
        value = re.sub(r"\s+", " ", value).strip()
    else:
        value = canonical_company_name(value)
    return value if is_company_like(value) and not _NON_COMPANY.search(value) else ""


def cls_company_from_title(title: str) -> str:
    """Extract a conservative subject from a telegraph title."""

    value = _ATTRIBUTION.sub("", title).strip()
    legal = list(_LEGAL.finditer(value))
    if legal:
        candidate = _clean_candidate(legal[-1].group(1))
        if candidate:
            return candidate
    colon = re.match(r"([^\uff1a:]{2,36})[\uff1a:]", value)
    if colon:
        candidate = _clean_candidate(colon.group(1).split("\uff5c")[-1])
        if candidate:
            return candidate
    described = re.search(r"(?:\u516c\u53f8|\u5236\u9020\u5546)([A-Za-z][A-Za-z0-9 .&+\-]{1,40})(?=\u79f0|\u8003\u8651|\u62df|\u5c06|\u4e0e|$)", value)
    if described:
        return described.group(1).strip()
    action = re.search(
        r"\u6536\u5230|\u7b7e\u7f72|\u7b7e\u8ba2|\u83b7\u5f97|\u83b7|\u53d6\u5f97|\u5b8c\u6210|"
        r"\u6279\u51c6|\u62df|\u5c06|\u8ba1\u5212|\u9884\u8ba1|\u8003\u8651|\u73b0\u5df2|\u6210\u7acb|\u53d1\u5e03|\u6536\u8d2d|\u4e1a\u7ee9",
        value,
    )
    if action:
        candidate = _clean_candidate(value[: action.start()].split("\uff5c")[-1])
        if candidate:
            return candidate
    return ""


def _explicit_companies(sentence: str, match: re.Match[str], event_type: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    pair = re.search(r"\u5b50\u516c\u53f8([\u4e00-\u9fffA-Za-z0-9\u00b7]{2,18})\u548c([\u4e00-\u9fffA-Za-z0-9\u00b7]{2,18})\u62df\u5206\u522b", sentence)
    if pair:
        values = tuple(canonical_company_name(pair.group(i)) for i in (1, 2))
        return values, values
    if event_type == "partnership":
        partners = re.search(r"(?:\u65d7\u4e0b)?([^\uff0c\uff1b\u3002]{2,36}?)(?:\u4e0e|\u548c)([^\uff0c\uff1b\u3002]{2,28}?)\u5c55\u5f00\u5408\u4f5c", sentence)
        if partners:
            first = _clean_candidate(partners.group(1).split("\u65d7\u4e0b")[-1])
            second = _clean_candidate(partners.group(2)) or partners.group(2).strip()
            if first:
                return (first,), (first, second)
    recipient = re.search(r"\u9884\u8ba1\u5c06\u5411([^\uff0c\uff1b\u3002]{2,36})\u63d0\u4f9b", sentence)
    if recipient:
        value = _clean_candidate(recipient.group(1))
        if value:
            return (value,), (value,)
    nested = re.search(rf"(?:\u6240\u5c5e|\u5373)({_LEGAL.pattern})(?=\u6295\u8d44|\u5e74\u4ea7|\u4e8e|\u4e0e|\u6536\u5230|\u83b7\u5f97)", sentence)
    if nested:
        value = _clean_candidate(nested.group(1))
        if value:
            return (value,), (value,)
    legal = list(_LEGAL.finditer(sentence[: match.end() + 60]))
    if legal:
        value = _clean_candidate(legal[-1].group(1))
        if value:
            return (value,), (value,)
    named = re.search(
        r"([A-Za-z][A-Za-z0-9 .&+\-]{1,45}|[\u4e00-\u9fffA-Za-z0-9\u00b7]{2,24})"
        r"(?=\u79f0|\u8868\u793a|\u8d22\u62a5\u62ab\u9732|\u8fd1\u65e5\u83b7\u5f97|\u5df2\u5b8c\u6210|\u6b63\u8003\u8651|\u62df|\u6279\u51c6\u53d1\u5e03)",
        _ATTRIBUTION.sub("", sentence[: match.end() + 40]),
    )
    if named:
        value = _clean_candidate(named.group(1))
        if value:
            return (value,), (value,)
    return (), ()


def _companies_for_sentence(sentence: str, match: re.Match[str], title_company: str, last_company: str, event_type: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    companies, mentions = _explicit_companies(sentence, match, event_type)
    if companies:
        return companies, mentions
    fallback = title_company or last_company
    if is_company_like(fallback) and not _NON_COMPANY.search(fallback):
        return (fallback,), (fallback,)
    return (), ()


def _funding_facts(sentence: str, event_type: str) -> tuple[str, str]:
    if event_type != "funding":
        return "", ""
    round_match = _ROUND.search(sentence)
    amount_match = _AMOUNT.search(sentence)
    if round_match is None and "\u6ce8\u518c\u8d44\u672c" in sentence:
        amount_match = None
    return (
        round_match.group(1) if round_match else "",
        amount_match.group(1) if amount_match else "",
    )


def extract_cls_supplemental_events(channel: SourceChannel, article: CleanArticle) -> list[SemanticEvent]:
    """Extract explicit CLS actions missed by the cross-site baseline."""

    text = " ".join((article.index.title, article.index.summary, article.clean_body))
    structured = _clean_candidate(str(article.index.structured_data.get("company") or ""))
    title_company = structured or cls_company_from_title(article.index.title)
    output: dict[tuple[str, str, str], SemanticEvent] = {}
    last_company = title_company
    sentences = _sentences(text)
    for sentence_index, sentence in enumerate(sentences):
        if _NEGATIVE.search(sentence):
            continue
        for event_type, status, pattern in _PATTERNS:
            match = pattern.search(sentence)
            if not match:
                continue
            companies, mentions = _companies_for_sentence(sentence, match, title_company, last_company, event_type)
            for company in companies:
                if not is_company_like(company) or _NON_COMPANY.search(company):
                    continue
                last_company = company
                quote = _context_quote(sentences, sentence_index, sentence, company)[:500]
                funding_round, funding_amount = _funding_facts(sentence, event_type)
                event = SemanticEvent(
                    source_id=channel.source_id,
                    source_article_id=article.index.source_article_id,
                    canonical_url=article.index.canonical_url,
                    company_mentions=tuple(dict.fromkeys((company, *mentions))),
                    canonical_company=company,
                    event_type=event_type,
                    event_date=article.index.published_at[:10],
                    industry_tags=_industry_tags(text),
                    funding_round=funding_round,
                    funding_amount=funding_amount,
                    event_summary=quote[:300],
                    evidence_quotes=(quote,),
                    confidence="high",
                    processor="rules:cls-supplemental-v2",
                    content_hash=article.content_hash,
                    phase=_PHASE[event_type],
                    event_status=status,
                )
                output.setdefault((company, event_type, status), event)
    return list(output.values())


def merge_cls_events(baseline: list[SemanticEvent], supplemental: list[SemanticEvent]) -> list[SemanticEvent]:
    """Prefer explicit supplemental subjects and collapse action duplicates."""

    filtered = []
    for event in baseline:
        quote = event.evidence_quotes[0] if event.evidence_quotes else ""
        if (
            not is_company_like(event.canonical_company)
            or _NON_COMPANY.search(event.canonical_company)
            or _EDITORIAL_NOISE.search(quote)
        ):
            continue
        if event.event_type == "merger_acquisition" and _PUBLIC_HOUSING.search(quote):
            continue
        if event.event_type == "partnership" and _INVESTMENT_SITE.search(quote):
            continue
        if any(_same_action_quote(event, item) and event.event_type == item.event_type for item in supplemental):
            continue
        filtered.append(event)
    supplemental = [
        event
        for event in supplemental
        if not (
            event.event_type == "regulatory_or_clinical"
            and not any(event.canonical_company.endswith(suffix) for suffix in ("\u80a1\u4efd\u6709\u9650\u516c\u53f8", "\u6709\u9650\u8d23\u4efb\u516c\u53f8", "\u6709\u9650\u516c\u53f8"))
            and any(
                other.event_type == event.event_type
                and any(other.canonical_company.endswith(suffix) for suffix in ("\u80a1\u4efd\u6709\u9650\u516c\u53f8", "\u6709\u9650\u8d23\u4efb\u516c\u53f8", "\u6709\u9650\u516c\u53f8"))
                and _same_action_quote(event, other)
                for other in supplemental
            )
        )
    ]
    merged = [*filtered, *supplemental]
    output: dict[tuple[str, str, str], SemanticEvent] = {}
    for event in merged:
        key = (_clean_candidate(event.canonical_company), event.event_type, event.event_status)
        current = output.get(key)
        if current is None or event.processor.startswith("rules:cls-supplemental"):
            output[key] = replace(event, canonical_company=key[0])
    return list(output.values())


def _context_quote(
    sentences: list[str],
    sentence_index: int,
    sentence: str,
    company: str,
) -> str:
    if company in sentence:
        return sentence
    for start in range(sentence_index - 1, max(-1, sentence_index - 4), -1):
        context = "".join(sentences[start : sentence_index + 1])
        if company in context:
            return context
    return sentence


def _same_action_quote(left: SemanticEvent, right: SemanticEvent) -> bool:
    left_quote = left.evidence_quotes[0] if left.evidence_quotes else ""
    right_quote = right.evidence_quotes[0] if right.evidence_quotes else ""
    return bool(left_quote and right_quote and (left_quote == right_quote or left_quote[:80] in right_quote or right_quote[:80] in left_quote))


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[\u3002\uff01\uff1f\uff1b])", re.sub(r"\s+", " ", text)) if item.strip()]


def _industry_tags(text: str) -> tuple[str, ...]:
    tags = []
    patterns = (
        ("semiconductor", r"\u82af\u7247|\u534a\u5bfc\u4f53|\u6676\u5706|HBM|NAND"),
        ("artificial_intelligence", r"\u4eba\u5de5\u667a\u80fd|AI\b|\u5927\u6a21\u578b|\u7b97\u529b"),
        ("embodied_intelligence", r"\u673a\u5668\u4eba|\u5177\u8eab\u667a\u80fd|\u7075\u5de7\u624b"),
        ("advanced_manufacturing", r"\u5236\u9020|\u4ea7\u80fd|\u5de5\u5382|\u4ea7\u7ebf|\u6750\u6599|\u6c7d\u8f66"),
        ("biotech", r"\u533b\u836f|\u533b\u7597|\u836f\u54c1|\u4e34\u5e8a|\u6ce8\u518c\u8bc1"),
        ("energy", r"\u65b0\u80fd\u6e90|\u7535\u529b|\u50a8\u80fd|\u5149\u4f0f|\u98ce\u7535"),
    )
    for tag, pattern in patterns:
        if re.search(pattern, text, re.I):
            tags.append(tag)
    return tuple(tags or ("other",))


__all__ = ["cls_company_from_title", "extract_cls_supplemental_events", "merge_cls_events"]