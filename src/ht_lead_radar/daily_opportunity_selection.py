"""Select daily companies using persisted report history and a short cooldown."""

from __future__ import annotations

import copy
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping


DEFAULT_COOLDOWN_DAYS = 7


def _urls(lead: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("source_url") or "").strip()
        for item in lead.get("evidence") or ()
        if isinstance(item, Mapping) and str(item.get("source_url") or "").strip()
    }


def _summary(lead: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "company": str(lead.get("company") or "").strip(),
        "direction": str(lead.get("direction") or "").strip(),
        "score": float(lead.get("score") or 0),
        **extra,
    }


def _reported_history(
    database: str | Path,
    *,
    as_of: date,
    cooldown_days: int,
) -> dict[str, dict[str, Any]]:
    path = Path(database)
    if not path.exists():
        return {}
    cutoff = (as_of - timedelta(days=cooldown_days)).isoformat()
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {
            "talent_pool_opportunity_links",
            "talent_pool_openclaw_reports",
        }
        if not required.issubset(tables):
            return {}
        rows = connection.execute(
            """
            SELECT o.company, o.run_date, o.evidence_urls_json
            FROM talent_pool_opportunity_links o
            JOIN talent_pool_openclaw_reports r
              ON r.snapshot_id=o.snapshot_id
            WHERE r.status='reported'
              AND o.run_date>? AND o.run_date<=?
            ORDER BY o.run_date DESC
            """,
            (cutoff, as_of.isoformat()),
        ).fetchall()
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        company = str(row["company"] or "").strip()
        if not company:
            continue
        item = output.setdefault(
            company,
            {"last_shown_date": str(row["run_date"]), "evidence_urls": set()},
        )
        if str(row["run_date"]) > item["last_shown_date"]:
            item["last_shown_date"] = str(row["run_date"])
        try:
            values = json.loads(row["evidence_urls_json"] or "[]")
        except json.JSONDecodeError:
            values = []
        item["evidence_urls"].update(
            str(value).strip() for value in values if str(value).strip()
        )
    return output


def _all_historical_companies(database: str | Path) -> set[str]:
    path = Path(database)
    if not path.exists():
        return set()
    with sqlite3.connect(path) as connection:
        try:
            rows = connection.execute(
                "SELECT DISTINCT company FROM talent_pool_opportunity_links"
            ).fetchall()
        except sqlite3.OperationalError:
            return set()
    return {str(row[0]).strip() for row in rows if str(row[0]).strip()}


def select_daily_opportunities(
    report: Mapping[str, Any],
    *,
    history_database: str | Path,
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
) -> dict[str, Any]:
    """Keep new, materially changed, or post-cooldown companies for generation."""

    if cooldown_days < 0:
        raise ValueError("cooldown_days must be non-negative")
    result = copy.deepcopy(dict(report))
    manifest = result.get("manifest") or {}
    as_of = date.fromisoformat(str(manifest.get("as_of") or ""))
    history = _reported_history(
        history_database,
        as_of=as_of,
        cooldown_days=cooldown_days,
    )
    all_historical = _all_historical_companies(history_database)
    new_items: list[dict[str, Any]] = []
    ongoing_items: list[dict[str, Any]] = []
    cooling_items: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    input_leads = [
        item for item in result.get("leads") or () if isinstance(item, Mapping)
    ]
    for raw_lead in input_leads:
        lead = dict(raw_lead)
        company = str(lead.get("company") or "").strip()
        prior = history.get(company)
        current_urls = _urls(lead)
        if prior is None:
            item = _summary(
                lead,
                reason=(
                    "returning_after_cooldown"
                    if company in all_historical
                    else "company_not_shown_before"
                ),
            )
            if company in all_historical:
                ongoing_items.append(item)
            else:
                new_items.append(item)
            eligible.append(lead)
            continue
        unseen_urls = sorted(current_urls - prior["evidence_urls"])
        if unseen_urls:
            new_items.append(
                _summary(
                    lead,
                    reason="material_new_evidence",
                    last_shown_date=prior["last_shown_date"],
                    new_evidence_urls=unseen_urls,
                )
            )
            eligible.append(lead)
            continue
        cooling_items.append(
            _summary(
                lead,
                reason="shown_without_new_evidence",
                last_shown_date=prior["last_shown_date"],
                cooldown_days=cooldown_days,
            )
        )

    result["leads"] = eligible
    result["daily_opportunity_segments"] = {
        "cooldown_days": cooldown_days,
        "input_company_count": len(input_leads),
        "eligible_company_count": len(eligible),
        "new_opportunities": new_items,
        "ongoing_watchlist": ongoing_items,
        "cooldown": cooling_items,
    }
    return result


__all__ = ["DEFAULT_COOLDOWN_DAYS", "select_daily_opportunities"]
