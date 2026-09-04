"""Select daily companies using persisted report history and a short cooldown."""

from __future__ import annotations

import copy
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


DEFAULT_COOLDOWN_DAYS = 7
_SHANGHAI = ZoneInfo("Asia/Shanghai")


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


def _item_key(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _merge_company_leads(
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep the highest-score record while retaining every collection item.

    A lower-scoring duplicate can carry the day's only new evidence URL or a
    different role hypothesis. Dropping that duplicate before cooldown would
    incorrectly suppress the company and discard useful audit context.
    """

    output = copy.deepcopy(dict(primary))
    for key, raw_value in secondary.items():
        # The selected record's score explanation must continue to reconcile
        # with its preserved highest score. Evidence and role collections are
        # merged below, but a lower-ranked score breakdown is not additive.
        if key == "score_components":
            continue
        if key not in output or output[key] in (None, "", [], {}, ()):
            output[key] = copy.deepcopy(raw_value)
            continue
        current = output[key]
        if isinstance(current, (list, tuple)) and isinstance(raw_value, (list, tuple)):
            merged = list(current)
            seen = {_item_key(item) for item in merged}
            for item in raw_value:
                marker = _item_key(item)
                if marker not in seen:
                    merged.append(copy.deepcopy(item))
                    seen.add(marker)
            output[key] = merged
        elif isinstance(current, Mapping) and isinstance(raw_value, Mapping):
            merged_mapping = copy.deepcopy(dict(current))
            for child_key, child_value in raw_value.items():
                if child_key not in merged_mapping:
                    merged_mapping[child_key] = copy.deepcopy(child_value)
            output[key] = merged_mapping
    return output


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
        if "talent_pool_final_report_opportunities" not in tables:
            # Pre-hotfix databases are migrated by TalentPoolStore.  Keep this
            # read-only helper safe when pointed at an unmigrated legacy DB.
            return {}
        queries: list[str] = []
        if "talent_pool_delivery_ledger" in tables:
            queries.append(
                """
                SELECT o.company, o.run_date, o.evidence_urls_json, d.delivered_at
                FROM talent_pool_final_report_opportunities o
                JOIN talent_pool_delivery_ledger d
                  ON d.snapshot_id=o.snapshot_id
                WHERE d.status='delivered'
                """
            )
        if "talent_pool_openclaw_reports" in tables:
            # Read-only compatibility for a DB whose delivery-ledger backfill
            # has not run yet. A reported OpenClaw row is itself a confirmed
            # user-visible delivery, never merely a generated snapshot.
            queries.append(
                """
                SELECT o.company, o.run_date, o.evidence_urls_json,
                       r.reported_at AS delivered_at
                FROM talent_pool_final_report_opportunities o
                JOIN talent_pool_openclaw_reports r
                  ON r.snapshot_id=o.snapshot_id
                WHERE r.status='reported' AND r.reported_at IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM talent_pool_delivery_ledger d
                    WHERE d.snapshot_id=o.snapshot_id AND d.status='delivered'
                  )
                """
                if "talent_pool_delivery_ledger" in tables
                else
                """
                SELECT o.company, o.run_date, o.evidence_urls_json,
                       r.reported_at AS delivered_at
                FROM talent_pool_final_report_opportunities o
                JOIN talent_pool_openclaw_reports r
                  ON r.snapshot_id=o.snapshot_id
                WHERE r.status='reported' AND r.reported_at IS NOT NULL
                """
            )
        if not queries:
            return {}
        rows = connection.execute(
            " UNION ALL ".join(queries)
            + " ORDER BY delivered_at DESC, run_date DESC"
        ).fetchall()
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        delivered_date = _delivery_local_date(row["delivered_at"])
        if len(delivered_date) == 10:
            # A historical replay must never learn from a delivery that had
            # not happened at its simulated as-of. Falling back to run_date
            # here leaked future user-visible state into cooldown history.
            if delivered_date > as_of.isoformat():
                continue
            effective_date = delivered_date
        else:
            effective_date = str(row["run_date"])
        if not (cutoff < effective_date <= as_of.isoformat()):
            continue
        company = str(row["company"] or "").strip()
        if not company:
            continue
        item = output.setdefault(
            company,
            {"last_shown_date": effective_date, "evidence_urls": set()},
        )
        if effective_date > item["last_shown_date"]:
            item["last_shown_date"] = effective_date
        try:
            values = json.loads(row["evidence_urls_json"] or "[]")
        except json.JSONDecodeError:
            values = []
        item["evidence_urls"].update(
            str(value).strip() for value in values if str(value).strip()
        )
    return output


def _delivery_local_date(value: object) -> str:
    """Translate aware delivery timestamps to the product's Shanghai day.

    Legacy rows sometimes contain only a date or a naive SQLite timestamp.  We
    preserve their recorded calendar date rather than guessing a timezone.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 10:
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10] if len(text) >= 10 else ""
    if parsed.tzinfo is None:
        return parsed.date().isoformat()
    return parsed.astimezone(_SHANGHAI).date().isoformat()


