"""Conservative company-name normalization shared by aggregate adapters."""

from __future__ import annotations

import re


LEGAL_SUFFIXES = (
    "\u80a1\u4efd\u6709\u9650\u516c\u53f8",
    "\u6709\u9650\u8d23\u4efb\u516c\u53f8",
    "\u6709\u9650\u516c\u53f8",
)


def canonical_company_name(value: str) -> str:
    """Remove an editorial descriptor while preserving an explicit legal name."""

    stripped = value.strip(" \uff0c\u3002\uff1b\uff1a:,\u3001")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+\-]{1,60}", stripped):
        return re.sub(r"\s+", " ", stripped)
    candidate = re.sub(r"\s+", "", value).strip(
        "\uff0c\u3002\uff1b\uff1a:,\u3001"
    )
    candidate = re.sub(
        r"^(?:20\d{2}\u5e74)?\d{1,2}\u6708"
        r"(?:\d{1,2}\u65e5)?[\uff0c,:]?",
        "",
        candidate,
    )
    ui_label = re.fullmatch(
        r"(?P<name>[A-Z][A-Z0-9-]{2,15})"
        r"(?:\u573a\u666f|\u610f\u5411\u8d5b\u961f|\u4e13\u5bb6)?"
        r"[\u00b7\u2022]?(?:\u626b\u7801)?"
        r"(?:\u62a5\u540d|\u5f81\u96c6)",
        candidate,
    )
    if ui_label:
        return ui_label.group("name")
    suffix = next(
        (item for item in LEGAL_SUFFIXES if candidate.endswith(item)),
        "",
    )
    if not suffix:
        return candidate
    markers = list(
        re.finditer(
            r"\u7684|"
            r"\u7814\u53d1\u5546|\u670d\u52a1\u5546|\u63d0\u4f9b\u5546|"
            r"\u89e3\u51b3\u65b9\u6848\u5546|\u8fd0\u8425\u5546|"
            r"\u5236\u9020\u5546|\u5f00\u53d1\u5546|\u521d\u521b\u516c\u53f8|"
            r"\u4f01\u4e1a",
            candidate[: -len(suffix)],
        )
    )
    for marker in reversed(markers):
        prefix = candidate[: marker.start()]
        tail = candidate[marker.end() :]
        marker_text = marker.group(0)
        descriptor = bool(
            marker_text == "\u7684"
            or marker_text != "\u4f01\u4e1a"
            or re.search(
                r"\u884c\u4e1a|\u6280\u672f|\u8def\u7ebf|\u521d\u521b|"
                r"\u805a\u7126|\u4e13\u6ce8|\u673a\u5668\u4eba|\u8de8\u5883|"
                r"\u53ef\u63a7|\u82af\u7247|\u533b\u7597|\u7814\u53d1|"
                r"\u5236\u9020|\u91d1\u878d\u79d1\u6280",
                prefix,
            )
        )
        if descriptor and len(tail) >= len(suffix) + 2:
            candidate = tail
            break
    return candidate


_NON_ENTITY_START = re.compile(
    r"^(?:\u9488\u5bf9|\u6b64\u5916|\u672c\u6b21|\u8be5|\u8fd9|"
    r"\u5176|\u56e0|\u636e|\u65e5\u524d|\u8fd1\u65e5|"
    r"\u77e5\u60c5\u4eba\u58eb|\u8bb0\u8005|\u7126\u70b9\u80a1|"
    r"\u6807\u7684\u516c\u53f8|\u4f9b\u5e94\u5546|\u7b2c\u4e8c\u6279|"
    r"\u6e2f\u80a1|\u5168\u8d44\u5b50\u516c\u53f8|\u8463\u4e8b\u4f1a|"
    r"\u516c\u53f8|\u4f01\u4e1a|\u9879\u76ee|\u8be5\u836f|"
    r"\u53c2\u8d5b|\u5404\u8d5b\u9053|\u51a0\u519b\u56e2\u961f)"
)
_NON_ENTITY_PREDICATE = re.compile(
    r"\u5b8c\u6210|\u83b7\u6279|\u7b7e\u8ba2|\u7b7e\u7f72|"
    r"\u4e2d\u6807|\u6536\u8d2d|\u5e76\u8d2d|\u53d1\u5e03|"
    r"\u63a8\u51fa|\u6269\u4ea7|\u6295\u4ea7|\u542f\u52a8|"
    r"\u7b79\u5907|\u795d\u8d3a|\u770b\u9f99\u5934|"
    r"\u516c\u544a|\u8868\u793a|\u5ba3\u5e03|\u8003\u8651|"
    r"\u65e8\u5728|\u8ba1\u5212|\u6b63\u5728|\u5c1a\u65e0|"
    r"\u4e3a\u516c\u53f8|\u73b0\u5df2|\u8fdb\u5165"
)
_NON_ENTITY_SLOGAN = re.compile(
    r"\u770b\u89c1\u672a\u6765|\u52b3\u52a8\u6700\u5149\u8363|"
    r"\u63ed\u699c\u6302\u5e05|\u83b7\u5956\u5373\u878d\u8d44|"
    r"\u53c2\u8d5b\u5373\u8def\u6f14"
)


