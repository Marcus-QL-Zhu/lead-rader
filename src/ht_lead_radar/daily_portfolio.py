"""Combine sector reports into one balanced hard-tech daily portfolio."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence


DEFAULT_DIRECTIONS = (
    "具身智能",
    "半导体",
    "商业航天",
    "核聚变",
    "脑机接口",
)
DEFAULT_PORTFOLIO_DIRECTION = "硬科技组合"


def combine_sector_reports(
    reports: Sequence[Mapping[str, Any]],
    *,
    direction: str = DEFAULT_PORTFOLIO_DIRECTION,
    target_count: int = 20,
) -> dict[str, Any]:
    if not reports:
        raise ValueError("at least one sector report is required")
    if target_count <= 0:
        raise ValueError("target_count must be positive")
    run_dates = {
        str((report.get("manifest") or {}).get("as_of") or "") for report in reports
    }
    if len(run_dates) != 1 or not next(iter(run_dates)):
        raise ValueError("sector reports must have the same non-empty as_of date")

    queues: list[list[dict[str, Any]]] = []
    children: list[dict[str, Any]] = []
    combined_source_summary: dict[str, Any] = {
        "runs": [],
        "failures": [],
        "normalization_exclusions": [],
        "adjacent_watchlist": [],
        "metaso_budget_by_sector": {},
    }
    for report in reports:
        manifest = report.get("manifest") or {}
        sector = str(manifest.get("direction") or "").strip()
        run_id = str(manifest.get("run_id") or "").strip()
        if not sector or not run_id:
            raise ValueError("each sector report requires direction and run_id")
        source_summary = manifest.get("source_summary") or {}
        for key in (
            "runs",
            "failures",
            "normalization_exclusions",
            "adjacent_watchlist",
        ):
            for raw in source_summary.get(key) or ():
                if isinstance(raw, Mapping):
                    item = copy.deepcopy(dict(raw))
                    item.setdefault("portfolio_sector", sector)
                else:
                    item = raw
                combined_source_summary[key].append(item)
        if source_summary.get("metaso_budget"):
            combined_source_summary["metaso_budget_by_sector"][sector] = copy.deepcopy(
                source_summary["metaso_budget"]
            )
        leads = []
        for raw in report.get("leads") or ():
            if not isinstance(raw, Mapping):
                continue
            lead = copy.deepcopy(dict(raw))
            lead["direction"] = str(lead.get("direction") or sector)
            leads.append(lead)
        leads.sort(key=lambda item: -float(item.get("score") or 0))
        queues.append(leads)
        children.append(
            {
                "direction": sector,
                "run_id": run_id,
                "lead_count": len(leads),
                "source_run_count": len(source_summary.get("runs") or ()),
                "source_failure_count": len(source_summary.get("failures") or ()),
            }
        )

    selected: list[dict[str, Any]] = []
    seen_companies: set[str] = set()
    positions = [0] * len(queues)
    while len(selected) < target_count:
        progressed = False
        for index, queue in enumerate(queues):
            while positions[index] < len(queue):
                lead = queue[positions[index]]
                positions[index] += 1
                company = str(lead.get("company") or "").strip()
                if not company or company in seen_companies:
                    continue
                selected.append(lead)
                seen_companies.add(company)
                progressed = True
                break
            if len(selected) >= target_count:
                break
        if not progressed:
            break

    selected.sort(
        key=lambda item: (
            -float(item.get("score") or 0),
            str(item.get("company") or ""),
            str(item.get("direction") or ""),
        )
    )

    child_manifests = [
        copy.deepcopy(dict(report.get("manifest") or {})) for report in reports
    ]
    identity = json.dumps(
        [(item["direction"], item["run_id"]) for item in children],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    first_policy = copy.deepcopy(dict(child_manifests[0].get("policy") or {}))
    portfolio_manifest = {
        "run_id": "portfolio_"
        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
        "generated_at": max(
            str(item.get("generated_at") or "") for item in child_manifests
        ),
        "as_of": next(iter(run_dates)),
        "direction": direction,
        "mode": "balanced-hardtech-portfolio",
        "request_plan": {
            "mode": "market_scan",
            "directions": [item["direction"] for item in children],
        },
        "execution_parameters": {
            "target_count": target_count,
            "sector_count": len(children),
        },
        "fact_summary": {
            item["direction"]: copy.deepcopy(
                dict(manifest.get("fact_summary") or {})
            )
            for item, manifest in zip(children, child_manifests)
        },
        "source_summary": combined_source_summary,
        "integration_status": {
            item["direction"]: copy.deepcopy(
                dict(manifest.get("integration_status") or {})
            )
            for item, manifest in zip(children, child_manifests)
        },
        "policy": {
            **first_policy,
            "portfolio_balanced_selection": True,
            "company_official_daily_discovery": False,
        },
        "portfolio": {
            "target_company_count": target_count,
            "selected_company_count": len(selected),
            "sector_reports": children,
            "child_manifests": child_manifests,
        },
    }
    return {
        "schema_version": max(
            int(report.get("schema_version") or 1) for report in reports
        ),
        "manifest": portfolio_manifest,
        "leads": selected,
        "late_opportunities": [],
        "float_matches": [],
        "deep_research": {},
    }


__all__ = [
    "DEFAULT_DIRECTIONS",
    "DEFAULT_PORTFOLIO_DIRECTION",
    "combine_sector_reports",
]
