"""Auditable data contracts shared by all dedicated aggregate adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceChannel:
    source_id: str
    name: str
    url: str
    source_grade: str
    event_prior: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    allowed_path_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceArticleIndex:
    source_id: str
    source_article_id: str
    channel: str
    canonical_url: str
    title: str
    published_at: str
    discovered_at: str
    cursor_value: str
    listing_page: str
    listing_position: int
    content_hash: str
    discovery_method: str
    summary: str = ""
    structured_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CleanArticle:
    index: SourceArticleIndex
    clean_body: str
    author: str = ""
    tags: tuple[str, ...] = ()
    structured_data: dict[str, Any] = field(default_factory=dict)
    extraction_method: str = "exact"
    adaptive_similarity: int | None = None
    evidence_locators: dict[str, str] = field(default_factory=dict)
    fetch_status: str = "ok"
    failure_reason: str = ""
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticEvent:
    source_id: str
    source_article_id: str
    canonical_url: str
    company_mentions: tuple[str, ...]
    canonical_company: str
    event_type: str
    event_date: str
    industry_tags: tuple[str, ...]
    funding_round: str = ""
    funding_amount: str = ""
    cumulative_funding_amount: str = ""
    investors: tuple[str, ...] = ()
    event_summary: str = ""
    evidence_quotes: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    confidence: str = "unknown"
    processor: str = "rules"
    prompt_version: str = ""
    content_hash: str = ""
    phase: str = "build_organize"
    event_status: str = "completed"
    claim_ids: tuple[str, ...] = ()
    span_ids: tuple[str, ...] = ()
    subject_entity_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterRun:
    adapter_id: str
    source_id: str
    started_at: str
    finished_at: str
    status: str
    listing_count: int
    incremental_count: int
    detail_success_count: int
    detail_failure_count: int
    rule_event_count: int
    minimax_event_count: int
    evidence_count: int
    adaptive_used_count: int
    semantic_failure_count: int = 0
    prefiltered_count: int = 0
    omissions_detected: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
