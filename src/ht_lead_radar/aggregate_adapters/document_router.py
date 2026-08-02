"""Deterministic document routing and immutable source-unit contracts."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
import re
from typing import Any

from .models import CleanArticle


DOCUMENT_TYPES = frozenset(
    {
        "single_company_flash",
        "multi_company_bulletin",
        "long_feature",
        "roadmap",
        "commentary",
    }
)

_COMMENTARY_TITLE = re.compile(r"(?:评论|观点|观察|专栏|解读|研判|复盘|透视)")
_TRANSACTION_OR_COMPANY_FEATURE_TITLE = re.compile(
    r"^(?:【[^】]{0,12}】)?(?:首发|对话|专访).{0,80}(?:公司|科技|资本|融资|创业)|"
    r"(?:硬氪|投资界).{0,16}首发"
)
_INDUSTRY_INSIGHT_TITLE = re.compile(r"^行业洞察(?:\||｜|：|:)")
_EXPLICIT_COMMENTARY_TAGS = frozenset({"行业研究", "政策解读", "产业观察"})
_PATENT_STUDY_TITLE = re.compile(
    r"(?:专利导航系列\s*\d+|产业.{0,24}专利(?:态势|概况))"
)
_EDITORIAL_REPRINT_PREFIX = "编者按：本文来自微信公众号"
_SECTIONED_LONG_FORM_EXCLUSION = re.compile(r"(?:通知|公告|招标|邀请函|议程)")
_NUMBERED_SECTION_HEADING = re.compile(
    r"(?m)(?:^|[\r\n])\s*(?:"
    r"[一二三四五六七八九十]{1,3}、|"
    r"[（(][一二三四五六七八九十]{1,3}[）)]|"
    r"(?:0?[1-9]|1\d|20)(?:[.、]|\s+(?=\S))"
    r")"
)
_DIGEST_SECTION_LABEL = (
    r"(?:\u5927\u516c\u53f8|\u65b0\u4ea7\u54c1|\u6295\u878d\u8d44|\u4eca\u65e5\u89c2\u70b9|"
    r"\u5176\u4ed6\u503c\u5f97\u5173\u6ce8\u7684\u65b0\u95fb|\u878d\u8d44\u4e8b\u4ef6|"
    r"\u4ea7\u4e1a\u52a8\u6001|\u884c\u4e1a\u52a8\u6001|\u79d1\u6280\u65b0\u95fb|\u878d\u8d44\u5feb\u62a5)"
)
_DIGEST_SECTION = re.compile(
    rf"(?:^|(?<=[\s\u3002\uff1b;])){_DIGEST_SECTION_LABEL}\s*[:\uff1a]"
)
_COMMENTARY_BODY_CUES = (
    "业内人士",
    "分析认为",
    "我们认为",
    "在笔者看来",
    "值得注意",
    "这意味着",
    "不难发现",
    "为何",
    "如何",
)
_FUTURE_ACTION = re.compile(
    r"(?:计划|拟(?:于|在|将)?|预计|目标|未来将|将于|将在|力争|有望|"
    r"到20(?:2[7-9]|3\d)年)"
)

# Article-shape cues used by the pre-LLM route gate. They are deliberately
# broad discovery cues; event truth remains the responsibility of the semantic
# claim ledger and MiniMax adjudication.
_FUNDING_CUE = re.compile(
    r"(?:\u878d\u8d44|\u6295\u8d44|\u52df\u8d44|\u79cd\u5b50\u8f6e|"
    r"A\u8f6e|B\u8f6e|C\u8f6e|Pre[- ]?IPO|IPO|\u4e0a\u5e02|\u5e76\u8d2d|"
    r"\u5e76\u8d44|\u8f6e\u878d\u8d44)",
    re.IGNORECASE,
)
_TRANSACTION_TITLE = re.compile(
    r"(?:\u878d\u8d44|\u5929\u4f7f\u8f6e|\u79cd\u5b50\u8f6e|A\u8f6e|B\u8f6e|C\u8f6e|"
    r"Pre[- ]?IPO|IPO|\u4e0a\u5e02|\u5e76\u8d2d|\u6536\u8d2d|\u83b7\u6295|\u6295\u540e\u4f30\u503c)",
    re.IGNORECASE,
)
_FUNDING_TITLE = re.compile(
    r"(?:\u5b8c\u6210|\u83b7\u5f97|\u83b7|\u5b98\u5ba3|\u5ba3\u5e03|"
    r"\u5ba3\u5e03\u5b8c\u6210|\u5f00\u59cb).{0,36}"
    r"(?:\u878d\u8d44|\u6295\u8d44|\u79cd\u5b50\u8f6e|A\u8f6e|B\u8f6e|C\u8f6e|"
    r"Pre[- ]?IPO|IPO|\u4e0a\u5e02|\u5e76\u8d2d)",
    re.IGNORECASE,
)
_INSTITUTION_CUE = re.compile(
    r"(?:\u57fa\u91d1|\u521b\u6295|\u79c1\u52df|\u6295\u8d44\u673a\u6784|"
    r"\u6295\u878d\u8d44\u6d3b\u52a8|\u8d44\u672c\u7ba1\u7406|(?<![A-Za-z])LP(?![A-Za-z])|"
    r"(?<![A-Za-z])GP(?![A-Za-z])|\u5408\u4f19\u4eba|\u6295\u8d44\u4eba)",
    re.IGNORECASE,
)
_POLICY_CUE = re.compile(
    r"(?:\u6807\u51c6|\u6307\u5357|\u7533\u62a5|"
    r"\u9074\u9009|\u63ed\u699c|\u540d\u5355|\u653f\u7b56|\u76d1\u7ba1|"
    r"\u5de5\u4fe1\u90e8|\u529e\u516c\u5385|\u90e8\u95e8\u53d1\u5e03)",
)
_INTERVIEW_CUE = re.compile(
    r"(?:\u91c7\u8bbf|\u4e13\u8bbf|\u5bf9\u8bdd|\u8bbf\u8c08|\u95ee\u7b54|"
    r"\u53d7\u8bbf|\u8bb0\u8005\u95ee|\u7b54\uff1a|\u95ee\uff1a)",
)
_MARKET_CUE = re.compile(
    r"(?:\u884c\u4e1a\u62a5\u544a|\u5e02\u573a\u6570\u636e|\u6307\u6570|"
    r"\u884c\u60c5|\u9884\u671f|\u8bc4\u4f30|\u8d22\u62a5|\u9500\u552e\u589e\u957f|"
    r"\u88c1\u5458|\u98ce\u9669\u6570\u636e|\u5408\u89c4|\u6210\u672c|"
    r"\u4e2a\u8d37|\u8d37\u6b3e|\u5229\u7387)",
)
_OPERATING_ACTION_CUE = re.compile(
    r"(?:\u5ba3\u5e03|\u53d1\u5e03|\u5b8c\u6210|\u83b7\u5f97|\u63a8\u51fa|\u4e0a\u7ebf|"
    r"\u6269\u5efa|\u6269\u4ea7|\u8ba2\u5355|\u4efb\u547d|\u7b7e\u7f72|\u91cf\u4ea7|"
    r"\u4ea4\u4ed8|\u6210\u7acb|\u5e76\u8d2d|\u56de\u8d2d)",
)
_COMPOUND_CUE = re.compile(
    r"(?:\u516c\u544a\u7cbe\u9009|\u516c\u544a\u96c6\u9526|\u4eca\u65e5\u516c\u544a|"
    r"\u591a\u5bb6\u516c\u53f8|\u7535\u62a5|\u5feb\u8baf\u5408\u96c6)",
)



@dataclass(frozen=True)
class DocumentUnit:
    unit_id: str
    char_start: int
    char_end: int
    text: str
    boundary_source: str


ROUTE_FAMILIES = frozenset(
    {
        "single_company_flash",
        "single_company_funding",
        "multi_company_bulletin",
        "multi_company_funding_digest",
        "compound_company_bulletin",
        "long_feature",
        "interview_commentary",
        "policy_market",
        "institutional_funding",
        "commentary",
        "roadmap",
    }
)


@dataclass(frozen=True)
class DocumentRoute:
    # Legacy structural type retained for semantic/action-ledger compatibility.
    document_type: str
    reason: str
    units: tuple[DocumentUnit, ...]
    # Two-layer pre-LLM gate output. These fields are descriptive and do not
    # alter the immutable source units or legacy downstream behavior.
    document_family: str = "single_company_flash"
    processing_mode: str = "single_unit"
    gate_confidence: str = "medium"
    llm_gate_required: bool = False
    gate_signals: tuple[str, ...] = ()


def _metadata(article: CleanArticle) -> dict[str, Any]:
    return {
        **dict(article.index.structured_data or {}),
        **dict(article.structured_data or {}),
    }


def _unit(
    body: str,
    start: int,
    end: int,
    *,
    boundary_source: str,
) -> DocumentUnit:
    material = f"{start}\0{end}\0{body[start:end]}".encode("utf-8")
    return DocumentUnit(
        unit_id=f"u_{sha1(material).hexdigest()[:12]}",
        char_start=start,
        char_end=end,
        text=body[start:end],
        boundary_source=boundary_source,
    )


def _adapter_boundaries(article: CleanArticle) -> list[tuple[int, int]]:
    body = article.clean_body
    raw_boundaries = _metadata(article).get("item_boundaries") or []
    if not isinstance(raw_boundaries, list):
        return []
    output: list[tuple[int, int]] = []
    cursor = 0
    for raw in raw_boundaries:
        if not isinstance(raw, dict):
            return []
        try:
            start = int(raw["char_start"])
            end = int(raw["char_end"])
        except (KeyError, TypeError, ValueError):
            text = str(raw.get("text") or "")
            if not text:
                return []
            start = body.find(text, cursor)
            end = start + len(text)
        if start < cursor or end <= start or end > len(body):
            return []
        output.append((start, end))
        cursor = end
    return output


def _complete_boundaries(
    body: str,
    boundaries: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    if not boundaries:
        return []
    output: list[tuple[int, int]] = []
    cursor = 0
    for start, end in boundaries:
        if start > cursor:
            output.append((cursor, start))
        output.append((start, end))
        cursor = end
    if cursor < len(body):
        output.append((cursor, len(body)))
    return [(start, end) for start, end in output if body[start:end].strip()]


def _heading_boundaries(body: str) -> list[tuple[int, int]]:
    starts: set[int] = set()
    for pattern in (
        r"(?m)^(?P<heading>\s*(?:\d{1,2}[.、]|[一二三四五六七八九十]+[.、]))",
        r"(?m)^(?P<heading>\s*【[^】]{2,50}】)",
        # Some adapters preserve list headings but normalize newlines to spaces.
        # Keep the heading offset (not the preceding separator) so the completed
        # units still concatenate to the immutable body byte-for-byte.
        r"(?:^|[\s。；）])(?P<heading>(?:\d{1,2}[.、]|[一二三四五六七八九十]+[.、]))"
        r"(?=.{0,80}(?:公司|基金|企业))",
    ):
        for match in re.finditer(pattern, body):
            starts.add(match.start("heading"))
    if len(starts) < 2:
        return []
    ordered = sorted({0, *starts, len(body)})
    return [
        (left, right)
        for left, right in zip(ordered, ordered[1:], strict=False)
        if body[left:right].strip()
    ]


def _digest_boundaries(body: str) -> list[tuple[int, int]]:
    """Split a flattened digest at stable section labels.

    Some aggregate pages expose a long daily bulletin as one text node instead
    of preserving ``h2``/``p`` boundaries. The labels are editorial structure,
    not semantic guesses, so they are safe deterministic cut points.
    """

    starts = {match.start() for match in _DIGEST_SECTION.finditer(body)}
    if len(starts) < 2:
        return []
    ordered = sorted({0, *starts, len(body)})
    return [
        (left, right)
        for left, right in zip(ordered, ordered[1:], strict=False)
        if body[left:right].strip()
    ]


def _sentence_boundaries(body: str, max_chars: int) -> list[tuple[int, int]]:
    sentence_ends = [
        match.end() for match in re.finditer(r"[。！？；;](?:\s+|$)", body)
    ]
    raw = list(zip([0, *sentence_ends], [*sentence_ends, len(body)], strict=False))
    raw = [(start, end) for start, end in raw if end > start]
    if not raw:
        raw = [(0, len(body))]
    output: list[tuple[int, int]] = []
    current_start: int | None = None
    current_end = 0
    for start, end in raw:
        if end - start > max_chars:
            if current_start is not None:
                output.append((current_start, current_end))
                current_start = None
            for piece_start in range(start, end, max_chars):
                output.append((piece_start, min(end, piece_start + max_chars)))
            current_end = end
            continue
        if current_start is None:
            current_start, current_end = start, end
            continue
        if end - current_start > max_chars:
            output.append((current_start, current_end))
            current_start, current_end = start, end
        else:
            current_end = end
    if current_start is not None:
        output.append((current_start, current_end))
    return output


def _future_action_sentence_count(body: str) -> int:
    return sum(
        bool(_FUTURE_ACTION.search(sentence))
        for sentence in re.split(r"[。！？；;]", body)
        if sentence.strip()
    )


def _company_heading_count(body: str) -> int:
    return sum(
        bool(re.search(r"(?:公司|基金|企业)", body[start : min(end, start + 120)]))
        for start, end in _heading_boundaries(body)
    )


def _metadata_tags(article: CleanArticle) -> set[str]:
    """Return exact human-authored tags without doing fuzzy keyword matching."""

    metadata = _metadata(article)
    output: set[str] = {str(value).strip() for value in article.tags if str(value).strip()}
    for key in ("tags", "tag", "categories", "category", "labels"):
        raw = metadata.get(key)
        values = raw if isinstance(raw, (list, tuple, set)) else [raw]
        output.update(str(value).strip() for value in values if value is not None)
    return output


def _numbered_section_count(body: str) -> int:
    return len(_NUMBERED_SECTION_HEADING.findall(body))


def _classify(article: CleanArticle, adapter_count: int) -> tuple[str, str]:
    body = article.clean_body
    title = article.index.title
    metadata = _metadata(article)
    source_id = str(article.index.source_id or "").lower()
    requested = str(metadata.get("document_type") or "").strip()
    if (
        source_id in {"stcn-flash", "cls-telegraph"}
        and str(metadata.get("company") or "").strip()
        and not _COMPOUND_CUE.search(title)
    ):
        return "single_company_flash", "adapter_company_field"
    if requested in DOCUMENT_TYPES and requested != "single_company_flash":
        return requested, "adapter_document_type"
    if adapter_count > 1:
        return "multi_company_bulletin", "adapter_item_boundaries"
    if len(_DIGEST_SECTION.findall(body)) >= 2:
        return "multi_company_bulletin", "body_digest_structure"
    if requested in DOCUMENT_TYPES:
        return requested, "adapter_document_type"
    heading_count = len(_heading_boundaries(body))
    if (
        re.search(
            r"(?:盘点|汇总|周报|日报|融资快报|投融资一览|"
            r"公告精选|盘前要闻一览|晚间公告|早间公告|要闻一览|"
            r"多家公司|(?:新增|新登记)\s*\d+\s*家)",
            title,
        )
        or title.count("；") >= 2
        or (heading_count > 1 and _company_heading_count(body) > 1)
    ):
        return "multi_company_bulletin", "bulletin_structure"
    if (
        _EXPLICIT_COMMENTARY_TAGS.intersection(_metadata_tags(article))
        or _INDUSTRY_INSIGHT_TITLE.search(title)
    ):
        return "commentary", "explicit_industry_research"
    if re.search(r"(?:路线图|发展规划|未来规划|三年规划|战略目标)", title):
        return "roadmap", "roadmap_title"
    if _COMMENTARY_TITLE.search(title):
        return "commentary", "commentary_title"
    # A financing/company profile often contains several future-use-of-funds
    # sentences.  Future-action density does not make it a roadmap, and
    # generic editorial phrases do not make it commentary.  Route these
    # articles as features before applying the body-level heuristics so their
    # current operating facts remain eligible for claim discovery.
    if _TRANSACTION_OR_COMPANY_FEATURE_TITLE.search(title):
        return "long_feature", "transaction_or_company_feature_title"
    if len(body) >= 1200 and sum(cue in body for cue in _COMMENTARY_BODY_CUES) >= 2:
        return "commentary", "commentary_discourse"
    if len(body) >= 500 and _future_action_sentence_count(body) >= 3:
        return "roadmap", "future_action_density"
    if _PATENT_STUDY_TITLE.search(title):
        return "long_feature", "patent_study_title"
    if len(body) >= 1800 and body.lstrip().startswith(_EDITORIAL_REPRINT_PREFIX):
        return "long_feature", "editorial_reprint"
    if (
        len(body) >= 1500
        and not _SECTIONED_LONG_FORM_EXCLUSION.search(title)
        and _numbered_section_count(body) >= 3
    ):
        return "long_feature", "sectioned_long_form"
    if len(body) > 3000 or re.search(r"(?:深度|专访|特写|长文|对话)", title):
        return "long_feature", "feature_length_or_title"
    if len(body) >= 2000:
        return "long_feature", "long_body"
    return "single_company_flash", "default_single_article"


def _route_profile(
    article: CleanArticle,
    *,
    document_type: str,
    adapter_count: int,
) -> tuple[str, str, str, bool, tuple[str, ...]]:
    """Return the explicit two-layer pre-LLM route profile.

    ``document_type`` remains the structural compatibility field used by the
    existing ledgers. ``document_family`` expresses what the article means for
    the lead-radar pipeline; ``processing_mode`` tells callers whether to split,
    prefix-window, skip, or process one unit. This gate never invents events.
    """

    source_id = str(article.index.source_id or "").lower()
    title = article.index.title
    metadata = _metadata(article)
    body_prefix = article.clean_body[:2400]
    metadata_company = str(metadata.get("company") or "").strip()
    metadata_mentions = metadata.get("company_mentions") or ()
    target_company = source_id != "miit-science-files" and bool(
        (metadata_company and len(metadata_company) <= 80)
        or any(str(value).strip() for value in metadata_mentions)
    )
    sample = f"{title}\n{body_prefix}"
    signals: list[str] = []
    has_adapter_units = adapter_count > 1
    has_funding = bool(_FUNDING_CUE.search(sample))
    explicit_funding_title = bool(_FUNDING_TITLE.search(title))
    title_transaction = bool(_TRANSACTION_TITLE.search(title))
    title_noise = bool(_MARKET_CUE.search(title)) and not target_company
    funding_count = len(_FUNDING_CUE.findall(body_prefix))
    has_institution = bool(_INSTITUTION_CUE.search(sample))
    has_policy = source_id == "miit-science-files" or bool(_POLICY_CUE.search(title))
    has_interview = bool(_INTERVIEW_CUE.search(sample))
    has_market = bool(_MARKET_CUE.search(title)) and not bool(
        _OPERATING_ACTION_CUE.search(title)
    )
    has_compound = bool(_COMPOUND_CUE.search(title)) or (
        source_id in {"stcn-flash", "cls-telegraph"}
        and (title.count("?") + title.count("\uff1f")) >= 1
        and not has_adapter_units
    )

    if has_adapter_units:
        signals.append("adapter_item_boundaries")
    if target_company:
        signals.append("adapter_company_field")
    if has_funding:
        signals.append("funding_cue")
    if explicit_funding_title:
        signals.append("explicit_funding_title")
    elif title_transaction and not title_noise:
        signals.append("transaction_title")
    if has_institution:
        signals.append("institution_cue")
    if has_policy:
        signals.append("policy_cue")
    if has_interview:
        signals.append("interview_cue")
    if has_market:
        signals.append("market_cue")
    if has_compound:
        signals.append("compound_cue")
    if len(article.clean_body) >= 2000:
        signals.append("long_body")

    # Compound exchange/newswire pages must be separated before the generic
    # policy cue (for example, ???? contains the word ?? but is not a
    # single policy document).
    if has_compound and source_id in {"stcn-flash", "cls-telegraph"}:
        family = "compound_company_bulletin"
        mode = "split_atomic_claims"
        confidence = "medium"
        llm = True
    # Official policy/standard feeds are not company-news feeds by default.
    elif has_policy:
        family = "policy_market"
        mode = "policy_rules_then_company_override"
        confidence = "high" if source_id == "miit-science-files" else "medium"
        llm = bool(has_adapter_units and has_funding)
    elif (
        document_type == "long_feature"
        and has_funding
        and target_company
        and not has_interview
        and (
            (explicit_funding_title or title_transaction)
            and not title_noise
        )
    ):
        family = "single_company_funding"
        mode = "prefix_2000_if_event_else_skip"
        confidence = "high" if explicit_funding_title else "medium"
        llm = True
    elif document_type == "long_feature":
        if has_interview:
            family = "interview_commentary"
            mode = "prefix_2000_if_event_else_skip"
        else:
            family = "long_feature"
            mode = "prefix_2000_if_event_else_skip"
        confidence = "high" if has_interview else "medium"
        llm = True
    elif document_type == "commentary":
        if has_interview:
            family = "interview_commentary"
            mode = "prefix_2000_if_event_else_skip"
            confidence = "high"
            llm = True
        elif len(article.clean_body) >= 2000:
            family = "long_feature"
            mode = "prefix_2000_if_event_else_skip"
            confidence = "medium"
            llm = True
        else:
            family = "commentary"
            mode = "commentary_review"
            confidence = "medium"
            llm = False
    elif document_type == "roadmap":
        family = "long_feature"
        mode = "future_action_review"
        confidence = "high"
        llm = False
    elif title_noise and not target_company:
        family = "policy_market"
        mode = "market_rules_then_company_override"
        confidence = "medium"
        llm = False
    elif has_market and source_id in {"stcn-flash", "cls-telegraph"}:
        family = "policy_market"
        mode = "market_rules_then_company_override"
        confidence = "medium"
        llm = False
    elif document_type == "multi_company_bulletin":
        if (
            has_funding
            and funding_count >= 2
            and source_id not in {"cls-telegraph", "stcn-flash"}
        ):
            family = "multi_company_funding_digest"
            mode = "split_units"
            confidence = "high" if has_adapter_units else "medium"
            llm = True
        elif has_compound:
            family = "compound_company_bulletin"
            mode = "split_atomic_claims"
            confidence = "medium"
            llm = True
        else:
            family = "multi_company_bulletin"
            mode = "split_units"
            confidence = "high" if has_adapter_units else "medium"
            llm = not has_adapter_units
    elif has_funding and (
        (explicit_funding_title or (title_transaction and not title_noise))
        or (target_company and source_id in {
            "cyzone-financing",
            "vbdata-funding",
            "zhidx-financing",
            "lieyunpro-archives",
        })
    ):
        if (
            has_institution
            and not explicit_funding_title
            and not target_company
            and source_id.startswith("pedaily")
        ):
            family = "institutional_funding"
            mode = "institution_or_target_review"
            confidence = "medium"
            llm = True
        else:
            family = "single_company_funding"
            mode = "single_unit"
            confidence = "high" if explicit_funding_title else "medium"
            llm = False
    elif (
        has_institution
        and source_id.startswith("pedaily")
        and not target_company
    ):
        family = "institutional_funding"
        mode = "institution_or_target_review"
        confidence = "medium"
        llm = True
    else:
        family = "single_company_flash"
        mode = "single_unit"
        confidence = "low" if not signals else "medium"
        llm = confidence == "low"

    return family, mode, confidence, llm, tuple(dict.fromkeys(signals))


def route_document(
    article: CleanArticle,
    *,
    max_unit_chars: int = 5000,
) -> DocumentRoute:
    """Route an article without changing or dropping any non-whitespace text."""

    body = article.clean_body
    if not body:
        return DocumentRoute(
            document_type="commentary",
            reason="empty_body",
            units=(),
            document_family="commentary",
            processing_mode="skip_empty",
            gate_confidence="high",
            llm_gate_required=False,
            gate_signals=("empty_body",),
        )
    adapter = _adapter_boundaries(article)
    document_type, reason = _classify(article, len(adapter))
    boundary_source = "whole_article"
    boundaries = _complete_boundaries(body, adapter)
    if boundaries:
        boundary_source = "adapter"
    elif document_type == "multi_company_bulletin":
        boundaries = _heading_boundaries(body)
        boundary_source = "deterministic_heading"
        if not boundaries:
            boundaries = _digest_boundaries(body)
            boundary_source = "deterministic_digest"
    if not boundaries:
        boundaries = (
            [(0, len(body))]
            if len(body) <= max_unit_chars
            else _sentence_boundaries(body, max_unit_chars)
        )
        if len(boundaries) > 1:
            boundary_source = "deterministic_sentence"
    expanded: list[tuple[int, int]] = []
    for start, end in boundaries:
        if end - start <= max_unit_chars:
            expanded.append((start, end))
        else:
            expanded.extend(
                (start + left, start + right)
                for left, right in _sentence_boundaries(
                    body[start:end],
                    max_unit_chars,
                )
            )
    units = tuple(
        _unit(body, start, end, boundary_source=boundary_source)
        for start, end in expanded
        if body[start:end].strip()
    )
    (
        document_family,
        processing_mode,
        gate_confidence,
        llm_gate_required,
        gate_signals,
    ) = _route_profile(
        article,
        document_type=document_type,
        adapter_count=len(adapter),
    )
    return DocumentRoute(
        document_type=document_type,
        reason=reason,
        units=units,
        document_family=document_family,
        processing_mode=processing_mode,
        gate_confidence=gate_confidence,
        llm_gate_required=llm_gate_required,
        gate_signals=gate_signals,
    )
