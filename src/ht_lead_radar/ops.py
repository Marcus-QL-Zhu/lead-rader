"""Operational safety, compliance, audit, backup, and monitoring helpers.

This module deliberately has no network or subprocess dependency.  It is the
single reusable policy boundary for daily scans, interactive market scans,
candidate-float analysis, deep research, and exports.

Only calls to explicit store/backup methods have filesystem side effects;
importing the module is side-effect free.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import statistics
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UTC = timezone.utc
AUDIT_ACTIONS = frozenset({"run", "view", "export", "modify"})
ENTRY_POINTS = frozenset(
    {
        "daily_scan",
        "market_scan",
        "candidate_float",
        "deep_research",
        "view",
        "export",
    }
)
ENTITY_TYPES = frozenset({"company", "person"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _as_utc(parsed)


def _normalise_name(value: str) -> str:
    normalised = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[\s\-_·•,，.。()（）\[\]【】]+", "", normalised)


# ---------------------------------------------------------------------------
# Suppression / opt-out


@dataclass(frozen=True)
class SuppressionEntry:
    entity_type: str
    canonical_name: str
    reason: str
    aliases: tuple[str, ...] = ()
    added_at: str | None = None
    expires_at: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        if self.entity_type not in ENTITY_TYPES:
            raise ValueError(f"unsupported entity_type: {self.entity_type}")
        if not self.canonical_name.strip():
            raise ValueError("canonical_name is required")
        if not self.reason.strip():
            raise ValueError("suppression reason is required")

    def active(self, at: datetime | None = None) -> bool:
        expiry = _parse_time(self.expires_at)
        return expiry is None or _as_utc(at or _utc_now()) < expiry

    def keys(self) -> frozenset[str]:
        return frozenset(
            _normalise_name(value)
            for value in (self.canonical_name, *self.aliases)
            if value.strip()
        )


@dataclass(frozen=True)
class SuppressionDecision:
    entry_point: str
    entity_type: str
    queried_name: str
    suppressed: bool
    canonical_name: str | None = None
    reason: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SuppressedEntity(PermissionError):
    """Raised when an entity opted out of processing."""

    def __init__(self, decision: SuppressionDecision):
        self.decision = decision
        super().__init__(
            f"{decision.entity_type} is suppressed at {decision.entry_point}: "
            f"{decision.canonical_name or decision.queried_name}"
        )


class SuppressionRegistry:
    """One policy gate shared by every product entry point.

    Matching is conservative: NFKC/case/spacing/punctuation are normalised,
    while abbreviations and legal-company-name variants must be listed as
    aliases.  This avoids suppressing unrelated entities with similar names.
    """

    def __init__(
        self,
        entries: Iterable[SuppressionEntry] = (),
        *,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self._entries = tuple(entries)
        self.clock = clock

    @classmethod
    def from_json(
        cls,
        path: str | Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> "SuppressionRegistry":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("suppression file must be a JSON object")
        entries: list[SuppressionEntry] = []
        for collection, entity_type in (
            ("companies", "company"),
            ("people", "person"),
        ):
            values = payload.get(collection, [])
            if not isinstance(values, list):
                raise ValueError(f"{collection} must be a list")
            for item in values:
                if not isinstance(item, Mapping):
                    raise ValueError(f"{collection} entries must be objects")
                aliases = item.get("aliases", [])
                if not isinstance(aliases, list):
                    raise ValueError("aliases must be a list")
                entries.append(
                    SuppressionEntry(
                        entity_type=entity_type,
                        canonical_name=str(item.get("name", "")),
                        aliases=tuple(str(value) for value in aliases),
                        reason=str(item.get("reason", "")),
                        added_at=(
                            str(item["added_at"])
                            if item.get("added_at") is not None
                            else None
                        ),
                        expires_at=(
                            str(item["expires_at"])
                            if item.get("expires_at") is not None
                            else None
                        ),
                        source=(
                            str(item["source"])
                            if item.get("source") is not None
                            else None
                        ),
                    )
                )
        return cls(entries, clock=clock)

    def check(
        self,
        entry_point: str,
        entity_type: str,
        name: str,
        *,
        aliases: Iterable[str] = (),
    ) -> SuppressionDecision:
        if entry_point not in ENTRY_POINTS:
            raise ValueError(f"unsupported entry point: {entry_point}")
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"unsupported entity type: {entity_type}")
        candidates = {
            _normalise_name(value)
            for value in (name, *aliases)
            if str(value).strip()
        }
        for entry in self._entries:
            if (
                entry.entity_type == entity_type
                and entry.active(self.clock())
                and candidates.intersection(entry.keys())
            ):
                return SuppressionDecision(
                    entry_point=entry_point,
                    entity_type=entity_type,
                    queried_name=name,
                    suppressed=True,
                    canonical_name=entry.canonical_name,
                    reason=entry.reason,
                    expires_at=entry.expires_at,
                )
        return SuppressionDecision(
            entry_point=entry_point,
            entity_type=entity_type,
            queried_name=name,
            suppressed=False,
        )

    def check_company(
        self,
        entry_point: str,
        name: str,
        *,
        aliases: Iterable[str] = (),
    ) -> SuppressionDecision:
        return self.check(entry_point, "company", name, aliases=aliases)

    def check_person(
        self,
        entry_point: str,
        name: str,
        *,
        aliases: Iterable[str] = (),
    ) -> SuppressionDecision:
        return self.check(entry_point, "person", name, aliases=aliases)

    def enforce(
        self,
        entry_point: str,
        *,
        company: str | None = None,
        people: Iterable[str] = (),
    ) -> tuple[SuppressionDecision, ...]:
        """Apply the same checks before collection, research, view, or export."""

        decisions: list[SuppressionDecision] = []
        if company:
            decisions.append(self.check_company(entry_point, company))
        decisions.extend(
            self.check_person(entry_point, person)
            for person in people
            if person.strip()
        )
        for decision in decisions:
            if decision.suppressed:
                raise SuppressedEntity(decision)
        return tuple(decisions)


# ---------------------------------------------------------------------------
# Structured audit log


class AuditPolicyError(ValueError):
    """Unsafe audit metadata was rejected before it reached SQLite."""


_FORBIDDEN_AUDIT_KEY_PARTS = (
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "token",
    "credential",
    "private_key",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "raw_text",
    "document_text",
    "query_text",
    "response_text",
    "attachment",
    "file_bytes",
    "raw_resume",
    "resume_text",
    "resume_file",
    "candidate",
    "candidate_profile",
    "curriculum_vitae",
    "cv_text",
    "简历",
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _safe_audit_value(value: Any, path: str = "metadata") -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 4000:
            raise AuditPolicyError(f"audit field is too long: {path}")
        if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
            raise AuditPolicyError(f"secret-like value rejected at {path}")
        return value
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            folded = key.casefold()
            if any(part in folded for part in _FORBIDDEN_AUDIT_KEY_PARTS):
                raise AuditPolicyError(f"forbidden audit field: {path}.{key}")
            output[key] = _safe_audit_value(item, f"{path}.{key}")
        return output
    if isinstance(value, (list, tuple)):
        if len(value) > 1000:
            raise AuditPolicyError(f"audit list is too long: {path}")
        return [
            _safe_audit_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise AuditPolicyError(
        f"unsupported audit value at {path}: {type(value).__name__}"
    )


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    occurred_at: str
    actor: str
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    run_id: str | None
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditLog:
    """Append-only structured audit events.

    The API only admits four user-impacting operations: run, view, export, and
    modify.  Metadata is validated before INSERT and rejects credentials,
    tokens, cookies, raw resumes, and candidate profiles.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    run_id TEXT,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS audit_events_time
                ON audit_events(occurred_at DESC, event_id DESC)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def record(
        self,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str = "success",
        run_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        if action not in AUDIT_ACTIONS:
            raise ValueError(f"unsupported audit action: {action}")
        for label, value in (
            ("actor", actor),
            ("resource_type", resource_type),
            ("resource_id", resource_id),
            ("outcome", outcome),
        ):
            if not value.strip():
                raise ValueError(f"{label} is required")
            _safe_audit_value(value, label)
        if run_id is not None:
            _safe_audit_value(run_id, "run_id")
        safe_metadata = _safe_audit_value(dict(metadata or {}))
        event = AuditEvent(
            event_id="audit_" + uuid.uuid4().hex,
            occurred_at=_iso(self.clock()),
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            run_id=run_id,
            metadata=safe_metadata,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    event_id, occurred_at, actor, action, resource_type,
                    resource_id, outcome, run_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.occurred_at,
                    event.actor,
                    event.action,
                    event.resource_type,
                    event.resource_id,
                    event.outcome,
                    event.run_id,
                    json.dumps(
                        event.metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
        return event

    def query(
        self,
        *,
        action: str | None = None,
        resource_type: str | None = None,
        run_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        if limit <= 0 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (
            ("action", action),
            ("resource_type", resource_type),
            ("run_id", run_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if since is not None:
            clauses.append("occurred_at >= ?")
            parameters.append(_iso(since))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM audit_events{where}
                ORDER BY occurred_at DESC, rowid DESC LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
        return [_audit_event_from_row(row) for row in rows]

    def export_jsonl(
        self,
        target: str | Path,
        *,
        allowed_root: str | Path,
        limit: int = 10_000,
    ) -> Path:
        destination = _validated_output_path(
            target,
            allowed_root=allowed_root,
            allowed_suffixes={".jsonl"},
            overwrite=False,
        )
        events = reversed(self.query(limit=limit))
        with destination.open("x", encoding="utf-8", newline="\n") as output:
            for event in events:
                output.write(
                    json.dumps(
                        event.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        return destination


def _audit_event_from_row(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        event_id=row["event_id"],
        occurred_at=row["occurred_at"],
        actor=row["actor"],
        action=row["action"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        outcome=row["outcome"],
        run_id=row["run_id"],
        metadata=json.loads(row["metadata_json"]),
    )


# ---------------------------------------------------------------------------
# Recoverable SQLite backup


@dataclass(frozen=True)
class BackupResult:
    source: str
    target: str
    pages: int
    integrity_check: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validated_output_path(
    target: str | Path,
    *,
    allowed_root: str | Path,
    allowed_suffixes: set[str],
    overwrite: bool,
) -> Path:
    root = Path(allowed_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("allowed_root must be an existing directory")
    destination = Path(target).expanduser()
    if not destination.is_absolute():
        destination = root / destination
    destination = destination.resolve(strict=False)
    if not _is_within(destination, root):
        raise ValueError("target must stay inside allowed_root")
    if destination.parent != root and not destination.parent.exists():
        raise ValueError("target parent must already exist")
    if not destination.parent.is_dir():
        raise ValueError("target parent is not a directory")
    if destination.suffix.casefold() not in allowed_suffixes:
        raise ValueError(
            "target suffix must be one of " + ", ".join(sorted(allowed_suffixes))
        )
    if destination.exists():
        if destination.is_symlink():
            raise ValueError("target may not be a symlink")
        if not overwrite:
            raise FileExistsError(destination)
    return destination


def backup_sqlite(
    source: str | Path,
    target: str | Path,
    *,
    allowed_root: str | Path,
    overwrite: bool = False,
    clock: Callable[[], datetime] = _utc_now,
) -> BackupResult:
    """Create a consistent SQLite backup through SQLite's online backup API.

    ``allowed_root`` is mandatory.  Both resolved paths are checked before any
    write; the destination must have a database suffix and must not alias the
    source.  A temporary database is integrity-checked before atomic placement.
    Existing backups are preserved unless the caller explicitly sets
    ``overwrite=True``.
    """

    source_path = Path(source).expanduser().resolve(strict=True)
    if not source_path.is_file() or source_path.is_symlink():
        raise ValueError("source must be a regular SQLite file")
    destination = _validated_output_path(
        target,
        allowed_root=allowed_root,
        allowed_suffixes={".db", ".sqlite", ".sqlite3"},
        overwrite=overwrite,
    )
    if source_path == destination:
        raise ValueError("backup target must differ from source")

    temp = destination.parent / (
        f".{destination.name}.{uuid.uuid4().hex}.backup-tmp"
    )
    source_connection: sqlite3.Connection | None = None
    target_connection: sqlite3.Connection | None = None
    integrity = ""
    pages = 0
    try:
        source_uri = source_path.as_uri() + "?mode=ro"
        source_connection = sqlite3.connect(source_uri, uri=True, timeout=30)
        target_connection = sqlite3.connect(temp, timeout=30)
        source_connection.backup(target_connection)
        pages = int(
            target_connection.execute("PRAGMA page_count").fetchone()[0]
        )
        integrity = str(
            target_connection.execute("PRAGMA integrity_check").fetchone()[0]
        )
        if integrity.casefold() != "ok":
            raise sqlite3.DatabaseError(
                f"backup integrity check failed: {integrity}"
            )
        target_connection.close()
        target_connection = None
        source_connection.close()
        source_connection = None
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)
        os.replace(temp, destination)
    finally:
        if target_connection is not None:
            target_connection.close()
        if source_connection is not None:
            source_connection.close()
        if temp.exists():
            temp.unlink()
    return BackupResult(
        source=str(source_path),
        target=str(destination),
        pages=pages,
        integrity_check=integrity,
        created_at=_iso(clock()),
    )


# ---------------------------------------------------------------------------
# Operational observations and daily monitoring


@dataclass(frozen=True)
class RunMetric:
    run_id: str
    recorded_at: str
    status: str
    result_count: int
    metaso_points: float = 0.0


@dataclass(frozen=True)
class SourceMetric:
    run_id: str
    source_id: str
    recorded_at: str
    ok: bool
    yield_count: int


class OpsMetricsStore:
    """Minimal operational metrics; never stores query text or candidate data."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ops_run_metrics (
                    run_id TEXT PRIMARY KEY,
                    recorded_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_count INTEGER NOT NULL,
                    metaso_points REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ops_source_metrics (
                    run_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    yield_count INTEGER NOT NULL,
                    PRIMARY KEY (run_id, source_id)
                );
                CREATE INDEX IF NOT EXISTS ops_run_metrics_time
                    ON ops_run_metrics(recorded_at DESC);
                CREATE INDEX IF NOT EXISTS ops_source_metrics_source_time
                    ON ops_source_metrics(source_id, recorded_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def record_run(
        self,
        run_id: str,
        *,
        recorded_at: datetime,
        status: str,
        result_count: int,
        metaso_points: float = 0,
    ) -> RunMetric:
        if not run_id.strip() or not status.strip():
            raise ValueError("run_id and status are required")
        if result_count < 0 or metaso_points < 0:
            raise ValueError("operational metrics must be non-negative")
        metric = RunMetric(
            run_id=run_id,
            recorded_at=_iso(recorded_at),
            status=status,
            result_count=int(result_count),
            metaso_points=float(metaso_points),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ops_run_metrics (
                    run_id, recorded_at, status, result_count, metaso_points
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    recorded_at=excluded.recorded_at,
                    status=excluded.status,
                    result_count=excluded.result_count,
                    metaso_points=excluded.metaso_points
                """,
                (
                    metric.run_id,
                    metric.recorded_at,
                    metric.status,
                    metric.result_count,
                    metric.metaso_points,
                ),
            )
        return metric

    def record_source(
        self,
        run_id: str,
        source_id: str,
        *,
        recorded_at: datetime,
        ok: bool,
        yield_count: int,
    ) -> SourceMetric:
        if not run_id.strip() or not source_id.strip():
            raise ValueError("run_id and source_id are required")
        if yield_count < 0:
            raise ValueError("yield_count must be non-negative")
        metric = SourceMetric(
            run_id=run_id,
            source_id=source_id,
            recorded_at=_iso(recorded_at),
            ok=bool(ok),
            yield_count=int(yield_count),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ops_source_metrics (
                    run_id, source_id, recorded_at, ok, yield_count
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source_id) DO UPDATE SET
                    recorded_at=excluded.recorded_at,
                    ok=excluded.ok,
                    yield_count=excluded.yield_count
                """,
                (
                    metric.run_id,
                    metric.source_id,
                    metric.recorded_at,
                    int(metric.ok),
                    metric.yield_count,
                ),
            )
        return metric


@dataclass(frozen=True)
class MonitorIssue:
    code: str
    severity: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MonitorReport:
    generated_at: str
    status: str
    suggested_exit_code: int
    issues: tuple[MonitorIssue, ...]
    metrics: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "suggested_exit_code": self.suggested_exit_code,
            "issues": [issue.to_dict() for issue in self.issues],
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class MonitorConfig:
    expected_cron_expression: str = "0 5 * * *"
    max_run_age_hours: float = 30
    source_failure_threshold: int = 3
    zero_yield_threshold: int = 3
    daily_metaso_budget: float = 500
    metaso_warning_ratio: float = 0.8
    minimum_result_count: int = 1
    history_size: int = 14
    timezone_name: str = "Asia/Shanghai"

    def __post_init__(self) -> None:
        if self.max_run_age_hours <= 0:
            raise ValueError("max_run_age_hours must be positive")
        if self.source_failure_threshold <= 0 or self.zero_yield_threshold <= 0:
            raise ValueError("failure/yield thresholds must be positive")
        if self.daily_metaso_budget <= 0:
            raise ValueError("daily_metaso_budget must be positive")
        if not 0 < self.metaso_warning_ratio <= 1:
            raise ValueError("metaso_warning_ratio must be in (0, 1]")


def inspect_cron_entries(
    entries: Iterable[str],
    *,
    command_marker: str,
    expected_expression: str = "0 5 * * *",
) -> dict[str, Any]:
    """Inspect supplied ``crontab -l`` lines without invoking a subprocess."""

    if not command_marker.strip():
        raise ValueError("command_marker is required")
    matches: list[str] = []
    conflicts: list[str] = []
    for raw in entries:
        line = raw.strip()
        if not line or line.startswith("#") or command_marker not in line:
            continue
        fields = line.split()
        expression = " ".join(fields[:5]) if len(fields) >= 6 else ""
        if expression == expected_expression:
            matches.append(line)
        else:
            conflicts.append(line)
    return {
        "configured": bool(matches),
        "expected_expression": expected_expression,
        "matching_entries": matches,
        "conflicting_entries": conflicts,
    }


def _open_readonly_database(path: str | Path) -> sqlite3.Connection | None:
    database = Path(path).expanduser().resolve()
    if not database.exists() or not database.is_file():
        return None
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def build_daily_monitoring_report(
    *,
    runtime_db: str | Path | None = None,
    source_health_db: str | Path | None = None,
    ops_metrics_db: str | Path | None = None,
    cron_entries: Iterable[str] = (),
    cron_command_marker: str | None = None,
    now: datetime | None = None,
    config: MonitorConfig = MonitorConfig(),
) -> MonitorReport:
    """Aggregate cron, runtime, sources, budget, yield, and checkpoint health."""

    current = _as_utc(now or _utc_now())
    issues: list[MonitorIssue] = []
    metrics: dict[str, Any] = {}

    if cron_command_marker is not None:
        cron = inspect_cron_entries(
            cron_entries,
            command_marker=cron_command_marker,
            expected_expression=config.expected_cron_expression,
        )
        metrics["cron"] = cron
        if not cron["configured"]:
            issues.append(
                MonitorIssue(
                    "cron_missing_or_wrong_time",
                    "critical",
                    "Daily cron entry is missing or does not use the expected schedule.",
                    {
                        "expected_expression": config.expected_cron_expression,
                        "conflicting_entries": cron["conflicting_entries"],
                    },
                )
            )

    _append_runtime_health(
        runtime_db, current=current, config=config, issues=issues, metrics=metrics
    )
    _append_source_health(
        source_health_db, config=config, issues=issues, metrics=metrics
    )
    _append_metrics_health(
        ops_metrics_db,
        current=current,
        config=config,
        issues=issues,
        metrics=metrics,
    )

    severity_rank = {"ok": 0, "warning": 1, "critical": 2}
    highest = max(
        (severity_rank.get(issue.severity, 1) for issue in issues), default=0
    )
    status = ("ok", "warning", "critical")[highest]
    return MonitorReport(
        generated_at=_iso(current),
        status=status,
        suggested_exit_code=highest,
        issues=tuple(issues),
        metrics=metrics,
    )


def _append_runtime_health(
    path: str | Path | None,
    *,
    current: datetime,
    config: MonitorConfig,
    issues: list[MonitorIssue],
    metrics: dict[str, Any],
) -> None:
    if path is None:
        metrics["runtime"] = {"available": False, "reason": "not_configured"}
        return
    connection = _open_readonly_database(path)
    if connection is None:
        metrics["runtime"] = {"available": False, "reason": "database_missing"}
        issues.append(
            MonitorIssue(
                "runtime_database_missing",
                "critical",
                "Runtime database is missing.",
                {"path": str(path)},
            )
        )
        return
    try:
        if not _table_exists(connection, "pipeline_runs"):
            metrics["runtime"] = {"available": False, "reason": "schema_missing"}
            issues.append(
                MonitorIssue(
                    "runtime_schema_missing",
                    "critical",
                    "Runtime checkpoint schema is missing.",
                )
            )
            return
        latest = connection.execute(
            """
            SELECT * FROM pipeline_runs
            ORDER BY created_at DESC, run_id DESC LIMIT 1
            """
        ).fetchone()
        if latest is None:
            metrics["runtime"] = {"available": True, "latest_run": None}
            issues.append(
                MonitorIssue(
                    "no_pipeline_runs",
                    "critical",
                    "No pipeline run has been recorded.",
                )
            )
        else:
            timestamp = (
                _parse_time(latest["completed_at"])
                or _parse_time(latest["updated_at"])
                or _parse_time(latest["created_at"])
            )
            age_hours = (
                (current - timestamp).total_seconds() / 3600
                if timestamp is not None
                else None
            )
            latest_metric = {
                "run_id": latest["run_id"],
                "status": latest["status"],
                "current_stage": latest["current_stage"],
                "updated_at": latest["updated_at"],
                "age_hours": age_hours,
            }
            metrics["runtime"] = {
                "available": True,
                "latest_run": latest_metric,
            }
            if latest["status"] == "failed":
                issues.append(
                    MonitorIssue(
                        "latest_run_failed",
                        "critical",
                        "The latest pipeline run failed.",
                        latest_metric,
                    )
                )
            elif latest["status"] not in {"completed", "running", "pending"}:
                issues.append(
                    MonitorIssue(
                        "latest_run_unknown_status",
                        "warning",
                        "The latest run has an unrecognised status.",
                        latest_metric,
                    )
                )
            if age_hours is None or age_hours > config.max_run_age_hours:
                issues.append(
                    MonitorIssue(
                        "latest_run_stale",
                        "critical",
                        "The latest pipeline activity is too old.",
                        {
                            "age_hours": age_hours,
                            "maximum_hours": config.max_run_age_hours,
                        },
                    )
                )

        if _table_exists(connection, "pipeline_checkpoints"):
            failed = connection.execute(
                """
                SELECT checkpoint.run_id, checkpoint.stage, checkpoint.attempt,
                       checkpoint.error, checkpoint.completed_at
                FROM pipeline_checkpoints AS checkpoint
                JOIN (
                    SELECT run_id, stage, MAX(attempt) AS attempt
                    FROM pipeline_checkpoints
                    GROUP BY run_id, stage
                ) AS latest
                  ON latest.run_id = checkpoint.run_id
                 AND latest.stage = checkpoint.stage
                 AND latest.attempt = checkpoint.attempt
                WHERE checkpoint.status = 'failed'
                ORDER BY checkpoint.completed_at DESC
                LIMIT 100
                """
            ).fetchall()
            metrics["failed_checkpoints"] = [dict(row) for row in failed]
            for row in failed:
                issues.append(
                    MonitorIssue(
                        "checkpoint_failed",
                        "critical",
                        "A latest stage checkpoint is failed and has not recovered.",
                        {
                            "run_id": row["run_id"],
                            "stage": row["stage"],
                            "attempt": row["attempt"],
                        },
                    )
                )
    finally:
        connection.close()


def _append_source_health(
    path: str | Path | None,
    *,
    config: MonitorConfig,
    issues: list[MonitorIssue],
    metrics: dict[str, Any],
) -> None:
    if path is None:
        metrics["sources"] = {"available": False, "reason": "not_configured"}
        return
    connection = _open_readonly_database(path)
    if connection is None:
        metrics["sources"] = {"available": False, "reason": "database_missing"}
        issues.append(
            MonitorIssue(
                "source_health_database_missing",
                "warning",
                "Source-health database is missing.",
                {"path": str(path)},
            )
        )
        return
    try:
        if not _table_exists(connection, "source_health") and _table_exists(
            connection, "source_pack_source_runs"
        ):
            rows = connection.execute(
                """
                SELECT *
                FROM source_pack_source_runs
                ORDER BY source_id, id DESC
                """
            ).fetchall()
            history: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                history.setdefault(str(row["source_id"]), []).append(row)
            latest_rows = [items[0] for items in history.values() if items]
            metrics["sources"] = {
                "available": True,
                "schema": "source_pack_source_runs",
                "count": len(latest_rows),
                "unhealthy": [],
            }
            for row in latest_rows:
                source_id = str(row["source_id"])
                source_history = history[source_id]
                consecutive_failures = 0
                for historical in source_history:
                    if str(historical["status"]) != "error":
                        break
                    consecutive_failures += 1
                consecutive_empty_discoveries = 0
                for historical in source_history:
                    if (
                        str(historical["status"]) == "ok"
                        and int(historical["discovered_count"]) == 0
                    ):
                        consecutive_empty_discoveries += 1
                    else:
                        break
                last_success = next(
                    (
                        str(historical["finished_at"])
                        for historical in source_history
                        if str(historical["status"]) in {"ok", "not_modified"}
                    ),
                    None,
                )
                last_new_item = next(
                    (
                        str(historical["finished_at"])
                        for historical in source_history
                        if int(historical["evidence_count"]) > 0
                    ),
                    None,
                )
                detail = {
                    "source_id": source_id,
                    "topic": str(row["topic"]),
                    "status": str(row["status"]),
                    "consecutive_failures": consecutive_failures,
                    "discovered_count": int(row["discovered_count"]),
                    "consecutive_empty_discoveries": consecutive_empty_discoveries,
                    "parse_yield": int(row["observation_count"]),
                    "evidence_yield": int(row["evidence_count"]),
                    "last_success": last_success,
                    "last_new_item": last_new_item,
                    "error": str(row["error"]),
                }
                if consecutive_failures >= config.source_failure_threshold:
                    metrics["sources"]["unhealthy"].append(detail)
                    issues.append(
                        MonitorIssue(
                            "source_consecutive_failures",
                            "critical",
                            "A source exceeded the consecutive-failure threshold.",
                            detail,
                        )
                    )
                elif consecutive_empty_discoveries >= config.zero_yield_threshold:
                    issues.append(
                        MonitorIssue(
                            "source_consecutive_empty_discovery",
                            "warning",
                            "A source repeatedly fetched successfully but discovered no list items.",
                            detail,
                        )
                    )
            return
        if not _table_exists(connection, "source_health"):
            metrics["sources"] = {"available": False, "reason": "schema_missing"}
            issues.append(
                MonitorIssue(
                    "source_health_schema_missing",
                    "warning",
                    "Source-health schema is missing.",
                )
            )
            return
        rows = connection.execute(
            "SELECT * FROM source_health ORDER BY source_id"
        ).fetchall()
        metrics["sources"] = {
            "available": True,
            "count": len(rows),
            "unhealthy": [],
        }
        for row in rows:
            detail = {
                "source_id": row["source_id"],
                "consecutive_failures": row["consecutive_failures"],
                "parse_yield": row["parse_yield"],
                "last_success": row["last_success"],
                "last_new_item": row["last_new_item"],
            }
            if row["consecutive_failures"] >= config.source_failure_threshold:
                metrics["sources"]["unhealthy"].append(detail)
                issues.append(
                    MonitorIssue(
                        "source_consecutive_failures",
                        "critical",
                        "A source exceeded the consecutive-failure threshold.",
                        detail,
                    )
                )
            elif row["parse_yield"] == 0 and row["last_success"] is not None:
                issues.append(
                    MonitorIssue(
                        "source_latest_zero_output",
                        "warning",
                        "A successful source fetch produced no parsable items.",
                        detail,
                    )
                )
    finally:
        connection.close()


def _append_metrics_health(
    path: str | Path | None,
    *,
    current: datetime,
    config: MonitorConfig,
    issues: list[MonitorIssue],
    metrics: dict[str, Any],
) -> None:
    if path is None:
        metrics["operations"] = {"available": False, "reason": "not_configured"}
        return
    connection = _open_readonly_database(path)
    if connection is None:
        metrics["operations"] = {"available": False, "reason": "database_missing"}
        issues.append(
            MonitorIssue(
                "ops_metrics_database_missing",
                "warning",
                "Operational metrics database is missing.",
                {"path": str(path)},
            )
        )
        return
    try:
        if not _table_exists(connection, "ops_run_metrics"):
            metrics["operations"] = {"available": False, "reason": "schema_missing"}
            return
        rows = connection.execute(
            """
            SELECT * FROM ops_run_metrics
            ORDER BY recorded_at DESC LIMIT ?
            """,
            (config.history_size + 1,),
        ).fetchall()
        latest = dict(rows[0]) if rows else None
        metrics["operations"] = {
            "available": True,
            "latest": latest,
            "history_count": len(rows),
        }
        if latest is not None:
            count = int(latest["result_count"])
            if count == 0:
                issues.append(
                    MonitorIssue(
                        "zero_results",
                        "critical",
                        "The latest run produced zero company results.",
                        {"run_id": latest["run_id"], "result_count": count},
                    )
                )
            elif count < config.minimum_result_count:
                issues.append(
                    MonitorIssue(
                        "too_few_results",
                        "warning",
                        "The latest run is below the configured minimum result count.",
                        {
                            "run_id": latest["run_id"],
                            "result_count": count,
                            "minimum": config.minimum_result_count,
                        },
                    )
                )
            previous = [int(row["result_count"]) for row in rows[1:]]
            if len(previous) >= 3:
                median = statistics.median(previous)
                metrics["operations"]["historical_result_median"] = median
                if median >= 4 and count < median * 0.5:
                    issues.append(
                        MonitorIssue(
                            "result_count_drop",
                            "warning",
                            "Result count dropped below half of the recent median.",
                            {
                                "result_count": count,
                                "historical_median": median,
                            },
                        )
                    )

        try:
            local_tz = ZoneInfo(config.timezone_name)
        except ZoneInfoNotFoundError as error:
            raise ValueError(
                f"unknown monitoring timezone: {config.timezone_name}"
            ) from error
        local_day = current.astimezone(local_tz).date()
        today_points = 0.0
        all_usage = connection.execute(
            "SELECT recorded_at, metaso_points FROM ops_run_metrics"
        ).fetchall()
        for row in all_usage:
            recorded = _parse_time(row["recorded_at"])
            if recorded and recorded.astimezone(local_tz).date() == local_day:
                today_points += float(row["metaso_points"])
        ratio = today_points / config.daily_metaso_budget
        metrics["metaso_budget"] = {
            "used": today_points,
            "budget": config.daily_metaso_budget,
            "remaining": max(0.0, config.daily_metaso_budget - today_points),
            "usage_ratio": ratio,
            "local_day": local_day.isoformat(),
        }
        if ratio >= 1:
            issues.append(
                MonitorIssue(
                    "metaso_budget_exhausted",
                    "critical",
                    "Metaso daily points reached or exceeded the configured budget.",
                    metrics["metaso_budget"],
                )
            )
        elif ratio >= config.metaso_warning_ratio:
            issues.append(
                MonitorIssue(
                    "metaso_budget_high",
                    "warning",
                    "Metaso daily points are near the configured budget.",
                    metrics["metaso_budget"],
                )
            )

        if _table_exists(connection, "ops_source_metrics"):
            sources = connection.execute(
                "SELECT DISTINCT source_id FROM ops_source_metrics"
            ).fetchall()
            zero_streaks: dict[str, int] = {}
            for source in sources:
                observations = connection.execute(
                    """
                    SELECT ok, yield_count FROM ops_source_metrics
                    WHERE source_id = ?
                    ORDER BY recorded_at DESC LIMIT ?
                    """,
                    (source["source_id"], config.zero_yield_threshold),
                ).fetchall()
                streak = 0
                for observation in observations:
                    if observation["ok"] and observation["yield_count"] == 0:
                        streak += 1
                    else:
                        break
                zero_streaks[source["source_id"]] = streak
                if streak >= config.zero_yield_threshold:
                    issues.append(
                        MonitorIssue(
                            "source_consecutive_zero_output",
                            "critical",
                            "A source repeatedly succeeded but produced zero items.",
                            {
                                "source_id": source["source_id"],
                                "zero_yield_streak": streak,
                            },
                        )
                    )
            metrics["source_zero_yield_streaks"] = zero_streaks
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Public-person freshness


@dataclass(frozen=True)
class PublicPersonFact:
    person_id: str
    fact_type: str
    observed_at: str | None
    source_published_at: str | None = None


@dataclass(frozen=True)
class FreshnessResult:
    person_id: str
    fact_type: str
    status: str
    reference_time: str | None
    age_days: float | None
    expires_at: str | None
    requires_refresh: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PublicPersonFreshnessPolicy:
    """Maximum ages reflect how quickly each public professional fact changes."""

    max_age_days: Mapping[str, int | None] = field(
        default_factory=lambda: {
            "current_role": 90,
            "board_role": 180,
            "investment_commentary": 180,
            "investment_focus": 365,
            "official_bio": 365,
            "historical_employment": None,
        }
    )
    unknown_fact_max_age_days: int = 180

    def assess(
        self,
        fact: PublicPersonFact,
        *,
        as_of: datetime | None = None,
    ) -> FreshnessResult:
        current = _as_utc(as_of or _utc_now())
        reference = (
            _parse_time(fact.source_published_at)
            or _parse_time(fact.observed_at)
        )
        if reference is None:
            return FreshnessResult(
                person_id=fact.person_id,
                fact_type=fact.fact_type,
                status="unknown",
                reference_time=None,
                age_days=None,
                expires_at=None,
                requires_refresh=True,
            )
        maximum = self.max_age_days.get(
            fact.fact_type, self.unknown_fact_max_age_days
        )
        age = max(0.0, (current - reference).total_seconds() / 86400)
        if maximum is None:
            return FreshnessResult(
                person_id=fact.person_id,
                fact_type=fact.fact_type,
                status="historical",
                reference_time=_iso(reference),
                age_days=age,
                expires_at=None,
                requires_refresh=False,
            )
        expiry = reference + timedelta(days=maximum)
        stale = current >= expiry
        return FreshnessResult(
            person_id=fact.person_id,
            fact_type=fact.fact_type,
            status="expired" if stale else "fresh",
            reference_time=_iso(reference),
            age_days=age,
            expires_at=_iso(expiry),
            requires_refresh=stale,
        )


# ---------------------------------------------------------------------------
# Collection compliance


PRIVATE_CONTACT_FIELDS = frozenset(
    {
        "personal_email",
        "private_email",
        "email",
        "personal_phone",
        "private_phone",
        "mobile_phone",
        "phone",
        "home_address",
        "wechat_id",
        "qq_id",
        "id_card",
    }
)
SAFE_PUBLIC_FIELDS = frozenset(
    {
        "url",
        "title",
        "published_at",
        "company_name",
        "person_name",
        "professional_role",
        "public_statement",
        "official_profile_url",
        "institution_name",
        "event_type",
        "source_name",
    }
)
ACCESS_BASES = frozenset(
    {"public_web", "official_api", "licensed_feed", "user_authorized"}
)
LICENSE_CATEGORIES = frozenset(
    {"open", "licensed", "public_fact_only", "unknown", "restricted"}
)


@dataclass(frozen=True)
class SourcePermission:
    access_basis: str
    publicly_accessible: bool
    authorized: bool = False
    bypasses_access_control: bool = False
    robots_allowed: bool = True
    terms_allow_collection: bool = True
    license_category: str = "unknown"
    full_text_retention_allowed: bool = False
    max_retention_days: int = 30

    def __post_init__(self) -> None:
        if self.access_basis not in ACCESS_BASES:
            raise ValueError(f"unsupported access basis: {self.access_basis}")
        if self.license_category not in LICENSE_CATEGORIES:
            raise ValueError(
                f"unsupported license category: {self.license_category}"
            )
        if self.max_retention_days < 0:
            raise ValueError("max_retention_days must be non-negative")


@dataclass(frozen=True)
class CollectionIntent:
    requested_fields: tuple[str, ...]
    retain_full_text: bool = False
    requested_retention_days: int = 0

    def __post_init__(self) -> None:
        if self.requested_retention_days < 0:
            raise ValueError("requested_retention_days must be non-negative")


@dataclass(frozen=True)
class ComplianceDecision:
    allowed: bool
    reasons: tuple[str, ...]
    collect_fields: tuple[str, ...]
    excluded_fields: tuple[str, ...]
    retention_mode: str
    retention_days: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CollectionPolicy:
    """Conservative OSINT collection boundary.

    It never authorises bypassing access controls, private contact collection,
    or body retention that the source permission does not allow.  Unknown or
    fact-only sources may still contribute URLs, provenance, short extracted
    facts, and hashes; their full body is not retained.
    """

    def evaluate(
        self,
        permission: SourcePermission,
        intent: CollectionIntent,
    ) -> ComplianceDecision:
        blocking: list[str] = []
        notices: list[str] = []
        if permission.bypasses_access_control:
            blocking.append("access_control_bypass_forbidden")
        if (
            not permission.publicly_accessible
            and not permission.authorized
        ):
            blocking.append("source_not_public_or_authorized")
        if permission.access_basis in {
            "official_api",
            "licensed_feed",
            "user_authorized",
        } and not permission.authorized:
            blocking.append("authorization_required")
        if not permission.robots_allowed:
            blocking.append("robots_policy_disallows_collection")
        if not permission.terms_allow_collection:
            blocking.append("source_terms_disallow_collection")
        if (
            permission.license_category == "restricted"
            and not permission.authorized
        ):
            blocking.append("restricted_source_without_permission")

        requested = tuple(dict.fromkeys(intent.requested_fields))
        excluded = tuple(
            field for field in requested if field in PRIVATE_CONTACT_FIELDS
        )
        if excluded:
            notices.append("private_contact_fields_excluded")
        collect = tuple(
            field
            for field in requested
            if field not in PRIVATE_CONTACT_FIELDS
            and (field in SAFE_PUBLIC_FIELDS or field.startswith("public_"))
        )
        unknown = tuple(
            field
            for field in requested
            if field not in PRIVATE_CONTACT_FIELDS and field not in collect
        )
        if unknown:
            notices.append("unapproved_fields_excluded")
            excluded = (*excluded, *unknown)

        full_text_allowed = (
            permission.full_text_retention_allowed
            and permission.license_category in {"open", "licensed"}
        )
        if intent.retain_full_text and full_text_allowed:
            retention_mode = "full_text"
        else:
            retention_mode = "metadata_and_extracted_facts"
            if intent.retain_full_text:
                notices.append("full_text_retention_downgraded")
        retention_days = min(
            intent.requested_retention_days,
            permission.max_retention_days,
        )
        if intent.requested_retention_days > permission.max_retention_days:
            notices.append("retention_period_capped")
        if retention_mode != "full_text":
            # The extracted facts/provenance record may persist, while fetched
            # body content remains checkpoint-temporary and is not archived.
            retention_days = 0

        return ComplianceDecision(
            allowed=not blocking,
            reasons=tuple((*blocking, *notices)),
            collect_fields=collect,
            excluded_fields=excluded,
            retention_mode=retention_mode,
            retention_days=retention_days,
        )

    def enforce(
        self,
        permission: SourcePermission,
        intent: CollectionIntent,
    ) -> ComplianceDecision:
        decision = self.evaluate(permission, intent)
        if not decision.allowed:
            raise PermissionError(
                "collection rejected: " + ", ".join(decision.reasons)
            )
        return decision
