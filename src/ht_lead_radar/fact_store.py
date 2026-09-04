"""SQLite persistence and clustering for the four-layer fact model.

The store is intentionally append-friendly:

* source snapshots and statements are immutable and content-addressed;
* entity resolution and event merge/split choices are reversible judgements;
* a business event is a projection over its supporting statements/documents;
* every lifecycle and slot projection change is retained in an audit table.

This is not an ORM.  Keeping the SQL explicit makes migrations and OpenClaw
deployment predictable and avoids introducing a second infrastructure stack.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .domain import (
    BusinessEvent,
    CanonicalEntity,
    EntityJudgement,
    EntityResolutionDecision,
    EventEvidence,
    EventLifecycle,
    EventLinkDecision,
    EventLinkType,
    EvidenceStance,
    IngestResult,
    SourceDocument,
    Statement,
    canonical_json,
    grade_rank,
    normalize_name,
    normalize_slots,
    normalize_timestamp,
    normalize_url,
    sha256_text,
    stable_id,
    utcnow,
)
from .sanitization import sanitize_tree, sanitize_url


SCHEMA_VERSION = 2
_PERSISTENCE_SANITIZER_VERSION = "3"


MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS source_documents (
        id TEXT PRIMARY KEY,
        source_name TEXT NOT NULL,
        source_url TEXT NOT NULL,
        normalized_url TEXT NOT NULL,
        url_hash TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        source_grade TEXT NOT NULL,
        source_record_id TEXT NOT NULL DEFAULT '',
        published_at TEXT,
        observed_at TEXT NOT NULL,
        language TEXT NOT NULL,
        independent_source_key TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        exact_duplicate_of_id TEXT REFERENCES source_documents(id),
        UNIQUE(normalized_url, content_hash)
    );
    CREATE INDEX IF NOT EXISTS source_documents_url_hash_idx
        ON source_documents(url_hash);
    CREATE INDEX IF NOT EXISTS source_documents_content_hash_idx
        ON source_documents(content_hash);
    CREATE INDEX IF NOT EXISTS source_documents_observed_idx
        ON source_documents(observed_at DESC);

    CREATE TABLE IF NOT EXISTS canonical_entities (
        id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        canonical_name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS canonical_entities_lookup_idx
        ON canonical_entities(entity_type, normalized_name);

    CREATE TABLE IF NOT EXISTS entity_aliases (
        entity_id TEXT NOT NULL REFERENCES canonical_entities(id) ON DELETE CASCADE,
        alias TEXT NOT NULL,
        normalized_alias TEXT NOT NULL,
        alias_type TEXT NOT NULL,
        source_document_id TEXT REFERENCES source_documents(id),
        created_at TEXT NOT NULL,
        PRIMARY KEY(entity_id, normalized_alias, alias_type)
    );
    CREATE INDEX IF NOT EXISTS entity_aliases_lookup_idx
        ON entity_aliases(normalized_alias);

    CREATE TABLE IF NOT EXISTS entity_resolution_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        left_entity_id TEXT NOT NULL REFERENCES canonical_entities(id),
        right_entity_id TEXT NOT NULL REFERENCES canonical_entities(id),
        judgement TEXT NOT NULL CHECK(
            judgement IN ('POSITIVE', 'NEGATIVE', 'UNSURE', 'NO_JUDGEMENT')
        ),
        reason TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL DEFAULT 'system',
        created_at TEXT NOT NULL,
        revoked_at TEXT,
        CHECK(left_entity_id <> right_entity_id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS entity_resolution_active_pair_idx
        ON entity_resolution_decisions(left_entity_id, right_entity_id)
        WHERE revoked_at IS NULL;

    CREATE TABLE IF NOT EXISTS statements (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL REFERENCES source_documents(id),
        predicate TEXT NOT NULL,
        subject_entity_id TEXT REFERENCES canonical_entities(id),
        object_entity_id TEXT REFERENCES canonical_entities(id),
        object_value_json TEXT,
        occurred_at TEXT,
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        quote TEXT NOT NULL DEFAULT '',
        slots_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS statements_document_idx
        ON statements(document_id);
    CREATE INDEX IF NOT EXISTS statements_subject_idx
        ON statements(subject_entity_id, predicate);

    CREATE TABLE IF NOT EXISTS business_events (
        id TEXT PRIMARY KEY,
        company_entity_id TEXT NOT NULL REFERENCES canonical_entities(id),
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        time_bucket TEXT NOT NULL,
        slots_json TEXT NOT NULL DEFAULT '{}',
        slot_fingerprint TEXT NOT NULL,
        lifecycle TEXT NOT NULL CHECK(
            lifecycle IN (
                'emerging', 'corroborated', 'developing', 'stale',
                'superseded', 'retracted', 'disputed'
            )
        ),
        lifecycle_mode TEXT NOT NULL DEFAULT 'auto'
            CHECK(lifecycle_mode IN ('auto', 'manual')),
        lifecycle_reason TEXT NOT NULL DEFAULT '',
        canonical_document_id TEXT REFERENCES source_documents(id),
        independent_source_count INTEGER NOT NULL DEFAULT 0,
        first_observed_at TEXT NOT NULL,
        last_observed_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS business_events_company_type_time_idx
        ON business_events(company_entity_id, event_type, occurred_at DESC);
    CREATE INDEX IF NOT EXISTS business_events_lifecycle_idx
        ON business_events(lifecycle, last_observed_at DESC);

    CREATE TABLE IF NOT EXISTS event_evidence (
        id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL REFERENCES business_events(id) ON DELETE CASCADE,
        document_id TEXT NOT NULL REFERENCES source_documents(id),
        statement_id TEXT REFERENCES statements(id),
        stance TEXT NOT NULL CHECK(stance IN ('supports', 'contradicts', 'retracts')),
        independent_source_key TEXT NOT NULL,
        source_grade TEXT NOT NULL,
        linked_at TEXT NOT NULL,
        UNIQUE(event_id, document_id, statement_id, stance)
    );
    CREATE INDEX IF NOT EXISTS event_evidence_event_idx
        ON event_evidence(event_id, stance);

    CREATE TABLE IF NOT EXISTS event_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL REFERENCES business_events(id) ON DELETE CASCADE,
        prior_occurred_at TEXT,
        new_occurred_at TEXT NOT NULL,
        prior_slots_json TEXT,
        new_slots_json TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS event_lifecycle_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL REFERENCES business_events(id) ON DELETE CASCADE,
        prior_lifecycle TEXT,
        new_lifecycle TEXT NOT NULL,
        reason TEXT NOT NULL,
        actor TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS event_link_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        left_event_id TEXT NOT NULL REFERENCES business_events(id),
        right_event_id TEXT NOT NULL REFERENCES business_events(id),
        link_type TEXT NOT NULL CHECK(link_type IN ('merge', 'split', 'supersedes')),
        judgement TEXT NOT NULL CHECK(
            judgement IN ('POSITIVE', 'NEGATIVE', 'UNSURE', 'NO_JUDGEMENT')
        ),
        reason TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL DEFAULT 'system',
        created_at TEXT NOT NULL,
        revoked_at TEXT,
        CHECK(left_event_id <> right_event_id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS event_link_active_pair_idx
        ON event_link_decisions(left_event_id, right_event_id, link_type)
        WHERE revoked_at IS NULL;
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_store_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
)


def _loads(value: str | None, default: Any) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_time_bucket(timestamp: str) -> str:
    parsed = _parse_datetime(timestamp)
    if parsed is None:
        return timestamp[:7] if len(timestamp) >= 7 else timestamp
    return parsed.strftime("%Y-%m")


def _merge_slots(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """Fill missing structured fields without overwriting conflicting claims."""

    output = dict(old)
    for key, value in new.items():
        if key not in output or output[key] in (None, "", [], {}):
            output[key] = value
        elif output[key] == value:
            continue
        # Conflicting claims remain in their source Statements.  The event
        # projection keeps the earlier canonical value rather than last-write
        # wins, which would destroy provenance.
    return normalize_slots(output)


class FactStore:
    """Content-addressed SQLite store for documents, claims and events."""

    def __init__(self, database: str | Path, *, stale_after_days: int = 180):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.stale_after_days = int(stale_after_days)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def migrate(self) -> None:
        with self._connect() as connection:
            for version, sql in enumerate(MIGRATIONS, start=1):
                applied = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?",
                    (version,),
                ).fetchone() if self._table_exists(connection, "schema_migrations") else None
                if applied:
                    continue
                connection.executescript(sql)
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, utcnow()),
                )
            self._sanitize_persisted_source_urls(connection)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _sanitize_persisted_source_urls(
        self, connection: sqlite3.Connection
    ) -> None:
        marker = connection.execute(
            "SELECT value FROM fact_store_metadata "
            "WHERE key='persistence_sanitizer_version'"
        ).fetchone()
        if marker and str(marker["value"]) == _PERSISTENCE_SANITIZER_VERSION:
            return
        rows = connection.execute(
            "SELECT id, source_url, normalized_url, content_hash, metadata_json "
            "FROM source_documents ORDER BY observed_at, id"
        ).fetchall()
        occupied = {
            (str(row["normalized_url"]), str(row["content_hash"])): str(row["id"])
            for row in rows
        }
        for row in rows:
            safe_url = sanitize_url(row["source_url"])
            normalized = normalize_url(safe_url)
            identity = (normalized, str(row["content_hash"]))
            other_id = occupied.get(identity)
            if other_id and other_id != str(row["id"]):
                separator = "&" if "?" in safe_url else "?"
                safe_url = (
                    f"{safe_url}{separator}dedupe_record="
                    f"{sha256_text(str(row['id']))[:12]}"
                )
                normalized = normalize_url(safe_url)
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            connection.execute(
                "UPDATE source_documents SET source_url=?, normalized_url=?, "
                "url_hash=?, metadata_json=? WHERE id=?",
                (
                    safe_url,
                    normalized,
                    sha256_text(normalized),
                    canonical_json(sanitize_tree(metadata, redact_pii=True)),
                    row["id"],
                ),
            )
            occupied[(normalized, str(row["content_hash"]))] = str(row["id"])
        connection.execute(
            """
            INSERT INTO fact_store_metadata(key, value)
            VALUES ('persistence_sanitizer_version', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (_PERSISTENCE_SANITIZER_VERSION,),
        )

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None

    # ------------------------------------------------------------------
    # Source documents

    def upsert_document(self, document: SourceDocument) -> tuple[SourceDocument, bool]:
        """Insert an immutable source snapshot, returning ``(row, created)``."""

        safe_source_url = sanitize_url(document.source_url)
        canonical_url = normalize_url(safe_source_url)
        content_hash = sha256_text(document.content)
        if (
            safe_source_url != document.source_url
            or canonical_url != document.normalized_url
            or content_hash != document.content_hash
            or document.url_hash != sha256_text(canonical_url)
            or document.metadata != sanitize_tree(document.metadata, redact_pii=True)
        ):
            document = replace(
                document,
                id=stable_id("doc", document.source_name.casefold(), canonical_url, content_hash),
                source_url=safe_source_url,
                normalized_url=canonical_url,
                url_hash=sha256_text(canonical_url),
                content_hash=content_hash,
                metadata=sanitize_tree(document.metadata, redact_pii=True),
            )
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT * FROM source_documents
                WHERE normalized_url = ? AND content_hash = ?
                """,
                (document.normalized_url, document.content_hash),
            ).fetchone()
            if existing:
                return self._document_from_row(existing), False

            duplicate = connection.execute(
                """
                SELECT id FROM source_documents
                WHERE content_hash = ?
                ORDER BY observed_at, id LIMIT 1
                """,
                (document.content_hash,),
            ).fetchone()
            duplicate_id = duplicate["id"] if duplicate else document.exact_duplicate_of_id
            stored = replace(document, exact_duplicate_of_id=duplicate_id)
            connection.execute(
                """
                INSERT INTO source_documents(
                    id, source_name, source_url, normalized_url, url_hash,
                    content_hash, title, content, source_grade, source_record_id,
                    published_at, observed_at, language, independent_source_key,
                    metadata_json, exact_duplicate_of_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.id,
                    stored.source_name,
                    stored.source_url,
                    stored.normalized_url,
                    stored.url_hash,
                    stored.content_hash,
                    stored.title,
                    stored.content,
                    stored.source_grade,
                    stored.source_record_id,
                    stored.published_at,
                    stored.observed_at,
                    stored.language,
                    stored.independent_source_key,
                    canonical_json(stored.metadata),
                    stored.exact_duplicate_of_id,
                ),
            )
            return stored, True

    def get_document(self, document_id: str) -> SourceDocument | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_documents WHERE id = ?", (document_id,)
            ).fetchone()
        return self._document_from_row(row) if row else None

    def find_documents(
        self,
        *,
        url: str | None = None,
        content_hash: str | None = None,
        limit: int = 100,
    ) -> list[SourceDocument]:
        conditions: list[str] = []
        values: list[Any] = []
        if url is not None:
            conditions.append("normalized_url = ?")
            values.append(normalize_url(url))
        if content_hash is not None:
            conditions.append("content_hash = ?")
            values.append(content_hash)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        values.append(max(1, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM source_documents{where} ORDER BY observed_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [self._document_from_row(row) for row in rows]

    @staticmethod
    def _document_from_row(row: sqlite3.Row) -> SourceDocument:
        return SourceDocument(
            id=row["id"],
            source_name=row["source_name"],
            source_url=row["source_url"],
            normalized_url=row["normalized_url"],
            url_hash=row["url_hash"],
            content_hash=row["content_hash"],
            title=row["title"],
            content=row["content"],
            source_grade=row["source_grade"],
            source_record_id=row["source_record_id"],
            published_at=row["published_at"],
            observed_at=row["observed_at"],
            language=row["language"],
            independent_source_key=row["independent_source_key"],
            metadata=_loads(row["metadata_json"], {}),
            exact_duplicate_of_id=row["exact_duplicate_of_id"],
        )

    # ------------------------------------------------------------------
    # Canonical entities and non-destructive resolution

    def upsert_entity(self, entity: CanonicalEntity) -> tuple[CanonicalEntity, bool]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM canonical_entities WHERE id = ?", (entity.id,)
            ).fetchone()
            if row:
                current = self._entity_from_row(row)
                attributes = dict(current.attributes)
                changed = False
                for key, value in entity.attributes.items():
                    if key not in attributes:
                        attributes[key] = value
                        changed = True
                if changed:
                    updated_at = utcnow()
                    connection.execute(
                        """
                        UPDATE canonical_entities
                        SET attributes_json = ?, updated_at = ? WHERE id = ?
                        """,
                        (canonical_json(attributes), updated_at, entity.id),
                    )
                    current = replace(current, attributes=attributes, updated_at=updated_at)
                return current, False
            connection.execute(
                """
                INSERT INTO canonical_entities(
                    id, entity_type, canonical_name, normalized_name,
                    attributes_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity.id,
                    entity.entity_type,
                    entity.canonical_name,
                    entity.normalized_name,
                    canonical_json(entity.attributes),
                    entity.created_at,
                    entity.updated_at,
                ),
            )
            return entity, True

    def get_or_create_entity(
        self,
        entity_type: str,
        canonical_name: str,
        *,
        entity_key: str = "",
        attributes: Mapping[str, Any] | None = None,
    ) -> tuple[CanonicalEntity, bool]:
        return self.upsert_entity(
            CanonicalEntity.create(
                entity_type, canonical_name, entity_key=entity_key, attributes=attributes
            )
        )

    def get_entity(self, entity_id: str) -> CanonicalEntity | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM canonical_entities WHERE id = ?", (entity_id,)
            ).fetchone()
        return self._entity_from_row(row) if row else None

    def find_entities(
        self, name: str, *, entity_type: str | None = None
    ) -> list[CanonicalEntity]:
        normalized = normalize_name(name)
        values: list[Any] = [normalized, normalized]
        type_sql = ""
        if entity_type:
            type_sql = " AND e.entity_type = ?"
            values.append(entity_type.casefold())
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT DISTINCT e.* FROM canonical_entities e
                LEFT JOIN entity_aliases a ON a.entity_id = e.id
                WHERE (e.normalized_name = ? OR a.normalized_alias = ?){type_sql}
                ORDER BY e.created_at
                """,
                values,
            ).fetchall()
        return [self._entity_from_row(row) for row in rows]

    def add_entity_alias(
        self,
        entity_id: str,
        alias: str,
        *,
        alias_type: str = "brand",
        source_document_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO entity_aliases(
                    entity_id, alias, normalized_alias, alias_type,
                    source_document_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entity_id,
                    alias,
                    normalize_name(alias),
                    alias_type,
                    source_document_id,
                    utcnow(),
                ),
            )

    def judge_entities(
        self,
        left_entity_id: str,
        right_entity_id: str,
        judgement: EntityJudgement | str,
        *,
        reason: str = "",
        actor: str = "system",
    ) -> EntityResolutionDecision:
        left, right = sorted((left_entity_id, right_entity_id))
        if left == right:
            raise ValueError("cannot judge an entity against itself")
        value = EntityJudgement(judgement).value
        now = utcnow()
        with self._connect() as connection:
            for entity_id in (left, right):
                if not connection.execute(
                    "SELECT 1 FROM canonical_entities WHERE id = ?", (entity_id,)
                ).fetchone():
                    raise KeyError(f"unknown entity: {entity_id}")
            active = connection.execute(
                """
                SELECT * FROM entity_resolution_decisions
                WHERE left_entity_id=? AND right_entity_id=? AND revoked_at IS NULL
                """,
                (left, right),
            ).fetchone()
            if (
                active
                and active["judgement"] == value
                and active["reason"] == reason
                and active["actor"] == actor
            ):
                return self._entity_decision_from_row(active)
            if active:
                connection.execute(
                    "UPDATE entity_resolution_decisions SET revoked_at=? WHERE id=?",
                    (now, active["id"]),
                )
            cursor = connection.execute(
                """
                INSERT INTO entity_resolution_decisions(
                    left_entity_id, right_entity_id, judgement, reason,
                    actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (left, right, value, reason, actor, now),
            )
            row = connection.execute(
                "SELECT * FROM entity_resolution_decisions WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
        return self._entity_decision_from_row(row)

    def entity_judgement_history(
        self, left_entity_id: str, right_entity_id: str
    ) -> list[EntityResolutionDecision]:
        left, right = sorted((left_entity_id, right_entity_id))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM entity_resolution_decisions
                WHERE left_entity_id=? AND right_entity_id=? ORDER BY id
                """,
                (left, right),
            ).fetchall()
        return [self._entity_decision_from_row(row) for row in rows]

    def get_positive_entity_cluster(self, entity_id: str) -> set[str]:
        """Return the reversible POSITIVE-resolution component."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT left_entity_id, right_entity_id
                FROM entity_resolution_decisions
                WHERE judgement='POSITIVE' AND revoked_at IS NULL
                """
            ).fetchall()
        graph: dict[str, set[str]] = {}
        for row in rows:
            graph.setdefault(row["left_entity_id"], set()).add(row["right_entity_id"])
            graph.setdefault(row["right_entity_id"], set()).add(row["left_entity_id"])
        seen = {entity_id}
        pending = [entity_id]
        while pending:
            current = pending.pop()
            for adjacent in graph.get(current, ()):
                if adjacent not in seen:
                    seen.add(adjacent)
                    pending.append(adjacent)
        return seen

    @staticmethod
    def _entity_from_row(row: sqlite3.Row) -> CanonicalEntity:
        return CanonicalEntity(
            id=row["id"],
            entity_type=row["entity_type"],
            canonical_name=row["canonical_name"],
            normalized_name=row["normalized_name"],
            attributes=_loads(row["attributes_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _entity_decision_from_row(row: sqlite3.Row) -> EntityResolutionDecision:
        return EntityResolutionDecision(
            id=row["id"],
            left_entity_id=row["left_entity_id"],
            right_entity_id=row["right_entity_id"],
            judgement=row["judgement"],
            reason=row["reason"],
            actor=row["actor"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
        )

    # ------------------------------------------------------------------
    # Statements

    def upsert_statement(self, statement: Statement) -> tuple[Statement, bool]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM statements WHERE id=?", (statement.id,)
            ).fetchone()
            if row:
                return self._statement_from_row(row), False
            connection.execute(
                """
                INSERT INTO statements(
                    id, document_id, predicate, subject_entity_id,
                    object_entity_id, object_value_json, occurred_at,
                    confidence, quote, slots_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    statement.id,
                    statement.document_id,
                    statement.predicate,
                    statement.subject_entity_id,
                    statement.object_entity_id,
                    canonical_json(statement.object_value)
                    if statement.object_value is not None
                    else None,
                    statement.occurred_at,
                    statement.confidence,
                    statement.quote,
                    canonical_json(statement.slots),
                    statement.created_at,
                ),
            )
            return statement, True

    def get_statement(self, statement_id: str) -> Statement | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM statements WHERE id=?", (statement_id,)
            ).fetchone()
        return self._statement_from_row(row) if row else None

    def list_statements(
        self,
        *,
        document_id: str | None = None,
        subject_entity_id: str | None = None,
        predicate: str | None = None,
        limit: int = 200,
    ) -> list[Statement]:
        conditions: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("document_id", document_id),
            ("subject_entity_id", subject_entity_id),
            ("predicate", predicate),
        ):
            if value is not None:
                conditions.append(f"{column}=?")
                values.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        values.append(max(1, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM statements{where} ORDER BY created_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [self._statement_from_row(row) for row in rows]

    @staticmethod
    def _statement_from_row(row: sqlite3.Row) -> Statement:
        return Statement(
            id=row["id"],
            document_id=row["document_id"],
            predicate=row["predicate"],
            subject_entity_id=row["subject_entity_id"],
            object_entity_id=row["object_entity_id"],
            object_value=_loads(row["object_value_json"], None),
            occurred_at=row["occurred_at"],
            confidence=float(row["confidence"]),
            quote=row["quote"],
            slots=_loads(row["slots_json"], {}),
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------------
    # Business-event clustering and evidence projection

    def cluster_event(
        self,
        *,
        company_entity_id: str,
        event_type: str,
        occurred_at: str | date | datetime | None,
        slots: Mapping[str, Any] | None = None,
        observed_at: str | date | datetime | None = None,
        window_days: int | None = None,
    ) -> tuple[BusinessEvent, bool]:
        normalized_slots = normalize_slots(slots)
        normalized_observed = normalize_timestamp(observed_at) or utcnow()
        normalized_time = normalize_timestamp(occurred_at) or normalized_observed
        window = int(window_days or self._default_event_window(event_type))

        candidates = self.list_events(
            company_entity_id=company_entity_id,
            event_type=event_type,
            include_linked=True,
            limit=500,
        )
        compatible: list[tuple[float, BusinessEvent]] = []
        for event in candidates:
            distance = self._time_distance_days(event.occurred_at, normalized_time)
            if distance is None or distance > window:
                continue
            if not self._slots_compatible(event.slots, normalized_slots, distance, window):
                continue
            compatible.append((distance, event))
        if compatible:
            compatible.sort(
                key=lambda pair: (
                    pair[0],
                    0 if pair[1].slot_fingerprint == sha256_text(canonical_json(normalized_slots)) else 1,
                    pair[1].created_at,
                )
            )
            event = compatible[0][1]
            merged = _merge_slots(event.slots, normalized_slots)
            earlier_time = min(event.occurred_at, normalized_time)
            changed = merged != event.slots or earlier_time != event.occurred_at
            last_observed = max(event.last_observed_at, normalized_observed)
            if changed or last_observed != event.last_observed_at:
                with self._connect() as connection:
                    if changed:
                        connection.execute(
                            """
                            INSERT INTO event_revisions(
                                event_id, prior_occurred_at, new_occurred_at,
                                prior_slots_json, new_slots_json, reason, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                event.id,
                                event.occurred_at,
                                earlier_time,
                                canonical_json(event.slots),
                                canonical_json(merged),
                                "compatible evidence expanded event projection",
                                utcnow(),
                            ),
                        )
                    connection.execute(
                        """
                        UPDATE business_events
                        SET occurred_at=?, time_bucket=?, slots_json=?,
                            slot_fingerprint=?, last_observed_at=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            earlier_time,
                            _event_time_bucket(earlier_time),
                            canonical_json(merged),
                            sha256_text(canonical_json(merged)),
                            last_observed,
                            utcnow(),
                            event.id,
                        ),
                    )
                event = self.get_event(event.id)
                assert event is not None
            return event, False

        slot_fingerprint = sha256_text(canonical_json(normalized_slots))
        event_id = stable_id(
            "evt",
            company_entity_id,
            event_type,
            normalized_time,
            slot_fingerprint,
        )
        now = utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO business_events(
                    id, company_entity_id, event_type, occurred_at, time_bucket,
                    slots_json, slot_fingerprint, lifecycle, lifecycle_mode,
                    lifecycle_reason, canonical_document_id,
                    independent_source_count, first_observed_at,
                    last_observed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'auto', '', NULL, 0, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    company_entity_id,
                    event_type,
                    normalized_time,
                    _event_time_bucket(normalized_time),
                    canonical_json(normalized_slots),
                    slot_fingerprint,
                    EventLifecycle.EMERGING.value,
                    normalized_observed,
                    normalized_observed,
                    now,
                    now,
                ),
            )
            created = connection.execute("SELECT changes()").fetchone()[0] == 1
            if created:
                connection.execute(
                    """
                    INSERT INTO event_revisions(
                        event_id, prior_occurred_at, new_occurred_at,
                        prior_slots_json, new_slots_json, reason, created_at
                    ) VALUES (?, NULL, ?, NULL, ?, 'event created', ?)
                    """,
                    (event_id, normalized_time, canonical_json(normalized_slots), now),
                )
                connection.execute(
                    """
                    INSERT INTO event_lifecycle_history(
                        event_id, prior_lifecycle, new_lifecycle,
                        reason, actor, created_at
                    ) VALUES (?, NULL, ?, 'event created', 'system', ?)
                    """,
                    (event_id, EventLifecycle.EMERGING.value, now),
                )
        event = self.get_event(event_id)
        assert event is not None
        return event, created

    def link_event_evidence(
        self,
        event_id: str,
        document_id: str,
        *,
        statement_id: str | None = None,
        stance: EvidenceStance | str = EvidenceStance.SUPPORTS,
    ) -> tuple[EventEvidence, bool]:
        stance_value = EvidenceStance(stance).value
        now = utcnow()
        with self._connect() as connection:
            document = connection.execute(
                "SELECT * FROM source_documents WHERE id=?", (document_id,)
            ).fetchone()
            if not document:
                raise KeyError(f"unknown document: {document_id}")
            if not connection.execute(
                "SELECT 1 FROM business_events WHERE id=?", (event_id,)
            ).fetchone():
                raise KeyError(f"unknown event: {event_id}")
            if statement_id and not connection.execute(
                "SELECT 1 FROM statements WHERE id=?", (statement_id,)
            ).fetchone():
                raise KeyError(f"unknown statement: {statement_id}")
            evidence_id = stable_id(
                "ee", event_id, document_id, statement_id or "", stance_value
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO event_evidence(
                    id, event_id, document_id, statement_id, stance,
                    independent_source_key, source_grade, linked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    event_id,
                    document_id,
                    statement_id,
                    stance_value,
                    document["independent_source_key"],
                    document["source_grade"],
                    now,
                ),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                "SELECT * FROM event_evidence WHERE id=?", (evidence_id,)
            ).fetchone()
        self.refresh_event(event_id)
        return self._event_evidence_from_row(row), created

    def refresh_event(
        self,
        event_id: str,
        *,
        as_of: str | date | datetime | None = None,
    ) -> BusinessEvent:
        """Recompute canonical evidence, support count and auto lifecycle."""

        now = normalize_timestamp(as_of) or utcnow()
        with self._connect() as connection:
            event_row = connection.execute(
                "SELECT * FROM business_events WHERE id=?", (event_id,)
            ).fetchone()
            if not event_row:
                raise KeyError(f"unknown event: {event_id}")
            evidence_rows = connection.execute(
                """
                SELECT ee.*, d.published_at, d.observed_at, d.id AS doc_id
                FROM event_evidence ee
                JOIN source_documents d ON d.id=ee.document_id
                WHERE ee.event_id=?
                """,
                (event_id,),
            ).fetchall()
            supporting = [row for row in evidence_rows if row["stance"] == "supports"]
            independent_count = len(
                {row["independent_source_key"] for row in supporting}
            )
            canonical_document_id: str | None = None
            if supporting:
                supporting.sort(
                    key=lambda row: (
                        grade_rank(row["source_grade"]),
                        row["published_at"] or row["observed_at"],
                        row["doc_id"],
                    )
                )
                canonical_document_id = supporting[0]["doc_id"]

            current = event_row["lifecycle"]
            reason = event_row["lifecycle_reason"]
            lifecycle = current
            if event_row["lifecycle_mode"] == "auto":
                superseded = connection.execute(
                    """
                    SELECT 1 FROM event_link_decisions
                    WHERE left_event_id=? AND link_type='supersedes'
                      AND judgement='POSITIVE' AND revoked_at IS NULL
                    """,
                    (event_id,),
                ).fetchone()
                if superseded:
                    lifecycle = EventLifecycle.SUPERSEDED.value
                    reason = "active supersedes link"
                elif any(row["stance"] == "retracts" for row in evidence_rows):
                    lifecycle = EventLifecycle.RETRACTED.value
                    reason = "a source explicitly retracts the event"
                elif any(row["stance"] == "contradicts" for row in evidence_rows):
                    lifecycle = EventLifecycle.DISPUTED.value
                    reason = "supporting and contradicting claims coexist"
                else:
                    last_seen = max(
                        (row["observed_at"] for row in evidence_rows),
                        default=event_row["last_observed_at"],
                    )
                    now_dt = _parse_datetime(now)
                    last_dt = _parse_datetime(last_seen)
                    if (
                        now_dt
                        and last_dt
                        and (now_dt - last_dt).days > self.stale_after_days
                    ):
                        lifecycle = EventLifecycle.STALE.value
                        reason = f"no evidence observed for {self.stale_after_days} days"
                    else:
                        revision_count = connection.execute(
                            "SELECT COUNT(*) FROM event_revisions WHERE event_id=?",
                            (event_id,),
                        ).fetchone()[0]
                        if independent_count >= 2 and revision_count > 1:
                            lifecycle = EventLifecycle.DEVELOPING.value
                            reason = "multiple independent sources and an evolving projection"
                        elif independent_count >= 2:
                            lifecycle = EventLifecycle.CORROBORATED.value
                            reason = "at least two independent source groups"
                        else:
                            lifecycle = EventLifecycle.EMERGING.value
                            reason = "fewer than two independent source groups"

            last_observed = max(
                (row["observed_at"] for row in evidence_rows),
                default=event_row["last_observed_at"],
            )
            changed_lifecycle = lifecycle != current
            connection.execute(
                """
                UPDATE business_events
                SET lifecycle=?, lifecycle_reason=?, canonical_document_id=?,
                    independent_source_count=?, last_observed_at=?, updated_at=?
                WHERE id=?
                """,
                (
                    lifecycle,
                    reason,
                    canonical_document_id,
                    independent_count,
                    last_observed,
                    utcnow(),
                    event_id,
                ),
            )
            if changed_lifecycle:
                connection.execute(
                    """
                    INSERT INTO event_lifecycle_history(
                        event_id, prior_lifecycle, new_lifecycle,
                        reason, actor, created_at
                    ) VALUES (?, ?, ?, ?, 'system', ?)
                    """,
                    (event_id, current, lifecycle, reason, utcnow()),
                )
        event = self.get_event(event_id)
        assert event is not None
        return event

    def refresh_all_lifecycles(
        self, *, as_of: str | date | datetime | None = None
    ) -> list[BusinessEvent]:
        events = self.list_events(include_linked=True, limit=100_000)
        return [self.refresh_event(event.id, as_of=as_of) for event in events]

    def set_event_lifecycle(
        self,
        event_id: str,
        lifecycle: EventLifecycle | str,
        *,
        reason: str,
        actor: str = "human",
        manual: bool = True,
    ) -> BusinessEvent:
        value = EventLifecycle(lifecycle).value
        now = utcnow()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT lifecycle FROM business_events WHERE id=?", (event_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"unknown event: {event_id}")
            connection.execute(
                """
                UPDATE business_events
                SET lifecycle=?, lifecycle_mode=?, lifecycle_reason=?, updated_at=?
                WHERE id=?
                """,
                (value, "manual" if manual else "auto", reason, now, event_id),
            )
            if row["lifecycle"] != value:
                connection.execute(
                    """
                    INSERT INTO event_lifecycle_history(
                        event_id, prior_lifecycle, new_lifecycle,
                        reason, actor, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, row["lifecycle"], value, reason, actor, now),
                )
        event = self.get_event(event_id)
        assert event is not None
        return event

    def get_event(self, event_id: str) -> BusinessEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM business_events WHERE id=?", (event_id,)
            ).fetchone()
        return self._event_from_row(row) if row else None

    def list_events(
        self,
        *,
        company_entity_id: str | None = None,
        company_name: str | None = None,
        event_type: str | None = None,
        lifecycle: str | None = None,
        include_linked: bool = False,
        limit: int = 200,
    ) -> list[BusinessEvent]:
        if company_name and not company_entity_id:
            entities = self.find_entities(company_name, entity_type="company")
            if not entities:
                return []
            company_entity_id = entities[0].id
        conditions: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("company_entity_id", company_entity_id),
            ("event_type", event_type),
            ("lifecycle", lifecycle),
        ):
            if value is not None:
                conditions.append(f"{column}=?")
                values.append(value)
        if not include_linked:
            conditions.append(
                """NOT EXISTS (
                    SELECT 1 FROM event_link_decisions eld
                    WHERE eld.left_event_id=business_events.id
                      AND eld.link_type='merge' AND eld.judgement='POSITIVE'
                      AND eld.revoked_at IS NULL
                )"""
            )
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        values.append(max(1, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM business_events{where}
                ORDER BY occurred_at DESC, id LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def get_event_evidence(self, event_id: str) -> list[EventEvidence]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM event_evidence
                WHERE event_id=?
                ORDER BY CASE source_grade
                    WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2
                    WHEN 'D' THEN 3 ELSE 9 END, linked_at
                """,
                (event_id,),
            ).fetchall()
        return [self._event_evidence_from_row(row) for row in rows]

    def get_event_documents(self, event_id: str) -> list[SourceDocument]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT d.* FROM source_documents d
                JOIN event_evidence ee ON ee.document_id=d.id
                WHERE ee.event_id=?
                ORDER BY CASE d.source_grade
                    WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2
                    WHEN 'D' THEN 3 ELSE 9 END, d.observed_at
                """,
                (event_id,),
            ).fetchall()
        return [self._document_from_row(row) for row in rows]

    def get_canonical_document(self, event_id: str) -> SourceDocument | None:
        event = self.get_event(event_id)
        if not event or not event.canonical_document_id:
            return None
        return self.get_document(event.canonical_document_id)

    def get_event_statements(self, event_id: str) -> list[Statement]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT s.* FROM statements s
                JOIN event_evidence ee ON ee.statement_id=s.id
                WHERE ee.event_id=? ORDER BY s.created_at
                """,
                (event_id,),
            ).fetchall()
        return [self._statement_from_row(row) for row in rows]

    def event_revisions(self, event_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM event_revisions WHERE event_id=? ORDER BY id",
                (event_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "prior_slots": _loads(row["prior_slots_json"], None),
                "new_slots": _loads(row["new_slots_json"], {}),
            }
            for row in rows
        ]

    def lifecycle_history(self, event_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM event_lifecycle_history
                WHERE event_id=? ORDER BY id
                """,
                (event_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> BusinessEvent:
        return BusinessEvent(
            id=row["id"],
            company_entity_id=row["company_entity_id"],
            event_type=row["event_type"],
            occurred_at=row["occurred_at"],
            time_bucket=row["time_bucket"],
            slots=_loads(row["slots_json"], {}),
            slot_fingerprint=row["slot_fingerprint"],
            lifecycle=row["lifecycle"],
            lifecycle_mode=row["lifecycle_mode"],
            lifecycle_reason=row["lifecycle_reason"],
            canonical_document_id=row["canonical_document_id"],
            independent_source_count=int(row["independent_source_count"]),
            first_observed_at=row["first_observed_at"],
            last_observed_at=row["last_observed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _event_evidence_from_row(row: sqlite3.Row) -> EventEvidence:
        return EventEvidence(
            id=row["id"],
            event_id=row["event_id"],
            document_id=row["document_id"],
            statement_id=row["statement_id"],
            stance=row["stance"],
            independent_source_key=row["independent_source_key"],
            source_grade=row["source_grade"],
            linked_at=row["linked_at"],
        )

    @staticmethod
    def _default_event_window(event_type: str) -> int:
        lower = event_type.casefold()
        if "financ" in lower or "融资" in lower:
            return 45
        if any(token in lower for token in ("factory", "capacity", "基地", "产能", "投产")):
            return 90
        if any(token in lower for token in ("order", "订单", "contract")):
            return 30
        return 45

    @staticmethod
    def _time_distance_days(left: str, right: str) -> float | None:
        left_dt, right_dt = _parse_datetime(left), _parse_datetime(right)
        if not left_dt or not right_dt:
            return 0.0 if left == right else None
        return abs((left_dt - right_dt).total_seconds()) / 86400

    @staticmethod
    def _slots_compatible(
        old: Mapping[str, Any],
        new: Mapping[str, Any],
        distance_days: float,
        window_days: int,
    ) -> bool:
        if old == new and old:
            return True
        shared = set(old) & set(new)
        identifying = {
            "round",
            "funding_round",
            "project",
            "project_name",
            "product",
            "location",
            "contract",
            "order",
        }
        if any(key in identifying and old[key] != new[key] for key in shared):
            return False
        if shared and any(old[key] == new[key] for key in shared):
            return True
        # Sparse news extracts can omit all structured fields.  In that case
        # only cluster tightly co-published reports, not everything in a broad
        # financing/capacity window.
        return distance_days <= min(7, window_days)

    # ------------------------------------------------------------------
    # Reversible event links (merge/split/supersession)

    def judge_event_link(
        self,
        left_event_id: str,
        right_event_id: str,
        link_type: EventLinkType | str,
        judgement: EntityJudgement | str,
        *,
        reason: str = "",
        actor: str = "system",
    ) -> EventLinkDecision:
        relation = EventLinkType(link_type).value
        if relation in {EventLinkType.MERGE.value, EventLinkType.SPLIT.value}:
            left, right = sorted((left_event_id, right_event_id))
        else:
            left, right = left_event_id, right_event_id
        if left == right:
            raise ValueError("cannot link an event to itself")
        value = EntityJudgement(judgement).value
        now = utcnow()
        with self._connect() as connection:
            for event_id in (left, right):
                if not connection.execute(
                    "SELECT 1 FROM business_events WHERE id=?", (event_id,)
                ).fetchone():
                    raise KeyError(f"unknown event: {event_id}")
            active = connection.execute(
                """
                SELECT * FROM event_link_decisions
                WHERE left_event_id=? AND right_event_id=? AND link_type=?
                  AND revoked_at IS NULL
                """,
                (left, right, relation),
            ).fetchone()
            if (
                active
                and active["judgement"] == value
                and active["reason"] == reason
                and active["actor"] == actor
            ):
                return self._event_link_from_row(active)
            if active:
                connection.execute(
                    "UPDATE event_link_decisions SET revoked_at=? WHERE id=?",
                    (now, active["id"]),
                )
            if value == EntityJudgement.POSITIVE.value and relation in {
                EventLinkType.MERGE.value,
                EventLinkType.SPLIT.value,
            }:
                opposite = (
                    EventLinkType.SPLIT.value
                    if relation == EventLinkType.MERGE.value
                    else EventLinkType.MERGE.value
                )
                # A pair cannot be actively "same event" and "must remain
                # separate" at once. Revocation preserves the contrary
                # decision in the audit history.
                connection.execute(
                    """
                    UPDATE event_link_decisions SET revoked_at=?
                    WHERE left_event_id=? AND right_event_id=? AND link_type=?
                      AND judgement='POSITIVE' AND revoked_at IS NULL
                    """,
                    (now, left, right, opposite),
                )
            cursor = connection.execute(
                """
                INSERT INTO event_link_decisions(
                    left_event_id, right_event_id, link_type, judgement,
                    reason, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (left, right, relation, value, reason, actor, now),
            )
            row = connection.execute(
                "SELECT * FROM event_link_decisions WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
        # Supersession affects the source event's lifecycle; revoking it should
        # also restore the automatically derived state.
        if relation == EventLinkType.SUPERSEDES.value:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE business_events SET lifecycle_mode='auto'
                    WHERE id=? AND lifecycle_reason='active supersedes link'
                    """,
                    (left,),
                )
            self.refresh_event(left)
        return self._event_link_from_row(row)

    def event_link_history(
        self,
        left_event_id: str,
        right_event_id: str,
        *,
        link_type: EventLinkType | str | None = None,
    ) -> list[EventLinkDecision]:
        conditions: list[str]
        values: list[Any]
        if link_type:
            relation = EventLinkType(link_type).value
            if relation in {"merge", "split"}:
                left_event_id, right_event_id = sorted(
                    (left_event_id, right_event_id)
                )
            conditions = ["left_event_id=?", "right_event_id=?", "link_type=?"]
            values = [left_event_id, right_event_id, relation]
        else:
            # Without a relation filter include directional and symmetric link
            # histories regardless of the caller's argument order.
            conditions = [
                "((left_event_id=? AND right_event_id=?) OR "
                "(left_event_id=? AND right_event_id=?))"
            ]
            values = [
                left_event_id,
                right_event_id,
                right_event_id,
                left_event_id,
            ]
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM event_link_decisions
                WHERE {' AND '.join(conditions)} ORDER BY id
                """,
                values,
            ).fetchall()
        return [self._event_link_from_row(row) for row in rows]

    def get_positive_event_cluster(self, event_id: str) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT left_event_id, right_event_id FROM event_link_decisions
                WHERE link_type='merge' AND judgement='POSITIVE'
                  AND revoked_at IS NULL
                """
            ).fetchall()
        graph: dict[str, set[str]] = {}
        for row in rows:
            graph.setdefault(row["left_event_id"], set()).add(row["right_event_id"])
            graph.setdefault(row["right_event_id"], set()).add(row["left_event_id"])
        seen = {event_id}
        pending = [event_id]
        while pending:
            current = pending.pop()
            for adjacent in graph.get(current, ()):
                if adjacent not in seen:
                    seen.add(adjacent)
                    pending.append(adjacent)
        return seen

    @staticmethod
    def _event_link_from_row(row: sqlite3.Row) -> EventLinkDecision:
        return EventLinkDecision(
            id=row["id"],
            left_event_id=row["left_event_id"],
            right_event_id=row["right_event_id"],
            link_type=row["link_type"],
            judgement=row["judgement"],
            reason=row["reason"],
            actor=row["actor"],
            created_at=row["created_at"],
            revoked_at=row["revoked_at"],
        )

    # ------------------------------------------------------------------
    # Compatibility ingestion for the existing Evidence pipeline

    def ingest_legacy_evidence(self, evidence: Any) -> IngestResult:
        """Project an existing ``models.Evidence`` into the four-layer store.

        ``Any`` is accepted deliberately: deployment copies sometimes contain
        serialized Evidence-like objects from older radar versions.  Required
        attributes still fail loudly rather than being guessed.
        """

        content = f"{evidence.title}\n{evidence.snippet}".strip()
        document = SourceDocument.create(
            source_name=evidence.source_name,
            source_url=evidence.source_url,
            title=evidence.title,
            content=content,
            source_grade=getattr(evidence, "source_grade", "B"),
            published_at=getattr(evidence, "event_date", None) or None,
            independent_source_key="",
            metadata={
                "legacy_evidence": True,
                "direction": getattr(evidence, "direction", ""),
            },
        )
        document, created_document = self.upsert_document(document)
        entity, created_entity = self.get_or_create_entity("company", evidence.company)
        slots = {
            "phase": getattr(evidence, "phase", ""),
            "direction": getattr(evidence, "direction", ""),
            "people": list(getattr(evidence, "people", ()) or ()),
            "organizations": list(getattr(evidence, "organizations", ()) or ()),
            **dict(getattr(evidence, "event_slots", {}) or {}),
        }
        statement = Statement.create(
            document_id=document.id,
            predicate=evidence.event_type,
            subject_entity_id=entity.id,
            object_value={"event_type": evidence.event_type},
            occurred_at=getattr(evidence, "event_date", None) or document.published_at,
            quote=evidence.snippet,
            slots=slots,
        )
        statement, created_statement = self.upsert_statement(statement)
        event, created_event = self.cluster_event(
            company_entity_id=entity.id,
            event_type=evidence.event_type,
            occurred_at=getattr(evidence, "event_date", None) or document.published_at,
            slots=slots,
            observed_at=document.observed_at,
        )
        self.link_event_evidence(
            event.id,
            document.id,
            statement_id=statement.id,
            stance=EvidenceStance.SUPPORTS,
        )
        event = self.get_event(event.id)
        assert event is not None
        return IngestResult(
            document=document,
            entity=entity,
            statement=statement,
            event=event,
            created_document=created_document,
            created_entity=created_entity,
            created_statement=created_statement,
            created_event=created_event,
        )

    def ingest_many_legacy(self, evidence_items: Iterable[Any]) -> list[IngestResult]:
        return [self.ingest_legacy_evidence(item) for item in evidence_items]
