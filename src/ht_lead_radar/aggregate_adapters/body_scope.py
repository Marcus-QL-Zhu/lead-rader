"""Conservative body scoping for deterministic semantic extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass


_REFERENCE_LABEL = r"(?:延展阅读|相关阅读|推荐阅读)"
_PARENTHETICAL_REFERENCE = re.compile(
    rf"[（(]\s*{_REFERENCE_LABEL}\s*[：:]"
    r"[^（）()\r\n]{1,500}[）)]"
)
_REFERENCE_LINE = re.compile(
    rf"(?m)^[ \t]*{_REFERENCE_LABEL}\s*[：:]"
    r"[^\r\n]{1,500}(?:\r?\n|$)"
)
_FLATTENED_REFERENCE = re.compile(
    rf"{_REFERENCE_LABEL}\s*[\uff1a:]\s*"
    r"[^\u3002\uff01\uff1f\uff1b;\r\n]{1,300}"
    r"[\u3002\uff01\uff1f\uff1b;]"
)

# Long-form articles are not all the same document.  A 2,000-character
# semantic window is safe for a single event followed by commentary, but it is
# unsafe for a weekly digest containing several independent items.  These
# rules are intentionally conservative and only decide what the semantic
# extractor may see; the immutable source body and citation offsets remain
# unchanged.
LONG_ARTICLE_WINDOW_CHARS = 2_000
_CONCRETE_EVENT = re.compile(
    r"(?:完成(?:(?:[A-Z0-9]+)?轮融资|客户验证|投产|交付)|"
    r"获得(?:融资|订单|批复|认证|许可)|宣布|发布|推出|上线|开源|签署|签订|"
    r"达成(?:合作|协议|订单)|组建|成立|设立|新建|投建|扩产|量产|"
    r"任命|接任|离任|更换|落地|启用|招聘|完成部署)"
)
_ACTION_SIGNAL = re.compile(
    r"(?:融资|投产|交付|验证|订单|发布|推出|上线|开源|签署|签订|合作|"
    r"成立|设立|新建|扩产|量产|任命|接任|离任|更换|招聘|落地|启用)"
)
_BULLETIN_TITLE = re.compile(
    r"(?:周报|月报|日报|快报|周刊|盘点|汇总|一览|速递|榜单|"
    r"多家|新登|热点|融资事件)"
)
_INTERVIEW_TITLE = re.compile(r"(?:采访|专访|访谈|对话|问答|记者问)")
_INTERVIEW_CUE = re.compile(
    r"(?:采访|专访|访谈|记者(?:问|获悉|了解到)|问：|答：|Q\s*[:：]|A\s*[:：])"
)
_COMMENTARY_TITLE = re.compile(
    r"(?:评论|观点|讲话|书记|主任|政策|解读|论坛|倡议|新型工业化)"
)
_DIGEST_SECTION = re.compile(
    r"(?:^|[\s\u3002\uff1b;])(?:\u5927\u516c\u53f8|\u65b0\u4ea7\u54c1|\u6295\u878d\u8d44|\u4eca\u65e5\u89c2\u70b9|\u5176\u4ed6\u503c\u5f97\u5173\u6ce8\u7684\u65b0\u95fb|\u878d\u8d44\u4e8b\u4ef6|\u4ea7\u4e1a\u52a8\u6001|\u884c\u4e1a\u52a8\u6001|\u79d1\u6280\u65b0\u95fb|\u878d\u8d44\u5feb\u62a5)\s*[:\uff1a]"
)
_COMPANY_ACTION = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9 .&+*-]{1,40}|"
    r"[\u4e00-\u9fff]{2,16}(?:科技|信息|集团|公司|机器人|电子|汽车|半导体|能源|"
    r"实验室|生物|医疗|材料|智能))"
    r"[^。！？；\n]{0,72}"
    r"(?:融资|发布|推出|上线|开源|签署|签订|达成|成立|设立|新建|投建|扩产|量产|"
    r"任命|接任|离任|更换|招聘|交付|投产|完成验证)"
)


@dataclass(frozen=True)
class ArticleWindowDecision:
    """Deterministic decision for the semantic body presented to the model."""

    mode: str
    reason: str
    original_chars: int
    semantic_chars: int
    prefix_action_count: int
    tail_action_count: int
    prefix_has_concrete_event: bool
    interview_cue_count: int

    def to_dict(self) -> dict[str, int | str | bool]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "original_chars": self.original_chars,
            "semantic_chars": self.semantic_chars,
            "prefix_action_count": self.prefix_action_count,
            "tail_action_count": self.tail_action_count,
            "prefix_has_concrete_event": self.prefix_has_concrete_event,
            "interview_cue_count": self.interview_cue_count,
        }


def _mask_range(body: str, start: int, end: int) -> str:
    """Replace a range with spaces while preserving every source offset."""

    if start >= end:
        return body
    return body[:start] + "".join(
        character if character in {"\r", "\n"} else " "
        for character in body[start:end]
    ) + body[end:]




def _digest_section_count(body: str) -> int:
    """Count explicit section labels used by flattened news digests."""

    return len(_DIGEST_SECTION.findall(body))
def classify_long_article(
    body: str,
    *,
    title: str = "",
    document_type: str = "",
) -> ArticleWindowDecision:
    """Classify a long article before semantic extraction.

    ``single_event_expansion`` keeps only the first 2,000 characters because
    the article has a concrete event in its lead and is not a bulletin.  A
    digest is routed as ``multi_event_digest`` and is never blindly truncated.
    If the lead window contains no concrete event, the article is marked
    ``skip_low_value``; callers can retain it for audit while presenting only a
    blank, offset-preserving body to the semantic extractor.
    """

    length = len(body)
    prefix = body[:LONG_ARTICLE_WINDOW_CHARS]
    tail = body[LONG_ARTICLE_WINDOW_CHARS:]
    prefix_actions = len(_ACTION_SIGNAL.findall(prefix))
    tail_actions = len(_ACTION_SIGNAL.findall(tail))
    digest_sections = _digest_section_count(body)
    is_digest = (
        document_type == "multi_company_bulletin"
        or bool(_BULLETIN_TITLE.search(title))
        or digest_sections >= 2
    )
    if is_digest:
        return ArticleWindowDecision(
            mode="multi_event_digest",
            reason=(
                "body_digest_structure"
                if digest_sections >= 2 and document_type != "multi_company_bulletin"
                else "bulletin_or_digest_structure"
            ),
            original_chars=length,
            semantic_chars=length,
            prefix_action_count=prefix_actions,
            tail_action_count=tail_actions if length > LONG_ARTICLE_WINDOW_CHARS else 0,
            prefix_has_concrete_event=bool(_CONCRETE_EVENT.search(prefix)),
            interview_cue_count=len(_INTERVIEW_CUE.findall(prefix)),
        )
    if length <= LONG_ARTICLE_WINDOW_CHARS:
        return ArticleWindowDecision(
            mode="full_short",
            reason="under_window_limit",
            original_chars=length,
            semantic_chars=length,
            prefix_action_count=len(_ACTION_SIGNAL.findall(body)),
            tail_action_count=0,
            prefix_has_concrete_event=bool(_CONCRETE_EVENT.search(body)),
            interview_cue_count=len(_INTERVIEW_CUE.findall(body)),
        )

    prefix = body[:LONG_ARTICLE_WINDOW_CHARS]
    tail = body[LONG_ARTICLE_WINDOW_CHARS:]
    prefix_actions = len(_ACTION_SIGNAL.findall(prefix))
    tail_actions = len(_ACTION_SIGNAL.findall(tail))
    concrete = bool(_CONCRETE_EVENT.search(prefix))
    if _COMMENTARY_TITLE.search(title):
        # A policy/speech article may contain verbs such as “推进” or
        # “打造” without naming an operating company.  Do not treat that
        # rhetoric as a company hiring signal merely because the body is long.
        concrete = concrete and bool(_COMPANY_ACTION.search(prefix))
    interview_count = len(_INTERVIEW_CUE.findall(prefix))
    is_interview = bool(_INTERVIEW_TITLE.search(title)) or interview_count >= 2
    is_digest = document_type == "multi_company_bulletin" or bool(
        _BULLETIN_TITLE.search(title)
    )
    if is_digest:
        return ArticleWindowDecision(
            mode="multi_event_digest",
            reason="bulletin_or_digest_structure",
            original_chars=length,
            semantic_chars=length,
            prefix_action_count=prefix_actions,
            tail_action_count=tail_actions,
            prefix_has_concrete_event=concrete,
            interview_cue_count=interview_count,
        )
    if concrete:
        mode = "interview_prefix" if is_interview else "single_event_expansion"
        return ArticleWindowDecision(
            mode=mode,
            reason=(
                "interview_lead_contains_event"
                if is_interview
                else "concrete_lead_event_followed_by_long_form"
            ),
            original_chars=length,
            semantic_chars=LONG_ARTICLE_WINDOW_CHARS,
            prefix_action_count=prefix_actions,
            tail_action_count=tail_actions,
            prefix_has_concrete_event=True,
            interview_cue_count=interview_count,
        )
    return ArticleWindowDecision(
        mode="skip_low_value",
        reason=(
            "interview_or_commentary_without_concrete_lead_event"
            if is_interview
            else "no_concrete_event_in_lead_window"
        ),
        original_chars=length,
        semantic_chars=0,
        prefix_action_count=prefix_actions,
        tail_action_count=tail_actions,
        prefix_has_concrete_event=False,
        interview_cue_count=interview_count,
    )


def scope_long_article(
    body: str,
    *,
    title: str = "",
    document_type: str = "",
) -> tuple[str, ArticleWindowDecision]:
    """Return an offset-preserving semantic body and its routing decision."""

    decision = classify_long_article(
        body,
        title=title,
        document_type=document_type,
    )
    if decision.mode == "single_event_expansion" or decision.mode == "interview_prefix":
        return (
            _mask_range(body, LONG_ARTICLE_WINDOW_CHARS, len(body)),
            decision,
        )
    if decision.mode == "skip_low_value":
        return _mask_range(body, 0, len(body)), decision
    return body, decision


def clean_semantic_body_scope(body: str) -> str:
    """Remove explicit related-reading entries without dropping later prose.

    Publishers frequently insert a linked headline such as
    ``（延展阅读：……）`` inside an otherwise relevant paragraph. That headline
    is not an assertion made by the current article, so rule-based extraction
    must not treat it as one. Only self-contained parenthetical spans and
    dedicated lines are removed. A flattened inline recommendation is removed
    only through its first explicit sentence terminator, preserving later prose.
    """

    scoped = _PARENTHETICAL_REFERENCE.sub(" ", body)
    if "\n" in scoped or "\r" in scoped:
        scoped = _REFERENCE_LINE.sub(" ", scoped)
    scoped = _FLATTENED_REFERENCE.sub(" ", scoped)
    return re.sub(r"\s+", " ", scoped).strip()


def mask_semantic_body_scope(body: str) -> str:
    """Mask excluded references without changing source offsets or whitespace.

    Claim-centric extraction needs the same conservative scope as legacy prompts,
    but its citations are restored from immutable source offsets.  Replacing only
    excluded characters with spaces keeps every surviving character at its
    original index and leaves line boundaries intact.
    """

    def mask(match: re.Match[str]) -> str:
        return "".join(
            character if character in {"\r", "\n"} else " "
            for character in match.group(0)
        )

    scoped = _PARENTHETICAL_REFERENCE.sub(mask, body)
    if "\n" in scoped or "\r" in scoped:
        scoped = _REFERENCE_LINE.sub(mask, scoped)
    return _FLATTENED_REFERENCE.sub(mask, scoped)


__all__ = [
    "ArticleWindowDecision",
    "LONG_ARTICLE_WINDOW_CHARS",
    "classify_long_article",
    "clean_semantic_body_scope",
    "mask_semantic_body_scope",
    "scope_long_article",
]
