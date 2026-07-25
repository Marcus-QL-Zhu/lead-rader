"""Persistent accounting for scarce metered search calls."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


METASO_PROVIDER_ABSOLUTE_DAILY_LIMIT = 500
METASO_CONSERVATIVE_POINTS_PER_SEARCH = 6
METASO_BILLING_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _usage_day(usage_date: str | None) -> str:
    if usage_date:
        return usage_date
    return datetime.now(METASO_BILLING_TIMEZONE).date().isoformat()


def _effective_provider_limit(provider_limit: int) -> int:
    return min(
        max(int(provider_limit), 0),
        METASO_PROVIDER_ABSOLUTE_DAILY_LIMIT,
    )


def _effective_points(provider: str, points: int) -> int:
    value = int(points)
    if provider.casefold() == "metaso":
        return max(value, METASO_CONSERVATIVE_POINTS_PER_SEARCH)
    return value


@dataclass(frozen=True)
class BudgetStatus:
    provider: str
    usage_date: str
    spent_points: int
    reserved_points: int
    configured_limit: int
    provider_limit: int

    @property
    def available_points(self) -> int:
        return max(
            min(
                self.configured_limit,
                self.provider_limit,
                METASO_PROVIDER_ABSOLUTE_DAILY_LIMIT,
            )
            - self.spent_points
            - self.reserved_points,
            0,
        )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "usage_date": self.usage_date,
            "spent_points": self.spent_points,
            "reserved_points": self.reserved_points,
            "configured_limit": self.configured_limit,
            "provider_limit": self.provider_limit,
            "available_points": self.available_points,
        }


class SearchBudgetLedger:
    """A transactional daily ledger.

    Reservations are charged even when a request fails because providers often
    account for attempted searches and their exact billing response is not
    always available.  This intentionally errs on the side of preserving the
    user's quota.
    """

    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS search_budget_usage (
                    provider TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    operation_key TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider, usage_date, operation_key)
                );
                """
            )

    def status(
        self,
        *,
        provider: str = "metaso",
        usage_date: str | None = None,
        configured_limit: int = 30,
        provider_limit: int = 500,
    ) -> BudgetStatus:
        day = _usage_day(usage_date)
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """
                SELECT state, COALESCE(SUM(points), 0)
                FROM search_budget_usage
                WHERE provider=? AND usage_date=?
                GROUP BY state
                """,
                (provider, day),
            ).fetchall()
        totals = {state: int(points) for state, points in rows}
        return BudgetStatus(
            provider=provider,
            usage_date=day,
            spent_points=totals.get("spent", 0),
            reserved_points=totals.get("reserved", 0),
            configured_limit=max(int(configured_limit), 0),
            provider_limit=_effective_provider_limit(provider_limit),
        )

    def reserve(
        self,
        operation_key: str,
        points: int,
        *,
        provider: str = "metaso",
        usage_date: str | None = None,
        configured_limit: int = 30,
        provider_limit: int = 500,
    ) -> bool:
        if points <= 0:
            raise ValueError("points must be positive")
        if not operation_key.strip():
            raise ValueError("operation_key must not be empty")
        points = _effective_points(provider, points)
        day = _usage_day(usage_date)
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.database, timeout=30) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT state FROM search_budget_usage
                WHERE provider=? AND usage_date=? AND operation_key=?
                """,
                (provider, day, operation_key),
            ).fetchone()
            if existing:
                return False
            used = connection.execute(
                """
                SELECT COALESCE(SUM(points), 0)
                FROM search_budget_usage
                WHERE provider=? AND usage_date=? AND state IN ('reserved', 'spent')
                """,
                (provider, day),
            ).fetchone()[0]
            limit = min(
                max(int(configured_limit), 0),
                _effective_provider_limit(provider_limit),
                METASO_PROVIDER_ABSOLUTE_DAILY_LIMIT,
            )
            if int(used) + points > limit:
                return False
            connection.execute(
                """
                INSERT INTO search_budget_usage
                    (provider, usage_date, operation_key, points, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'reserved', ?, ?)
                """,
                (provider, day, operation_key, int(points), now, now),
            )
        return True

    def commit(
        self,
        operation_key: str,
        *,
        provider: str = "metaso",
        usage_date: str | None = None,
    ) -> None:
        day = _usage_day(usage_date)
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                """
                UPDATE search_budget_usage
                SET state='spent', updated_at=?
                WHERE provider=? AND usage_date=? AND operation_key=? AND state='reserved'
                """,
                (now, provider, day, operation_key),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown reserved operation: {operation_key}")

    def charge(
        self,
        operation_key: str,
        points: int,
        *,
        provider: str = "metaso",
        usage_date: str | None = None,
        configured_limit: int = 30,
        provider_limit: int = 500,
    ) -> bool:
        reserved = self.reserve(
            operation_key,
            points,
            provider=provider,
            usage_date=usage_date,
            configured_limit=configured_limit,
            provider_limit=provider_limit,
        )
        if reserved:
            self.commit(
                operation_key,
                provider=provider,
                usage_date=usage_date,
            )
        return reserved


__all__ = [
    "BudgetStatus",
    "METASO_CONSERVATIVE_POINTS_PER_SEARCH",
    "METASO_PROVIDER_ABSOLUTE_DAILY_LIMIT",
    "SearchBudgetLedger",
]