def is_company_like(value: str) -> bool:
    """Fail closed on editorial fragments while retaining grounded names."""

    candidate = canonical_company_name(value)
    if not 2 <= len(candidate) <= 40:
        return False
    if _NON_ENTITY_START.search(candidate) or _NON_ENTITY_SLOGAN.search(candidate):
        return False
    if any(suffix in candidate for suffix in LEGAL_SUFFIXES):
        return True
    if re.search(r"[\u3002\uff01\uff1f\uff1b\uff0c,:]", candidate):
        return False
    if _NON_ENTITY_PREDICATE.search(candidate):
        return False
    if re.search(
        r"(?:\u65b9\u9762|\u5e02\u573a|\u884c\u4e1a|\u9879\u76ee|"
        r"\u4ea7\u54c1|\u673a\u5236|\u56e2\u961f|\u5e73\u53f0|\u8d5b\u9053)$",
        candidate,
    ):
        return False
    return bool(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9 .&+-]{1,39}", candidate)
        or re.search(
            r"\u79d1\u6280|\u667a\u80fd|\u96c6\u56e2|\u8d44\u672c|"
            r"\u673a\u5668\u4eba|\u534a\u5bfc\u4f53|\u7535\u5b50|"
            r"\u836f\u4e1a|\u533b\u7597|\u80a1\u4efd|\u80fd\u6e90|"
            r"\u6750\u6599|\u822a\u5929|\u6c7d\u8f66|\u7cfb\u7edf|"
            r"\u7814\u7a76\u6240|\u5927\u5b66$",
            candidate,
        )
        or (
            len(candidate) <= 12
            and bool(re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fff*+-]+", candidate))
        )
    )


def company_alias_candidates(value: str) -> tuple[str, ...]:
    """Return grounded brand-like variants for primary-quote validation."""

    canonical = canonical_company_name(value)
    candidates = [canonical]
    for suffix in LEGAL_SUFFIXES:
        if canonical.endswith(suffix):
            candidates.append(canonical[: -len(suffix)])
            break
    business_suffixes = (
        "\u79d1\u6280",
        "\u667a\u80fd",
        "\u96c6\u56e2",
        "\u8d44\u672c",
        "\u673a\u5668\u4eba",
        "\u534a\u5bfc\u4f53",
    )
    changed = True
    while changed:
        changed = False
        for candidate in tuple(candidates):
            for suffix in business_suffixes:
                if candidate.endswith(suffix) and len(candidate) > len(suffix):
                    shortened = candidate[: -len(suffix)]
                    if shortened not in candidates:
                        candidates.append(shortened)
                        changed = True
    for candidate in tuple(candidates):
        without_parenthetical = re.sub(
            r"[\uff08(][^\uff09)]{1,20}[\uff09)]",
            "",
            candidate,
        )
        if without_parenthetical and without_parenthetical not in candidates:
            candidates.append(without_parenthetical)
    changed = True
    while changed:
        changed = False
        for candidate in tuple(candidates):
            for suffix in business_suffixes:
                if candidate.endswith(suffix) and len(candidate) > len(suffix):
                    shortened = candidate[: -len(suffix)]
                    if shortened not in candidates:
                        candidates.append(shortened)
                        changed = True
    return tuple(dict.fromkeys(item for item in candidates if item))


__all__ = [
    "LEGAL_SUFFIXES",
    "canonical_company_name",
    "company_alias_candidates",
    "is_company_like",
]
