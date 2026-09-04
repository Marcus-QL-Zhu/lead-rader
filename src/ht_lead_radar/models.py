from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .sanitization import sanitize_url


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
    source_grade: str = "B"
    direction: str = ""
    people: tuple[str, ...] = ()
    organizations: tuple[str, ...] = ()
    event_slots: dict[str, Any] = field(default_factory=dict)
    document_id: str = ""
    event_id: str = ""
    statement_ids: tuple[str, ...] = ()
    independent_source_group: str = ""
    published_at: str = ""
    observed_at: str = ""
    content_sha256: str = ""
    company_type: str = ""
    source_excerpt: str = ""
    source_locator: str = ""
    analyst_note: str = ""
    source_kind: str = ""
    source_id: str = ""
    industry_tags: tuple[str, ...] = ()
    is_recruiting_input: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_url", sanitize_url(self.source_url))


@dataclass(frozen=True)
class OutreachRoute:
    kind: str
    target: str
    path: str
    evidence_url: str
    grade: str
    note: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_url", sanitize_url(self.evidence_url))


@dataclass(frozen=True)
class ScoreComponent:
    key: str
    label: str
    points: float
    reason: str
    evidence_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_urls",
            tuple(sanitize_url(item) for item in self.evidence_urls),
        )


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
    industry_layer: str = "core"
    mainland_relevance: str = "default-market"
    request_mode: str = "market_scan"
    basic_research: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