def _all_delivered_companies(
    database: str | Path,
    *,
    as_of: date,
) -> set[str]:
    """Return companies truly delivered on or before the simulated day.

    This deliberately uses the same confirmed-delivery semantics as cooldown
    history.  Historical replay must not label a company as ``returning``
    merely because the live database contains a delivery from its future.
    """

    path = Path(database)
    if not path.exists():
        return set()
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "talent_pool_final_report_opportunities" not in tables:
            return set()
        queries: list[str] = []
        if "talent_pool_delivery_ledger" in tables:
            queries.append(
                """
                SELECT o.company, o.run_date, d.delivered_at
                FROM talent_pool_final_report_opportunities o
                JOIN talent_pool_delivery_ledger d ON d.snapshot_id=o.snapshot_id
                WHERE d.status='delivered'
                """
            )
        if "talent_pool_openclaw_reports" in tables:
            queries.append(
                """
                SELECT o.company, o.run_date, r.reported_at AS delivered_at
                FROM talent_pool_final_report_opportunities o
                JOIN talent_pool_openclaw_reports r ON r.snapshot_id=o.snapshot_id
                WHERE r.status='reported' AND r.reported_at IS NOT NULL
                {ledger_exclusion}
                """
                .format(
                    ledger_exclusion=(
                        "AND NOT EXISTS ("
                        "SELECT 1 FROM talent_pool_delivery_ledger d "
                        "WHERE d.snapshot_id=o.snapshot_id "
                        "AND d.status='delivered')"
                        if "talent_pool_delivery_ledger" in tables
                        else ""
                    )
                )
            )
        if not queries:
            return set()
        rows = connection.execute(" UNION ALL ".join(queries)).fetchall()
    output: set[str] = set()
    for company_value, run_date, delivered_at in rows:
        effective_date = _delivery_local_date(delivered_at) or str(run_date or "")
        if effective_date and effective_date <= as_of.isoformat():
            company = str(company_value or "").strip()
            if company:
                output.add(company)
    return output


def select_daily_opportunities(
    report: Mapping[str, Any],
    *,
    history_database: str | Path,
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
    target_count: int | None = None,
    fill_from_cooldown: bool = False,
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
    all_historical = _all_delivered_companies(history_database, as_of=as_of)
    new_items: list[dict[str, Any]] = []
    ongoing_items: list[dict[str, Any]] = []
    cooling_items: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    unique_by_company: dict[str, dict[str, Any]] = {}
    for item in result.get("leads") or ():
        if not isinstance(item, Mapping):
            continue
        company = str(item.get("company") or "").strip()
        if not company:
            continue
        candidate = dict(item)
        current = unique_by_company.get(company)
        if current is None:
            unique_by_company[company] = candidate
            continue
        if float(candidate.get("score") or 0) > float(current.get("score") or 0):
            unique_by_company[company] = _merge_company_leads(candidate, current)
        else:
            unique_by_company[company] = _merge_company_leads(current, candidate)
    input_leads = sorted(
        unique_by_company.values(),
        key=lambda item: (-float(item.get("score") or 0), str(item.get("company") or "").casefold()),
    )
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

    selected = eligible
    if target_count is not None:
        if target_count < 1:
            raise ValueError("target_count must be positive")
        selected = eligible[:target_count]
        if fill_from_cooldown:
            raise ValueError("cooldown-suppressed companies cannot refill the final report")

    result["leads"] = selected
    result["daily_opportunity_segments"] = {
        "cooldown_days": cooldown_days,
        "input_company_count": len(input_leads),
        "eligible_company_count": len(eligible),
        "selected_company_count": len(selected),
        "suppressed_company_count": len(cooling_items),
        "new_evidence_company_count": sum(
            item.get("reason") == "material_new_evidence" for item in new_items
        ),
        "returning_company_count": len(ongoing_items),
        "input_companies": [str(item.get("company") or "") for item in input_leads],
        "eligible_companies": [str(item.get("company") or "") for item in eligible],
        "selected_companies": [str(item.get("company") or "") for item in selected],
        "suppressed_companies": [item["company"] for item in cooling_items],
        "new_opportunities": new_items,
        "ongoing_watchlist": ongoing_items,
        "cooldown": cooling_items,
    }
    return result


__all__ = ["DEFAULT_COOLDOWN_DAYS", "select_daily_opportunities"]
