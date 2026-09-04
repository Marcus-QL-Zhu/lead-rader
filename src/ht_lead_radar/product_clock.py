"""One product calendar for the China daily workflow."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


PRODUCT_TIMEZONE = ZoneInfo("Asia/Shanghai")


def product_date(now: datetime | None = None) -> date:
    """Return the Asia/Shanghai calendar day for an aware instant."""

    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("product clock must be timezone-aware")
    return moment.astimezone(PRODUCT_TIMEZONE).date()


def product_date_iso(now: datetime | None = None) -> str:
    return product_date(now).isoformat()


__all__ = ["PRODUCT_TIMEZONE", "product_date", "product_date_iso"]
