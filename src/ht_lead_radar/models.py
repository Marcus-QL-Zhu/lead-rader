from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Evidence:
    company: str
    event_type: str
    phase: str
    event_date: str
    title: str
    snippet: str
    source_url: str
    source_name: str
    source_grade: str = 'B'
    direction: str = ''
    people: tuple[str, ...] = ()
    organizations: tuple[str, ...] = ()
    document_id: str = ''
    event_id: str = ''
    statement_ids: tuple[str, ...] = ()
    independent_source_group: str = ''


@dataclass(frozen=True)
class OutreachRoute:
    kind: str
    target: str
    path: str
    evidence_url: str
    grade: str
    note: str


@dataclass(frozen=True)
class ScoreComponent:
    key: str
    label: str
    points: float
    reason: str
    evidence_urls: tuple[str, ...] = ()


@dataclass
class CompanyLead:
    company: str
    direction: str
    score: float
    confidence_grade: str
    timing_stage: str
    target_roles: list[str]
    hiring_thesis: str
    evidence: list[Evidence]
    outreach_routes: list[OutreachRoute]
    risk_notes: list[str] = field(default_factory=list)
    lead_time_days: int | None = None
    gates: dict[str, bool] = field(default_factory=dict)
    score_components: list[ScoreComponent] = field(default_factory=list)
    industry_layer: str = 'core'
    mainland_relevance: str = 'default-market'
    request_mode: str = 'market_scan'
    basic_research: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
