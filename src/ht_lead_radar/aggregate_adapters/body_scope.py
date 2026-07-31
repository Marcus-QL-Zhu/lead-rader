"""Conservative body scoping for deterministic semantic extraction."""

from __future__ import annotations

import re


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


__all__ = ["clean_semantic_body_scope"]
