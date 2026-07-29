"""Conservative publication-date recovery from captured search result text."""

from __future__ import annotations

from datetime import datetime, timedelta
import re


_RELATIVE_PATTERNS = (
    (re.compile(r"(\d+)\s*(?:小时|hours?)\s*(?:前|ago)", re.I), "hours"),
    (re.compile(r"(\d+)\s*(?:天|日|days?)\s*(?:前|ago)", re.I), "days"),
    (re.compile(r"(\d+)\s*(?:周|星期|weeks?)\s*(?:前|ago)", re.I), "weeks"),
    (re.compile(r"(\d+)\s*(?:个?月|months?)\s*(?:前|ago)", re.I), "months"),
)


def relative_publication_date(
    text: str,
    *,
    captured_at: str,
) -> tuple[str, str]:
    """Return an estimated date and explicit basis, or two empty strings.

    Search-provider ``Crawled:`` metadata is removed first because it describes
    the crawler observation, not the job's publication age. Month offsets are
    deliberately approximated as 30 days and must not be represented as exact
    source dates downstream.
    """
    try:
        captured = datetime.fromisoformat(captured_at)
    except ValueError:
        return "", ""
    cleaned = re.sub(r"Crawled:[^;\n]+;?", " ", text, flags=re.I)
    for pattern, unit in _RELATIVE_PATTERNS:
        match = pattern.search(cleaned)
        if not match:
            continue
        amount = int(match.group(1))
        if unit == "hours":
            delta = timedelta(hours=amount)
        elif unit == "days":
            delta = timedelta(days=amount)
        elif unit == "weeks":
            delta = timedelta(days=7 * amount)
        else:
            delta = timedelta(days=30 * amount)
        return (captured - delta).date().isoformat(), f"relative_{unit}_estimate"
    return "", ""


__all__ = ["relative_publication_date"]
