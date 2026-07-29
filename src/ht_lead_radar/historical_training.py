"""Historical company-month dataset construction and lightweight calibration.

The module deliberately separates two questions:

1. hiring propensity: does a company publish any eligible Director+ role?
2. role-family ranking: conditional on hiring, which role family is most likely?

Confirmed negatives require replayable coverage artifacts.  When historical
career pages cannot be replayed, the row remains ``unknown``.  Role-family
ranking may still use explicitly marked contrastive alternatives, but those
weak negatives are never promoted to confirmed market negatives.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .backtest import HistoricalJob, ROLE_FAMILIES, role_family
from .models import Evidence
from .signals import canonical_event_type


SCHEMA_VERSION = 1
DEFAULT_ROLE_FAMILIES = tuple(name for name, _ in ROLE_FAMILIES)
ALLOWED_SPLITS = {"train", "calibration", "test"}
ALLOWED_LABELS = {"positive", "negative", "unknown", "contrastive_negative"}
ALLOWED_OBSERVABILITY = {"replayable", "search_only", "partial", "unknown"}


def _parse_date(value: str, *, field_name: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _month_end(value: date) -> date:
    if value.month == 12:
        next_month = date(value.year + 1, 1, 1)
    else:
        next_month = date(value.year, value.month + 1, 1)
    return next_month - timedelta(days=1)


def _shift_month(value: date, months: int) -> date:
    absolute = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(absolute, 12)
    return date(year, month_zero + 1, 1)


def monthly_cutoffs_before(
    published_at: str | date,
    *,
    months_back: int = 4,
) -> tuple[date, ...]:
    """Return unique month-end cutoffs preceding a historical job."""

    anchor = (
        published_at
        if isinstance(published_at, date)
        else _parse_date(published_at, field_name="published_at")
    )
    values = {
        _month_end(_shift_month(anchor.replace(day=1), -offset))
        for offset in range(1, months_back + 1)
    }
    return tuple(sorted(value for value in values if value < anchor))


@dataclass(frozen=True)
class CompanyPartition:
    company_id: str
    company: str
    company_type: str
    split: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.company_id.strip() or not self.company.strip():
            raise ValueError("company_id and company are required")
        if self.split not in ALLOWED_SPLITS:
            raise ValueError(f"invalid split: {self.split}")


@dataclass(frozen=True)
class CoverageArtifact:
    channel: str
    source_url: str
    captured_at: str
    content_sha256: str
    storage_path: str = ""

    def is_replayable(self) -> bool:
        try:
            datetime.fromisoformat(self.captured_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        digest = self.content_sha256.lower()
        return (
            bool(self.channel.strip())
            and self.source_url.startswith(("http://", "https://"))
            and len(digest) == 64
            and all(char in "0123456789abcdef" for char in digest)
        )


@dataclass(frozen=True)
class CoverageAudit:
    company: str
    window_start: str
    window_end_exclusive: str
    channels_completed: tuple[str, ...]
    artifacts: tuple[CoverageArtifact, ...] = ()
    searched_at: str = ""
    notes: str = ""

    def observability(self) -> str:
        channels = {value.strip() for value in self.channels_completed if value.strip()}
        required = {"official_careers", "public_web_search"}
        replayable_channels = {
            artifact.channel
            for artifact in self.artifacts
            if artifact.is_replayable()
        }
        if required.issubset(replayable_channels):
            return "replayable"
        if required.issubset(channels):
            return "search_only"
        if channels:
            return "partial"
        return "unknown"

    def covers(self, cutoff: date, horizon_end: date) -> bool:
        start = _parse_date(self.window_start, field_name="window_start")
        end = _parse_date(
            self.window_end_exclusive,
            field_name="window_end_exclusive",
        )
        return start <= cutoff + timedelta(days=1) and end >= horizon_end


@dataclass(frozen=True)
class HistoricalTrainingRow:
    sample_id: str
    company_id: str
    company: str
    company_type: str
    split: str
    cutoff: str
    horizon_end: str
    role_family: str
    label: str
    label_weight: float
    observability: str
    evidence_ids: tuple[str, ...]
    matched_job_ids: tuple[str, ...]
    features: Mapping[str, float]
    row_sha256: str

    def __post_init__(self) -> None:
        if self.split not in ALLOWED_SPLITS:
            raise ValueError(f"invalid split: {self.split}")
        if self.label not in ALLOWED_LABELS:
            raise ValueError(f"invalid label: {self.label}")
        if self.observability not in ALLOWED_OBSERVABILITY:
            raise ValueError(f"invalid observability: {self.observability}")
        if self.label_weight < 0:
            raise ValueError("label_weight must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["features"] = dict(sorted(self.features.items()))
        return value


@dataclass(frozen=True)
class HistoricalDataset:
    dataset_id: str
    created_at: str
    schema_version: int
    source_hashes: Mapping[str, str]
    companies: tuple[CompanyPartition, ...]
    rows: tuple[HistoricalTrainingRow, ...]
    summary: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
            "source_hashes": dict(sorted(self.source_hashes.items())),
            "companies": [asdict(item) for item in self.companies],
            "rows": [item.to_dict() for item in self.rows],
            "summary": dict(self.summary),
        }


def stable_company_id(company: str) -> str:
    normalized = " ".join(str(company or "").strip().lower().split())
    if not normalized:
        raise ValueError("company is required")
    return "co_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def evidence_available_at(item: Evidence) -> date | None:
    """Return defensible public availability; never fall back to event_date."""

    for value in (item.published_at, item.observed_at):
        text = str(value or "").strip()
        if not text:
            continue
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            continue
    return None


def evidence_identity(item: Evidence) -> str:
    if item.event_id.strip():
        return item.event_id.strip()
    payload = {
        "company": item.company,
        "event_type": canonical_event_type(item.event_type),
        "phase": item.phase,
        "event_date": item.event_date,
        "published_at": item.published_at,
        "observed_at": item.observed_at,
        "title": item.title,
        "snippet": item.snippet,
        "source_url": item.source_url,
        "content_sha256": item.content_sha256,
    }
    return "ev_" + _sha256_json(payload)[:16]


def job_identity(item: HistoricalJob) -> str:
    payload = asdict(item)
    return "job_" + _sha256_json(payload)[:16]


def _company_matches(value: str, company: CompanyPartition) -> bool:
    names = {company.company, *company.aliases}
    normalized = " ".join(str(value or "").strip().lower().split())
    return normalized in {
        " ".join(str(name).strip().lower().split())
        for name in names
        if str(name).strip()
    }


def _features_for(
    evidence: Sequence[Evidence],
    *,
    cutoff: date,
    role: str,
    company_type: str,
) -> dict[str, float]:
    features: dict[str, float] = {
        f"role:{role}": 1.0,
        f"company_type:{company_type or 'unknown'}": 1.0,
        "evidence:count": float(len(evidence)),
    }
    source_groups: set[str] = set()
    for item in evidence:
        event = canonical_event_type(item.event_type) or "other"
        available = evidence_available_at(item)
        if available is None:
            continue
        days = max(0, (cutoff - available).days)
        recency = 1.0 / (1.0 + days / 30.0)
        features[f"event:{event}:count"] = (
            features.get(f"event:{event}:count", 0.0) + 1.0
        )
        features[f"event:{event}:recency"] = max(
            features.get(f"event:{event}:recency", 0.0),
            recency,
        )
        features[f"event_role:{event}:{role}"] = (
            features.get(f"event_role:{event}:{role}", 0.0) + recency
        )
        phase = str(item.phase or "unknown").strip() or "unknown"
        features[f"phase:{phase}"] = features.get(f"phase:{phase}", 0.0) + 1.0
        grade = str(item.source_grade or "unknown").strip().upper() or "UNKNOWN"
        features[f"source_grade:{grade}"] = (
            features.get(f"source_grade:{grade}", 0.0) + 1.0
        )
        group = (
            str(item.independent_source_group or item.source_kind or "unknown").strip()
            or "unknown"
        )
        source_groups.add(group)
        features[f"source_group:{group}"] = (
            features.get(f"source_group:{group}", 0.0) + 1.0
        )
    features["source_group:independent_count"] = float(len(source_groups))
    return dict(sorted(features.items()))


def _row(
    *,
    company: CompanyPartition,
    cutoff: date,
    horizon_end: date,
    role: str,
    label: str,
    label_weight: float,
    observability: str,
    evidence: Sequence[Evidence],
    jobs: Sequence[HistoricalJob],
) -> HistoricalTrainingRow:
    evidence_ids = tuple(sorted({evidence_identity(item) for item in evidence}))
    matched_job_ids = tuple(sorted({job_identity(item) for item in jobs}))
    features = _features_for(
        evidence,
        cutoff=cutoff,
        role=role,
        company_type=company.company_type,
    )
    core = {
        "company_id": company.company_id,
        "split": company.split,
        "cutoff": cutoff.isoformat(),
        "horizon_end": horizon_end.isoformat(),
        "role_family": role,
        "label": label,
        "label_weight": label_weight,
        "observability": observability,
        "evidence_ids": evidence_ids,
        "matched_job_ids": matched_job_ids,
        "features": features,
    }
    digest = _sha256_json(core)
    return HistoricalTrainingRow(
        sample_id="sample_" + digest[:20],
        company_id=company.company_id,
        company=company.company,
        company_type=company.company_type,
        split=company.split,
        cutoff=cutoff.isoformat(),
        horizon_end=horizon_end.isoformat(),
        role_family=role,
        label=label,
        label_weight=label_weight,
        observability=observability,
        evidence_ids=evidence_ids,
        matched_job_ids=matched_job_ids,
        features=features,
        row_sha256=digest,
    )


def build_company_month_rows(
    *,
    companies: Sequence[CompanyPartition],
    evidence: Sequence[Evidence],
    jobs: Sequence[HistoricalJob],
    coverage_audits: Sequence[CoverageAudit] = (),
    explicit_cutoffs: Mapping[str, Sequence[str | date]] | None = None,
    role_families: Sequence[str] = DEFAULT_ROLE_FAMILIES,
    months_back_from_job: int = 4,
    lookback_days: int = 180,
    horizon_days: int = 90,
    add_contrastive_negatives: bool = True,
    contrastive_weight: float = 0.25,
) -> tuple[HistoricalTrainingRow, ...]:
    """Build leakage-safe company-month × role-family rows."""

    rows: list[HistoricalTrainingRow] = []
    explicit_cutoffs = explicit_cutoffs or {}
    for company in companies:
        company_evidence = [
            item for item in evidence if _company_matches(item.company, company)
        ]
        company_jobs = [
            item for item in jobs if _company_matches(item.company, company)
        ]
        cutoffs: set[date] = set()
        for value in explicit_cutoffs.get(company.company_id, ()):
            cutoffs.add(
                value
                if isinstance(value, date)
                else _parse_date(str(value), field_name="cutoff")
            )
        for item in company_jobs:
            cutoffs.update(
                monthly_cutoffs_before(
                    item.published_at,
                    months_back=months_back_from_job,
                )
            )
        if not cutoffs:
            continue
        for cutoff in sorted(cutoffs):
            horizon_end = cutoff + timedelta(days=horizon_days)
            lower_bound = cutoff - timedelta(days=lookback_days)
            usable_evidence = [
                item
                for item in company_evidence
                if (
                    (available := evidence_available_at(item)) is not None
                    and lower_bound <= available <= cutoff
                    and not item.is_recruiting_input
                    and canonical_event_type(item.event_type)
                    not in {"job_ad", "workforce_cluster"}
                )
            ]
            future_jobs = [
                item
                for item in company_jobs
                if cutoff
                < _parse_date(item.published_at, field_name="published_at")
                <= horizon_end
            ]
            jobs_by_family: dict[str, list[HistoricalJob]] = {}
            for item in future_jobs:
                family = role_family(item.title, item.description)
                if family:
                    jobs_by_family.setdefault(family, []).append(item)
            audits = [
                audit
                for audit in coverage_audits
                if _company_matches(audit.company, company)
                and audit.covers(cutoff, horizon_end)
            ]
            observability = max(
                (audit.observability() for audit in audits),
                key=("unknown", "partial", "search_only", "replayable").index,
                default="unknown",
            )
            for role in role_families:
                matched = jobs_by_family.get(role, [])
                if matched:
                    label = "positive"
                    weight = 1.0
                elif observability == "replayable":
                    label = "negative"
                    weight = 1.0
                elif future_jobs and add_contrastive_negatives:
                    label = "contrastive_negative"
                    weight = contrastive_weight
                else:
                    label = "unknown"
                    weight = 0.0
                rows.append(
                    _row(
                        company=company,
                        cutoff=cutoff,
                        horizon_end=horizon_end,
                        role=role,
                        label=label,
                        label_weight=weight,
                        observability=observability,
                        evidence=usable_evidence,
                        jobs=matched,
                    )
                )
    validate_rows(rows)
    return tuple(rows)


def validate_company_partitions(companies: Sequence[CompanyPartition]) -> None:
    by_id: dict[str, CompanyPartition] = {}
    normalized_names: dict[str, str] = {}
    for item in companies:
        previous = by_id.get(item.company_id)
        if previous and previous != item:
            raise ValueError(f"company_id appears in multiple partitions: {item.company_id}")
        by_id[item.company_id] = item
        for name in (item.company, *item.aliases):
            normalized = " ".join(name.strip().lower().split())
            owner = normalized_names.get(normalized)
            if owner and owner != item.company_id:
                raise ValueError(f"company alias crosses partitions: {name}")
            normalized_names[normalized] = item.company_id


def validate_rows(rows: Sequence[HistoricalTrainingRow]) -> None:
    seen: set[str] = set()
    company_split: dict[str, str] = {}
    for row in rows:
        if row.sample_id in seen:
            raise ValueError(f"duplicate sample_id: {row.sample_id}")
        seen.add(row.sample_id)
        prior = company_split.setdefault(row.company_id, row.split)
        if prior != row.split:
            raise ValueError(f"company leaks across splits: {row.company_id}")
        core = {
            "company_id": row.company_id,
            "split": row.split,
            "cutoff": row.cutoff,
            "horizon_end": row.horizon_end,
            "role_family": row.role_family,
            "label": row.label,
            "label_weight": row.label_weight,
            "observability": row.observability,
            "evidence_ids": row.evidence_ids,
            "matched_job_ids": row.matched_job_ids,
            "features": dict(row.features),
        }
        if _sha256_json(core) != row.row_sha256:
            raise ValueError(f"row hash mismatch: {row.sample_id}")


def dataset_summary(
    companies: Sequence[CompanyPartition],
    rows: Sequence[HistoricalTrainingRow],
) -> dict[str, Any]:
    labels: dict[str, int] = {}
    splits: dict[str, set[str]] = {}
    for row in rows:
        labels[row.label] = labels.get(row.label, 0) + 1
        splits.setdefault(row.split, set()).add(row.company_id)
    return {
        "company_count": len({item.company_id for item in companies}),
        "companies_by_split": {
            split: len(values) for split, values in sorted(splits.items())
        },
        "row_count": len(rows),
        "rows_by_label": dict(sorted(labels.items())),
        "distinct_cutoffs": len({row.cutoff for row in rows}),
        "distinct_role_families": len({row.role_family for row in rows}),
        "replayable_negative_count": sum(
            row.label == "negative" and row.observability == "replayable"
            for row in rows
        ),
    }


def make_dataset(
    *,
    companies: Sequence[CompanyPartition],
    rows: Sequence[HistoricalTrainingRow],
    source_hashes: Mapping[str, str],
    created_at: str | None = None,
) -> HistoricalDataset:
    validate_company_partitions(companies)
    validate_rows(rows)
    created = created_at or datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_hashes": dict(sorted(source_hashes.items())),
        "companies": [asdict(item) for item in companies],
        "row_hashes": [item.row_sha256 for item in rows],
    }
    dataset_id = "hist_" + _sha256_json(payload)[:20]
    return HistoricalDataset(
        dataset_id=dataset_id,
        created_at=created,
        schema_version=SCHEMA_VERSION,
        source_hashes=dict(source_hashes),
        companies=tuple(companies),
        rows=tuple(rows),
        summary=dataset_summary(companies, rows),
    )


def deterministic_company_split(
    companies: Sequence[Mapping[str, str]],
    *,
    train_count: int,
    calibration_count: int,
    test_count: int,
    seed: str,
    forced_test: Iterable[str] = (),
) -> tuple[CompanyPartition, ...]:
    """Create a company-level split, stratified by company_type."""

    total = train_count + calibration_count + test_count
    unique: dict[str, Mapping[str, str]] = {}
    for item in companies:
        name = str(item.get("company") or "").strip()
        if not name:
            continue
        company_id = str(item.get("company_id") or stable_company_id(name))
        if company_id in unique:
            continue
        unique[company_id] = item
    if len(unique) < total:
        raise ValueError(f"need {total} companies, found {len(unique)}")
    forced_names = {" ".join(value.strip().lower().split()) for value in forced_test}
    forced_ids = {
        company_id
        for company_id, item in unique.items()
        if " ".join(str(item.get("company") or "").strip().lower().split())
        in forced_names
    }
    if len(forced_ids) > test_count:
        raise ValueError("forced_test exceeds test_count")

    def order_key(company_id: str) -> str:
        return hashlib.sha256(f"{seed}:{company_id}".encode("utf-8")).hexdigest()

    remaining = [company_id for company_id in unique if company_id not in forced_ids]

    def stratified_take(
        candidate_ids: Sequence[str],
        count: int,
    ) -> tuple[set[str], list[str]]:
        groups: dict[str, list[str]] = {}
        for company_id in candidate_ids:
            company_type = str(
                unique[company_id].get("company_type") or "unknown"
            )
            groups.setdefault(company_type, []).append(company_id)
        for values in groups.values():
            values.sort(
                key=lambda company_id: (
                    -int(unique[company_id].get("priority") or 0),
                    order_key(company_id),
                )
            )
        selected: set[str] = set()
        types = sorted(groups)
        while len(selected) < count:
            progressed = False
            for company_type in types:
                values = groups[company_type]
                if not values:
                    continue
                selected.add(values.pop(0))
                progressed = True
                if len(selected) >= count:
                    break
            if not progressed:
                break
        leftovers = [
            company_id
            for company_type in types
            for company_id in groups[company_type]
        ]
        if len(selected) != count:
            raise ValueError(f"unable to allocate {count} stratified companies")
        return selected, leftovers

    test_ids = set(forced_ids)
    additional_test, rest = stratified_take(
        remaining,
        test_count - len(test_ids),
    )
    test_ids.update(additional_test)
    calibration_ids, rest = stratified_take(rest, calibration_count)
    train_ids, _unused = stratified_take(rest, train_count)
    selected = train_ids | calibration_ids | test_ids
    result = []
    for company_id in sorted(selected):
        item = unique[company_id]
        split = (
            "test"
            if company_id in test_ids
            else "calibration"
            if company_id in calibration_ids
            else "train"
        )
        aliases = tuple(
            str(value).strip()
            for value in item.get("aliases", ())
            if str(value).strip()
        )
        result.append(
            CompanyPartition(
                company_id=company_id,
                company=str(item.get("company") or "").strip(),
                company_type=str(item.get("company_type") or "unknown").strip(),
                split=split,
                aliases=aliases,
            )
        )
    validate_company_partitions(result)
    return tuple(result)


@dataclass(frozen=True)
class LogisticModel:
    feature_names: tuple[str, ...]
    weights: tuple[float, ...]
    intercept: float
    l2: float

    def score(self, features: Mapping[str, float]) -> float:
        value = self.intercept + sum(
            weight * float(features.get(name, 0.0))
            for name, weight in zip(self.feature_names, self.weights)
        )
        if value >= 0:
            return 1.0 / (1.0 + math.exp(-value))
        exp_value = math.exp(value)
        return exp_value / (1.0 + exp_value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SIGNAL_ROLE_PRIORS: Mapping[str, tuple[str, ...]] = {
    "executive_change": (
        "general_management",
        "strategy_transformation",
        "human_resources",
        "sales_accounts",
        "finance_control",
    ),
    "merger_acquisition": (
        "corporate_development",
        "strategy_transformation",
        "finance_control",
        "legal",
        "human_resources",
    ),
    "joint_venture_or_spinout": (
        "general_management",
        "strategy_transformation",
        "finance_control",
        "human_resources",
        "sales_accounts",
    ),
    "ipo_or_listing": (
        "capital_markets",
        "finance_control",
        "legal",
        "strategy_transformation",
    ),
    "new_site_or_entity": (
        "general_management",
        "human_resources",
        "manufacturing",
        "process_engineering",
        "supply_chain",
    ),
    "factory_or_capacity": (
        "manufacturing",
        "process_engineering",
        "quality",
        "supply_chain",
        "ehs_compliance",
        "digital_it",
    ),
    "project_buildout": (
        "program_delivery",
        "process_engineering",
        "manufacturing",
        "supply_chain",
        "ehs_compliance",
    ),
    "procurement_intention": (
        "supply_chain",
        "digital_it",
        "program_delivery",
    ),
    "procurement_tender": (
        "supply_chain",
        "digital_it",
        "program_delivery",
    ),
    "major_order": (
        "program_delivery",
        "manufacturing",
        "supply_chain",
        "quality",
        "sales_accounts",
    ),
    "customer_validation": (
        "quality",
        "program_delivery",
        "product",
        "sales_accounts",
    ),
    "funding": (
        "finance_control",
        "human_resources",
        "research_development",
        "commercialization",
        "strategy_transformation",
    ),
    "global_expansion": (
        "international",
        "sales_accounts",
        "channel_ecosystem",
        "supply_chain",
        "regulatory_clinical",
    ),
    "channel_expansion": (
        "channel_ecosystem",
        "sales_accounts",
        "marketing",
    ),
    "technical_milestone": (
        "research_development",
        "product",
        "process_engineering",
        "quality",
        "commercialization",
    ),
    "data_or_model": (
        "algorithm_data",
        "research_development",
        "product",
        "digital_it",
    ),
    "regulatory_or_clinical": (
        "regulatory_clinical",
        "quality",
        "research_development",
        "program_delivery",
    ),
    "research_or_ip": (
        "research_development",
        "algorithm_data",
        "product",
        "legal",
    ),
    "enterprise_system": (
        "digital_it",
        "strategy_transformation",
        "program_delivery",
    ),
    "partnership": (
        "channel_ecosystem",
        "commercialization",
        "program_delivery",
        "sales_accounts",
    ),
    "policy_or_standard": (
        "government_affairs",
        "regulatory_clinical",
        "research_development",
    ),
}


@dataclass(frozen=True)
class RuleRoleModel:
    """Transparent signal-to-role prior used as the no-training baseline."""

    priors: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: SIGNAL_ROLE_PRIORS
    )

    def score(self, features: Mapping[str, float]) -> float:
        roles = [name.removeprefix("role:") for name in features if name.startswith("role:")]
        if len(roles) != 1:
            return 0.0
        role = roles[0]
        raw = 0.0
        for event, preferred_roles in self.priors.items():
            recency = float(features.get(f"event:{event}:recency", 0.0))
            if not recency or role not in preferred_roles:
                continue
            rank = preferred_roles.index(role)
            raw += recency * max(0.25, 1.0 - rank * 0.15)
        return 1.0 - math.exp(-max(0.0, raw))


def fit_logistic_regression(
    rows: Sequence[HistoricalTrainingRow],
    *,
    split: str = "train",
    l2: float = 0.1,
    learning_rate: float = 0.05,
    iterations: int = 800,
) -> LogisticModel:
    usable = [
        row
        for row in rows
        if row.split == split
        and row.label in {"positive", "negative", "contrastive_negative"}
        and row.label_weight > 0
    ]
    positives = sum(row.label == "positive" for row in usable)
    negatives = len(usable) - positives
    if positives < 2 or negatives < 2:
        raise ValueError(
            "training requires at least two positive and two negative/contrastive rows"
        )
    feature_names = tuple(
        sorted({name for row in usable for name in row.features})
    )
    weights = [0.0] * len(feature_names)
    intercept = math.log((positives + 0.5) / (negatives + 0.5))
    total_weight = sum(row.label_weight for row in usable)
    for _ in range(iterations):
        gradient = [0.0] * len(weights)
        intercept_gradient = 0.0
        for row in usable:
            linear = intercept + sum(
                weights[index] * float(row.features.get(name, 0.0))
                for index, name in enumerate(feature_names)
            )
            if linear >= 0:
                prediction = 1.0 / (1.0 + math.exp(-linear))
            else:
                exp_value = math.exp(linear)
                prediction = exp_value / (1.0 + exp_value)
            target = 1.0 if row.label == "positive" else 0.0
            error = (prediction - target) * row.label_weight
            intercept_gradient += error
            for index, name in enumerate(feature_names):
                gradient[index] += error * float(row.features.get(name, 0.0))
        intercept -= learning_rate * intercept_gradient / total_weight
        for index in range(len(weights)):
            regularized = gradient[index] / total_weight + l2 * weights[index]
            weights[index] -= learning_rate * regularized
    return LogisticModel(
        feature_names=feature_names,
        weights=tuple(weights),
        intercept=intercept,
        l2=l2,
    )


def evaluate_role_ranker(
    model: LogisticModel,
    rows: Sequence[HistoricalTrainingRow],
    *,
    split: str,
    top_k: int = 5,
) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[HistoricalTrainingRow]] = {}
    for row in rows:
        if row.split != split:
            continue
        groups.setdefault((row.company_id, row.cutoff), []).append(row)
    evaluated = 0
    top1 = 0
    topk = 0
    reciprocal_rank = 0.0
    brier_terms: list[float] = []
    ranked_groups: list[tuple[float, bool]] = []
    family_counts: dict[str, dict[str, int]] = {}
    type_counts: dict[str, dict[str, int]] = {}
    for group_rows in groups.values():
        positives = [row for row in group_rows if row.label == "positive"]
        if not positives:
            continue
        evaluated += 1
        ranked = sorted(
            group_rows,
            key=lambda row: (-model.score(row.features), row.role_family),
        )
        positive_roles = {row.role_family for row in positives}
        ranks = [
            index
            for index, row in enumerate(ranked, start=1)
            if row.role_family in positive_roles
        ]
        best_rank = min(ranks)
        top1_hit = best_rank == 1
        topk_hit = best_rank <= top_k
        top1 += top1_hit
        topk += topk_hit
        reciprocal_rank += 1.0 / best_rank
        predicted_role = ranked[0].role_family
        ranked_groups.append((model.score(ranked[0].features), top1_hit))
        all_families = positive_roles | {predicted_role}
        for family in all_families:
            counts = family_counts.setdefault(
                family,
                {"tp": 0, "fp": 0, "fn": 0},
            )
            predicted = predicted_role == family
            actual = family in positive_roles
            if predicted and actual:
                counts["tp"] += 1
            elif predicted:
                counts["fp"] += 1
            elif actual:
                counts["fn"] += 1
        company_type = group_rows[0].company_type or "unknown"
        slice_counts = type_counts.setdefault(
            company_type,
            {"evaluated": 0, "top1": 0, "topk": 0},
        )
        slice_counts["evaluated"] += 1
        slice_counts["top1"] += int(top1_hit)
        slice_counts["topk"] += int(topk_hit)
        for row in group_rows:
            if row.label not in {
                "positive",
                "negative",
                "contrastive_negative",
            }:
                continue
            target = 1.0 if row.label == "positive" else 0.0
            brier_terms.append((model.score(row.features) - target) ** 2)
    macro_f1_values = []
    for counts in family_counts.values():
        denominator = 2 * counts["tp"] + counts["fp"] + counts["fn"]
        macro_f1_values.append(
            2 * counts["tp"] / denominator if denominator else 0.0
        )
    top20 = sorted(ranked_groups, reverse=True)[:20]
    top20_hits = sum(hit for _score, hit in top20)
    return {
        "split": split,
        "evaluated_company_cutoffs": evaluated,
        "top1_accuracy": top1 / evaluated if evaluated else None,
        f"top{top_k}_accuracy": topk / evaluated if evaluated else None,
        "mean_reciprocal_rank": (
            reciprocal_rank / evaluated if evaluated else None
        ),
        "brier_score": (
            sum(brier_terms) / len(brier_terms) if brier_terms else None
        ),
        "macro_f1_top1": (
            sum(macro_f1_values) / len(macro_f1_values)
            if macro_f1_values
            else None
        ),
        "precision_at_20": top20_hits / len(top20) if top20 else None,
        "recall_at_20": top20_hits / evaluated if evaluated else None,
        "company_type_slices": {
            company_type: {
                "evaluated": counts["evaluated"],
                "top1_accuracy": counts["top1"] / counts["evaluated"],
                f"top{top_k}_accuracy": counts["topk"] / counts["evaluated"],
            }
            for company_type, counts in sorted(type_counts.items())
        },
    }


def write_dataset(path: str | Path, dataset: HistoricalDataset) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dataset.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "ALLOWED_LABELS",
    "CompanyPartition",
    "CoverageArtifact",
    "CoverageAudit",
    "DEFAULT_ROLE_FAMILIES",
    "HistoricalDataset",
    "HistoricalTrainingRow",
    "LogisticModel",
    "RuleRoleModel",
    "SIGNAL_ROLE_PRIORS",
    "build_company_month_rows",
    "dataset_summary",
    "deterministic_company_split",
    "evaluate_role_ranker",
    "evidence_available_at",
    "fit_logistic_regression",
    "job_identity",
    "make_dataset",
    "monthly_cutoffs_before",
    "stable_company_id",
    "validate_company_partitions",
    "validate_rows",
    "write_dataset",
]
