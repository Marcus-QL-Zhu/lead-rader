"""Small JSON boundary helpers for the staged runtime.

The runtime deliberately checkpoints plain JSON instead of pickles.  These
helpers keep the conversion in one place and make old report files readable
after new optional fields are added to the domain dataclasses.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any, Mapping

from .models import CompanyLead, Evidence, OutreachRoute, ScoreComponent


def evidence_from_dict(value: Mapping[str, Any]) -> Evidence:
    payload = dict(value)
    for key in ("people", "organizations", "statement_ids", "industry_tags"):
        payload[key] = tuple(payload.get(key) or ())
    payload["event_slots"] = dict(payload.get("event_slots") or {})
    allowed = {item.name for item in fields(Evidence)}
    return Evidence(**{key: item for key, item in payload.items() if key in allowed})


def route_from_dict(value: Mapping[str, Any]) -> OutreachRoute:
    allowed = {item.name for item in fields(OutreachRoute)}
    return OutreachRoute(
        **{key: item for key, item in dict(value).items() if key in allowed}
    )


def score_component_from_dict(value: Mapping[str, Any]) -> ScoreComponent:
    payload = dict(value)
    payload["evidence_urls"] = tuple(payload.get("evidence_urls") or ())
    allowed = {item.name for item in fields(ScoreComponent)}
    return ScoreComponent(
        **{key: item for key, item in payload.items() if key in allowed}
    )


def lead_from_dict(value: Mapping[str, Any]) -> CompanyLead:
    payload = dict(value)
    payload["evidence"] = [
        evidence_from_dict(item) for item in payload.get("evidence", ())
    ]
    payload["outreach_routes"] = [
        route_from_dict(item) for item in payload.get("outreach_routes", ())
    ]
    payload["score_components"] = [
        score_component_from_dict(item) for item in payload.get("score_components", ())
    ]
    allowed = {item.name for item in fields(CompanyLead)}
    return CompanyLead(**{key: item for key, item in payload.items() if key in allowed})


__all__ = [
    "evidence_from_dict",
    "lead_from_dict",
    "route_from_dict",
    "score_component_from_dict",
]
