"""SQLite state, approval audit and idempotency for talent-pool drafts."""

from __future__ import annotations

import hashlib
import json
import secrets
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .talent_pool import (
    canonical_payload_hash,
    draft_expiry_date,
    validate_liepin_payload,
)
from .sanitization import (
    safe_error,
    sanitize_diagnostic_fields,
    sanitize_text,
    sanitize_tree,
    sanitize_url,
)
from .product_clock import product_date_iso


_PERSISTENCE_SANITIZER_VERSION = "2"
_PROTECTED_JOB_KEYS = frozenset(
    {"public_payload", "liepin_payload", "liepin_payload_json", "payload_hash"}
)
_STRUCTURED_OPERATIONAL_FIELDS = frozenset(
    {
        "error_class",
        "model_identity",
        "generation_model",
        "provider",
        "processor",
        "source_id",
        "source_article_id",
        "adapter_id",
        "delivery_channel",
        "event_type",
        "phase",
        "mode",
        "reason_code",
        "code",
        "status",
    }
)
_DIAGNOSTIC_FIELD = re.compile(
    r"(?i)(?:^|[_-])(?:error|errors|exception|exceptions|trace|traces|"
    r"diagnostic|diagnostics|failure|failures)(?:$|[_-])"
)
_RAW_DIAGNOSTIC_FIELD = re.compile(
    r"(?i)^(?:raw_(?:completion|response|output|prompt)|"
    r"(?:first|repair|model|llm|provider)_(?:completion|response|output)|"
    r"request_payload|response_payload)$"
)
_OPAQUE_TALENT_FIELD = re.compile(
    r"(?i)(?:^|_)(?:id|ids|hash|sha(?:1|256|512)?|digest|fingerprint|"
    r"version|date|timestamp|time|at|count|status)(?:$|_)"
)


def _sanitize_talent_tree(value: Any, *, field: str = "") -> Any:
    """Sanitize operational state while keeping approved job JSON byte-stable."""

    if field in _PROTECTED_JOB_KEYS:
        return value
    if _RAW_DIAGNOSTIC_FIELD.fullmatch(field):
        return "[redacted]"
    # Primary/foreign keys and content-addressed identities must remain stable.
    # Without field context, a 16+ character identifier looks like a bearer
    # token to the scalar sanitizer and collapses distinct drafts into the same
    # ``[redacted-token]`` key.
    if _OPAQUE_TALENT_FIELD.search(field) and isinstance(
        value,
        (str, int, float, bool, type(None)),
    ):
        return value
    if field.casefold() in _STRUCTURED_OPERATIONAL_FIELDS and isinstance(value, str):
        return sanitize_tree({field: value}, redact_pii=True)[field]
    if _DIAGNOSTIC_FIELD.search(field):
        return sanitize_tree(value, string_limit=4000, redact_pii=True)
    probe = sanitize_diagnostic_fields({field: None}).get(field) if field else None
    if probe == "[redacted]":
        return "[redacted]"
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_talent_tree(item, field=str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_talent_tree(item, field=field) for item in value]
    if isinstance(value, str):
        if "url" in field.casefold() or value.lstrip().casefold().startswith(
            ("http://", "https://")
        ):
            return sanitize_url(value)
        return sanitize_tree(value, redact_pii=True)
    return value


def _safe_json_list_urls(value: object) -> str:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    return json.dumps(
        [sanitize_url(item) for item in parsed if str(item).strip()],
        ensure_ascii=False,
        sort_keys=True,
    )


VALID_STATUSES = frozenset(
    {
        "draft",
        "pending_approval",
        "approved",
        "publishing",
        "published",
        "rejected",
        "publish_failed",
        "expired",
    }
)
COMPLETION_STATUS_ENUMS = {
    "analysis_status": frozenset({"completed", "partial", "failed", "not_run"}),
    "draft_generation_status": frozenset(
        {"complete", "partial", "failed", "not_run"}
    ),
    "notification_status": frozenset(
        {
            "pending",
            "hook_reported",
            "hook_failed",
            "hook_failed_fallback_sent",
            "fallback_sent",
            "fallback_failed",
            "not_attempted",
        }
    ),
    "source_health_status": frozenset(
        {"healthy", "warning", "critical", "unavailable"}
    ),
}


@dataclass(frozen=True)
class ApprovalCommand:
    action: str
    indexes: tuple[int, ...] = ()


def parse_approval_command(command: str, *, draft_count: int) -> ApprovalCommand | None:
    """Accept only the explicit Chinese commands promised in the daily summary."""

    text = command.strip()
    if text == "发布全部":
        return ApprovalCommand("publish", tuple(range(1, draft_count + 1)))
    if text == "跳过全部":
        return ApprovalCommand("reject", tuple(range(1, draft_count + 1)))
    match = re.fullmatch(r"发布 ([1-9]\d*(?:,[1-9]\d*)*)", text)
    if match:
        indexes = tuple(sorted(int(item) for item in match.group(1).split(",")))
        if len(set(indexes)) != len(indexes) or any(
            item > draft_count for item in indexes
        ):
            return None
        return ApprovalCommand("publish", indexes)
    match = re.fullmatch(r"跳过 ([1-9]\d*(?:,[1-9]\d*)*)", text)
    if match:
        indexes = tuple(sorted(int(item) for item in match.group(1).split(",")))
        if len(set(indexes)) != len(indexes) or any(
            item > draft_count for item in indexes
        ):
            return None
        return ApprovalCommand("reject", indexes)
    match = re.fullmatch(r"查看 ([1-9]\d*) 的完整广告 JSON", text)
    if match:
        index = int(match.group(1))
        if index > draft_count:
            return None
        return ApprovalCommand("view", (index,))
    return None


class TalentPoolStore:
    def __init__(self, database: str | Path):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS talent_pool_drafts (
                    draft_id TEXT PRIMARY KEY,
                    run_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    payload_hash TEXT NOT NULL,
                    draft_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    approved_at TEXT,
                    approved_by TEXT,
                    approval_command TEXT,
                    liepin_job_id TEXT,
                    liepin_job_url TEXT,
                    published_at TEXT,
                    last_error_code TEXT,
                    last_error_message TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_talent_pool_batch
                ON talent_pool_drafts(run_date, direction, ordinal);

                CREATE TABLE IF NOT EXISTS talent_pool_current_batches (
                    run_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    PRIMARY KEY(run_date, direction)
                );

                CREATE TABLE IF NOT EXISTS talent_pool_current_snapshot_drafts (
                    run_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    PRIMARY KEY(run_date, direction, draft_id)
                );

                CREATE TABLE IF NOT EXISTS talent_pool_current_snapshots (
                    run_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_date, direction)
                );

                CREATE TABLE IF NOT EXISTS talent_pool_bundle_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    run_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    generation_provider TEXT NOT NULL,
                    generation_model TEXT NOT NULL,
                    generation_error TEXT NOT NULL,
                    bundle_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_talent_pool_snapshots
                ON talent_pool_bundle_snapshots(run_date, direction, created_at);

                CREATE TABLE IF NOT EXISTS talent_pool_opportunity_links (
                    snapshot_id TEXT NOT NULL,
                    run_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    generation_model TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    recommended_title TEXT NOT NULL,
                    role_family TEXT NOT NULL,
                    talent_persona TEXT NOT NULL,
                    company TEXT NOT NULL,
                    company_role TEXT NOT NULL,
                    evidence_urls_json TEXT NOT NULL,
                    liepin_payload_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, draft_id, company, company_role)
                );
                CREATE INDEX IF NOT EXISTS idx_talent_pool_opportunity_current
                ON talent_pool_opportunity_links(active, direction, company);

                CREATE TABLE IF NOT EXISTS talent_pool_approval_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    draft_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    command TEXT NOT NULL,
                    action TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS talent_pool_publish_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    draft_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    attempt_key TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    outcome TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    job_id TEXT,
                    job_url TEXT,
                    UNIQUE(attempt_key)
                );
                CREATE TABLE IF NOT EXISTS talent_pool_publish_leases (
                    lease_key TEXT PRIMARY KEY,
                    run_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    lease_token TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    released_at TEXT
                );
                CREATE TABLE IF NOT EXISTS talent_pool_openclaw_reports (
                    snapshot_id TEXT PRIMARY KEY,
                    run_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    ordered_draft_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reported_at TEXT,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_talent_pool_openclaw_pending
                ON talent_pool_openclaw_reports(status, run_date, direction);
                CREATE TABLE IF NOT EXISTS talent_pool_delivery_ledger (
                    snapshot_id TEXT NOT NULL,
                    delivery_channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    delivered_at TEXT,
                    error_class TEXT NOT NULL DEFAULT '',
                    error_detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, delivery_channel),
                    FOREIGN KEY(snapshot_id)
                      REFERENCES talent_pool_bundle_snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_talent_pool_delivery_delivered
                ON talent_pool_delivery_ledger(status, delivered_at);
                CREATE TABLE IF NOT EXISTS talent_pool_final_report_opportunities (
                    snapshot_id TEXT NOT NULL,
                    run_date TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    source_run_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    company TEXT NOT NULL,
                    score REAL NOT NULL,
                    role_hypotheses_json TEXT NOT NULL,
                    evidence_urls_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, company),
                    FOREIGN KEY(snapshot_id)
                      REFERENCES talent_pool_bundle_snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_talent_pool_final_report_history
                ON talent_pool_final_report_opportunities(run_date, direction, company);
                CREATE TABLE IF NOT EXISTS talent_pool_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            self._migrate_legacy_state(connection)
            self._sanitize_persisted_operational_state(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _migrate_legacy_state(self, connection: sqlite3.Connection) -> None:
        """Backfill current pointers and opportunity links from pre-snapshot DBs."""

        now = _utcnow()
        batches = connection.execute(
            """
            SELECT b.run_date, b.direction, b.source_run_id, b.generated_at
            FROM talent_pool_current_batches b
            LEFT JOIN talent_pool_current_snapshots c
              ON c.run_date=b.run_date AND c.direction=b.direction
            WHERE c.snapshot_id IS NULL
            """
        ).fetchall()
        for batch in batches:
            rows = connection.execute(
                """
                SELECT * FROM talent_pool_drafts
                WHERE run_date=? AND direction=? AND source_run_id=?
                  AND status<>'expired'
                ORDER BY ordinal
                """,
                (batch["run_date"], batch["direction"], batch["source_run_id"]),
            ).fetchall()
            drafts = [json.loads(row["draft_json"]) for row in rows]
            legacy_bundle = {
                "run_date": batch["run_date"],
                "direction": batch["direction"],
                "source_run_id": batch["source_run_id"],
                "generation_provider": "legacy-migration",
                "generation_model": "",
                "generation_error": "",
                "drafts": drafts,
            }
            bundle_json = json.dumps(
                legacy_bundle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            snapshot_id = hashlib.sha256(bundle_json.encode("utf-8")).hexdigest()
            connection.execute(
                """
                INSERT OR IGNORE INTO talent_pool_bundle_snapshots(
                    snapshot_id, run_date, direction, source_run_id,
                    generation_provider, generation_model, generation_error,
                    bundle_json, created_at
                ) VALUES (?, ?, ?, ?, 'legacy-migration', '', '', ?, ?)
                """,
                (
                    snapshot_id,
                    batch["run_date"],
                    batch["direction"],
                    batch["source_run_id"],
                    bundle_json,
                    batch["generated_at"] or now,
                ),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO talent_pool_current_snapshots(
                    run_date, direction, snapshot_id, source_run_id, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    batch["run_date"],
                    batch["direction"],
                    snapshot_id,
                    batch["source_run_id"],
                    now,
                ),
            )
            for ordinal, (row, draft) in enumerate(zip(rows, drafts), start=1):
                connection.execute(
                    """
                    INSERT OR REPLACE INTO talent_pool_current_snapshot_drafts(
                        run_date, direction, snapshot_id, draft_id, ordinal
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        batch["run_date"],
                        batch["direction"],
                        snapshot_id,
                        row["draft_id"],
                        ordinal,
                    ),
                )
                self._insert_opportunity_links(
                    connection,
                    snapshot_id=snapshot_id,
                    run_date=batch["run_date"],
                    direction=batch["direction"],
                    source_run_id=batch["source_run_id"],
                    generation_model="",
                    draft=draft,
                    active=1,
                    created_at=batch["generated_at"] or now,
                )

        historical_rows = connection.execute(
            """
            SELECT d.* FROM talent_pool_drafts d
            WHERE NOT EXISTS (
                SELECT 1 FROM talent_pool_opportunity_links o
                WHERE o.draft_id=d.draft_id
            )
            """
        ).fetchall()
        for row in historical_rows:
            draft = json.loads(row["draft_json"])
            legacy_id = hashlib.sha256(
                f"legacy:{row['draft_id']}:{row['payload_hash']}".encode("utf-8")
            ).hexdigest()
            self._insert_opportunity_links(
                connection,
                snapshot_id=legacy_id,
                run_date=row["run_date"],
                direction=row["direction"],
                source_run_id=row["source_run_id"],
                generation_model="",
                draft=draft,
                active=0,
                created_at=row["updated_at"] or now,
            )

        # Legacy OpenClaw reports are confirmed deliveries. Backfill once so
        # the new ledger does not forget the existing seven-day history.
        connection.execute(
            """
            INSERT OR IGNORE INTO talent_pool_delivery_ledger(
                snapshot_id, delivery_channel, status, delivered_at,
                error_class, error_detail, created_at, updated_at
            )
            SELECT r.snapshot_id, 'legacy_openclaw', 'delivered', r.reported_at,
                   '', '', r.created_at, r.updated_at
            FROM talent_pool_openclaw_reports r
            WHERE r.status='reported' AND r.reported_at IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM talent_pool_delivery_ledger d
                WHERE d.snapshot_id=r.snapshot_id AND d.status='delivered'
              )
            """
        )
        # Pre-hotfix snapshots only know companies through draft links. They
        # remain usable for cooldown, while every new snapshot stores the full
        # final report independently of whether a draft was generated.
        legacy_rows = connection.execute(
            """
            SELECT o.snapshot_id, o.run_date, o.direction, o.source_run_id,
                   o.company, o.created_at, o.evidence_urls_json, o.company_role
            FROM talent_pool_opportunity_links o
            LEFT JOIN talent_pool_final_report_opportunities f
              ON f.snapshot_id=o.snapshot_id AND f.company=o.company
            WHERE f.company IS NULL
            ORDER BY o.run_date, o.snapshot_id, o.company, o.company_role
            """
        ).fetchall()
        grouped_legacy: dict[tuple[str, str], dict[str, Any]] = {}
        for row in legacy_rows:
            key = (str(row["snapshot_id"]), str(row["company"]))
            item = grouped_legacy.setdefault(
                key,
                {
                    "snapshot_id": str(row["snapshot_id"]),
                    "run_date": str(row["run_date"]),
                    "direction": str(row["direction"]),
                    "source_run_id": str(row["source_run_id"]),
                    "company": str(row["company"]),
                    "created_at": str(row["created_at"] or now),
                    "roles": [],
                    "urls": [],
                },
            )
            role = str(row["company_role"] or "").strip()
            if role and role not in item["roles"]:
                item["roles"].append(role)
            try:
                urls = json.loads(str(row["evidence_urls_json"] or "[]"))
            except json.JSONDecodeError:
                urls = []
            for url in urls if isinstance(urls, list) else []:
                normalized_url = str(url).strip()
                if normalized_url and normalized_url not in item["urls"]:
                    item["urls"].append(normalized_url)
        ordinals: dict[str, int] = {}
        for item in grouped_legacy.values():
            snapshot_id = item["snapshot_id"]
            ordinals[snapshot_id] = ordinals.get(snapshot_id, 0) + 1
            connection.execute(
                """
                INSERT OR IGNORE INTO talent_pool_final_report_opportunities(
                    snapshot_id, run_date, direction, source_run_id, ordinal,
                    company, score, role_hypotheses_json, evidence_urls_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    item["run_date"],
                    item["direction"],
                    item["source_run_id"],
                    ordinals[snapshot_id],
                    item["company"],
                    json.dumps(item["roles"], ensure_ascii=False, sort_keys=True),
                    json.dumps(item["urls"], ensure_ascii=False, sort_keys=True),
                    item["created_at"] or now,
                ),
            )

    def _sanitize_persisted_operational_state(
        self, connection: sqlite3.Connection
    ) -> None:
        marker = connection.execute(
            "SELECT value FROM talent_pool_metadata "
            "WHERE key='persistence_sanitizer_version'"
        ).fetchone()
        if marker and str(marker["value"]) == _PERSISTENCE_SANITIZER_VERSION:
            return

        for row in connection.execute(
            "SELECT draft_id, draft_json, liepin_job_url, last_error_code, "
            "last_error_message FROM talent_pool_drafts"
        ).fetchall():
            try:
                draft = json.loads(str(row["draft_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                draft = {}
            safe_draft = _sanitize_talent_tree(draft)
            connection.execute(
                "UPDATE talent_pool_drafts SET draft_json=?, liepin_job_url=?, "
                "last_error_code=?, last_error_message=? WHERE draft_id=?",
                (
                    json.dumps(safe_draft, ensure_ascii=False, sort_keys=True),
                    sanitize_url(row["liepin_job_url"]),
                    sanitize_text(row["last_error_code"], limit=120),
                    sanitize_text(row["last_error_message"], limit=1000),
                    row["draft_id"],
                ),
            )
        for row in connection.execute(
            "SELECT snapshot_id, generation_error, bundle_json "
            "FROM talent_pool_bundle_snapshots"
        ).fetchall():
            try:
                bundle = json.loads(str(row["bundle_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                bundle = {}
            safe_bundle = _sanitize_talent_tree(bundle)
            connection.execute(
                "UPDATE talent_pool_bundle_snapshots SET generation_error=?, "
                "bundle_json=? WHERE snapshot_id=?",
                (
                    sanitize_text(row["generation_error"], limit=1000),
                    json.dumps(
                        safe_bundle,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    row["snapshot_id"],
                ),
            )
        for table in (
            "talent_pool_opportunity_links",
            "talent_pool_final_report_opportunities",
        ):
            rows = connection.execute(
                f"SELECT rowid, evidence_urls_json FROM {table}"
            ).fetchall()
            for row in rows:
                connection.execute(
                    f"UPDATE {table} SET evidence_urls_json=? WHERE rowid=?",
                    (_safe_json_list_urls(row["evidence_urls_json"]), int(row["rowid"])),
                )
        for row in connection.execute(
            "SELECT id, error_code, error_message, job_url "
            "FROM talent_pool_publish_attempts"
        ).fetchall():
            connection.execute(
                "UPDATE talent_pool_publish_attempts SET error_code=?, "
                "error_message=?, job_url=? WHERE id=?",
                (
                    sanitize_text(row["error_code"], limit=120),
                    sanitize_text(row["error_message"], limit=1000),
                    sanitize_url(row["job_url"]),
                    int(row["id"]),
                ),
            )
        for row in connection.execute(
            "SELECT snapshot_id, last_error FROM talent_pool_openclaw_reports"
        ).fetchall():
            connection.execute(
                "UPDATE talent_pool_openclaw_reports SET last_error=? "
                "WHERE snapshot_id=?",
                (
                    sanitize_text(row["last_error"], limit=1000),
                    row["snapshot_id"],
                ),
            )
        for row in connection.execute(
            "SELECT snapshot_id, delivery_channel, error_class, error_detail "
            "FROM talent_pool_delivery_ledger"
        ).fetchall():
            connection.execute(
                "UPDATE talent_pool_delivery_ledger SET error_class=?, "
                "error_detail=? WHERE snapshot_id=? AND delivery_channel=?",
                (
                    safe_error(row["error_class"])["error_class"]
                    if row["error_class"]
                    else "",
                    sanitize_text(row["error_detail"], limit=1000),
                    row["snapshot_id"],
                    row["delivery_channel"],
                ),
            )
        connection.execute(
            """
            INSERT INTO talent_pool_metadata(key, value)
            VALUES ('persistence_sanitizer_version', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (_PERSISTENCE_SANITIZER_VERSION,),
        )

    @staticmethod
    def _insert_opportunity_links(
        connection: sqlite3.Connection,
        *,
        snapshot_id: str,
        run_date: str,
        direction: str,
        source_run_id: str,
        generation_model: str,
        draft: Mapping[str, Any],
        active: int,
        created_at: str,
    ) -> None:
        payload_json = json.dumps(
            draft.get("public_payload") or {}, ensure_ascii=False, sort_keys=True
        )
        for source in draft.get("source_leads") or ():
            if not isinstance(source, Mapping):
                continue
            company = str(source.get("company") or "").strip()
            if not company:
                continue
            roles = [
                str(item).strip()
                for item in source.get("role_hypotheses") or ()
                if str(item).strip()
            ] or [str(draft.get("recommended_title") or "").strip()]
            evidence_urls_json = json.dumps(
                [
                    sanitize_url(item)
                    for item in source.get("evidence_urls") or ()
                    if str(item).strip()
                ],
                ensure_ascii=False,
                sort_keys=True,
            )
            for company_role in roles:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO talent_pool_opportunity_links(
                        snapshot_id, run_date, direction, source_run_id,
                        generation_model, draft_id, recommended_title,
                        role_family, talent_persona, company, company_role,
                        evidence_urls_json, liepin_payload_json, active, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        run_date,
                        direction,
                        source_run_id,
                        generation_model,
                        str(draft.get("draft_id") or ""),
                        str(draft.get("recommended_title") or ""),
                        str(draft.get("role_family") or ""),
                        str(draft.get("talent_persona") or ""),
                        company,
                        company_role,
                        evidence_urls_json,
                        payload_json,
                        active,
                        created_at,
                    ),
                )

    def save_bundle(self, bundle: Mapping[str, Any]) -> int:
        run_date = str(bundle.get("run_date") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", run_date):
            raise ValueError("draft bundle run_date must use YYYY-MM-DD")
        try:
            date.fromisoformat(run_date)
        except ValueError as error:
            raise ValueError("draft bundle run_date must be a valid date") from error
        direction = str(bundle.get("direction") or "")
        source_run_id = str(bundle.get("source_run_id") or "")
        if not source_run_id:
            raise ValueError("draft bundle requires source_run_id")
        raw_drafts = bundle.get("drafts") or ()
        if not isinstance(raw_drafts, (list, tuple)):
            raise ValueError("draft bundle drafts must be a list")
        drafts = [dict(item) for item in raw_drafts if isinstance(item, Mapping)]
        if len(drafts) != len(raw_drafts):
            raise ValueError("each draft must be an object")
        for draft in drafts:
            payload = draft.get("public_payload")
            if not isinstance(payload, Mapping):
                raise ValueError("each draft public_payload must be an object")
            validate_liepin_payload(payload)
            draft_run_date = str(draft.get("run_date") or "")
            if draft_run_date != run_date:
                raise ValueError("each draft run_date must equal bundle run_date")
            draft["run_date"] = run_date
            expiry = str(draft.get("expires_at") or "").strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry):
                raise ValueError("each draft expires_at must use YYYY-MM-DD")
            try:
                canonical_expiry = date.fromisoformat(expiry).isoformat()
            except ValueError as error:
                raise ValueError("each draft expires_at must be a valid date") from error
            if canonical_expiry != draft_expiry_date(run_date):
                raise ValueError("each draft expires_at must equal run_date plus 7 days")
            draft["expires_at"] = canonical_expiry
            draft["payload_hash"] = canonical_payload_hash(payload)
        drafts = [dict(_sanitize_talent_tree(draft)) for draft in drafts]
        normalized_bundle = dict(bundle)
        normalized_bundle["drafts"] = drafts
        generation_error = str(normalized_bundle.get("generation_error") or "")
        if generation_error:
            normalized_bundle["generation_error"] = (
                f"{safe_error(generation_error)['error_class']}: "
                "draft generation was incomplete"
            )
        raw_completion = normalized_bundle.get("completion_status") or {}
        if not isinstance(raw_completion, Mapping):
            raise ValueError("completion_status must be an object")
        for field, allowed in COMPLETION_STATUS_ENUMS.items():
            value = str(raw_completion.get(field) or "").strip()
            if value and value not in allowed:
                raise ValueError(f"unsupported {field}: {value}")
        normalized_bundle["completion_status"] = _sanitize_talent_tree(
            raw_completion,
            field="completion_status",
        )
        normalized_bundle = dict(_sanitize_talent_tree(normalized_bundle))
        drafts = [
            dict(item)
            for item in normalized_bundle.get("drafts") or ()
            if isinstance(item, Mapping)
        ]
        bundle_json = json.dumps(
            normalized_bundle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot_id = hashlib.sha256(bundle_json.encode("utf-8")).hexdigest()
        now = _utcnow()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            unresolved = connection.execute(
                """
                SELECT 1 FROM talent_pool_drafts d
                WHERE d.status='publishing'
                   OR EXISTS (
                     SELECT 1 FROM talent_pool_publish_attempts a
                     WHERE a.draft_id=d.draft_id
                       AND a.outcome IN ('started', 'ambiguous')
                   )
                LIMIT 1
                """
            ).fetchone()
            if unresolved:
                raise RuntimeError(
                    "cannot replace a batch while a publish result is unresolved"
                )
            prior_memberships = {
                row["draft_id"]: row["snapshot_id"]
                for row in connection.execute(
                    """
                    SELECT draft_id, snapshot_id
                    FROM talent_pool_current_snapshot_drafts
                    WHERE run_date=? AND direction=?
                    """,
                    (run_date, direction),
                ).fetchall()
            }
            connection.execute(
                """
                INSERT OR IGNORE INTO talent_pool_bundle_snapshots(
                    snapshot_id, run_date, direction, source_run_id,
                    generation_provider, generation_model, generation_error,
                    bundle_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    run_date,
                    direction,
                    source_run_id,
                    str(normalized_bundle.get("generation_provider") or ""),
                    str(normalized_bundle.get("generation_model") or ""),
                    str(normalized_bundle.get("generation_error") or ""),
                    bundle_json,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM talent_pool_final_report_opportunities WHERE snapshot_id=?",
                (snapshot_id,),
            )
            report_opportunities = normalized_bundle.get("final_report_opportunities")
            if report_opportunities is None:
                legacy_by_company: dict[str, dict[str, Any]] = {}
                for draft in drafts:
                    for source in draft.get("source_leads") or []:
                        if not isinstance(source, Mapping):
                            continue
                        company = str(source.get("company") or "").strip()
                        if not company:
                            continue
                        item = legacy_by_company.setdefault(
                            company,
                            {
                                "company": company,
                                "score": float(source.get("score") or 0),
                                "role_hypotheses": [],
                                "evidence_urls": [],
                            },
                        )
                        item["role_hypotheses"] = list(
                            dict.fromkeys(
                                [*item["role_hypotheses"], *(source.get("role_hypotheses") or [])]
                            )
                        )
                        item["evidence_urls"] = list(
                            dict.fromkeys(
                                [*item["evidence_urls"], *(source.get("evidence_urls") or [])]
                            )
                        )
                report_opportunities = list(legacy_by_company.values())
            if not isinstance(report_opportunities, list):
                raise ValueError("final_report_opportunities must be a list")
            for ordinal, raw in enumerate(report_opportunities, start=1):
                if not isinstance(raw, Mapping):
                    raise ValueError("each final report opportunity must be an object")
                company = str(raw.get("company") or "").strip()
                if not company:
                    raise ValueError("final report opportunity requires company")
                roles = [
                    str(item).strip()
                    for item in raw.get("role_hypotheses") or []
                    if str(item).strip()
                ]
                urls = [
                    sanitize_url(item)
                    for item in raw.get("evidence_urls") or []
                    if str(item).strip()
                ]
                connection.execute(
                    """
                    INSERT INTO talent_pool_final_report_opportunities(
                        snapshot_id, run_date, direction, source_run_id, ordinal,
                        company, score, role_hypotheses_json,
                        evidence_urls_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        run_date,
                        direction,
                        source_run_id,
                        ordinal,
                        company,
                        float(raw.get("score") or 0),
                        json.dumps(roles, ensure_ascii=False, sort_keys=True),
                        json.dumps(urls, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE talent_pool_opportunity_links SET active=0
                WHERE run_date=? AND direction=?
                """,
                (run_date, direction),
            )
            connection.execute(
                """
                UPDATE talent_pool_drafts SET status='expired', updated_at=?
                WHERE run_date=? AND direction=? AND source_run_id<>?
                  AND status NOT IN ('published', 'expired')
                """,
                (now, run_date, direction, source_run_id),
            )
            current_ids = [str(item["draft_id"]) for item in drafts]
            if current_ids:
                placeholders = ",".join("?" for _ in current_ids)
                connection.execute(
                    f"""
                    UPDATE talent_pool_drafts SET status='expired', updated_at=?
                    WHERE run_date=? AND direction=? AND source_run_id=?
                      AND draft_id NOT IN ({placeholders})
                      AND status NOT IN ('published', 'expired')
                    """,
                    (now, run_date, direction, source_run_id, *current_ids),
                )
            else:
                connection.execute(
                    """
                    UPDATE talent_pool_drafts SET status='expired', updated_at=?
                    WHERE run_date=? AND direction=? AND source_run_id=?
                      AND status NOT IN ('published', 'expired')
                    """,
                    (now, run_date, direction, source_run_id),
                )
            connection.execute(
                """
                DELETE FROM talent_pool_current_snapshot_drafts
                WHERE run_date=? AND direction=?
                """,
                (run_date, direction),
            )
            connection.execute(
                """
                INSERT INTO talent_pool_current_batches(
                    run_date, direction, source_run_id, generated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(run_date, direction) DO UPDATE SET
                    source_run_id=excluded.source_run_id,
                    generated_at=excluded.generated_at
                """,
                (run_date, direction, source_run_id, now),
            )
            connection.execute(
                """
                INSERT INTO talent_pool_current_snapshots(
                    run_date, direction, snapshot_id, source_run_id, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_date, direction) DO UPDATE SET
                    snapshot_id=excluded.snapshot_id,
                    source_run_id=excluded.source_run_id,
                    updated_at=excluded.updated_at
                """,
                (run_date, direction, snapshot_id, source_run_id, now),
            )
            for ordinal, draft_value in enumerate(drafts, start=1):
                draft = dict(draft_value)
                draft_id = str(draft["draft_id"])
                payload_hash = canonical_payload_hash(draft["public_payload"])
                if payload_hash != draft.get("payload_hash"):
                    draft["payload_hash"] = payload_hash
                current = connection.execute(
                    "SELECT payload_hash, status, source_run_id "
                    "FROM talent_pool_drafts WHERE draft_id=?",
                    (draft_id,),
                ).fetchone()
                if (
                    current
                    and current["status"] == "published"
                    and current["payload_hash"] != payload_hash
                ):
                    raise ValueError(f"published draft {draft_id} is immutable")
                status = "pending_approval"
                clear_approval = True
                if (
                    current
                    and current["status"] != "expired"
                    and current["payload_hash"] == payload_hash
                    and (
                        prior_memberships.get(draft_id) == snapshot_id
                        or current["status"] == "published"
                    )
                ):
                    status = current["status"]
                    clear_approval = False
                if status == "published":
                    clear_approval = False
                values = (
                    run_date,
                    direction,
                    source_run_id,
                    ordinal,
                    payload_hash,
                    json.dumps(draft, ensure_ascii=False, sort_keys=True),
                    status,
                    str(draft["expires_at"]),
                    now,
                    draft_id,
                )
                connection.execute(
                    """
                    INSERT INTO talent_pool_drafts(
                        run_date, direction, source_run_id, ordinal,
                        payload_hash, draft_json,
                        status, expires_at, updated_at, draft_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(draft_id) DO UPDATE SET
                        run_date=excluded.run_date,
                        direction=excluded.direction,
                        source_run_id=excluded.source_run_id,
                        ordinal=excluded.ordinal,
                        payload_hash=excluded.payload_hash,
                        draft_json=excluded.draft_json,
                        status=excluded.status,
                        expires_at=excluded.expires_at,
                        updated_at=excluded.updated_at
                    """,
                    values,
                )
                if clear_approval:
                    connection.execute(
                        """
                        UPDATE talent_pool_drafts SET
                            approved_at=NULL, approved_by=NULL,
                            approval_command=NULL, liepin_job_id=NULL,
                            liepin_job_url=NULL, published_at=NULL,
                            last_error_code=NULL, last_error_message=NULL
                        WHERE draft_id=?
                        """,
                        (draft_id,),
                    )
                connection.execute(
                    """
                    INSERT INTO talent_pool_current_snapshot_drafts(
                        run_date, direction, snapshot_id, draft_id, ordinal
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_date, direction, snapshot_id, draft_id, ordinal),
                )
                payload_json = json.dumps(
                    draft["public_payload"], ensure_ascii=False, sort_keys=True
                )
                for source in draft.get("source_leads") or ():
                    if not isinstance(source, Mapping):
                        continue
                    company = str(source.get("company") or "").strip()
                    if not company:
                        continue
                    company_roles = [
                        str(item).strip()
                        for item in source.get("role_hypotheses") or ()
                        if str(item).strip()
                    ] or [str(draft.get("recommended_title") or "").strip()]
                    evidence_urls_json = json.dumps(
                        [
                            sanitize_url(item)
                            for item in source.get("evidence_urls") or ()
                            if str(item).strip()
                        ],
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    for company_role in company_roles:
                        connection.execute(
                            """
                            INSERT OR REPLACE INTO talent_pool_opportunity_links(
                                snapshot_id, run_date, direction, source_run_id,
                                generation_model, draft_id, recommended_title,
                                role_family, talent_persona, company, company_role,
                                evidence_urls_json, liepin_payload_json, active,
                                created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                            """,
                            (
                                snapshot_id,
                                run_date,
                                direction,
                                source_run_id,
                                str(bundle.get("generation_model") or ""),
                                draft_id,
                                str(draft.get("recommended_title") or ""),
                                str(draft.get("role_family") or ""),
                                str(draft.get("talent_persona") or ""),
                                company,
                                company_role,
                                evidence_urls_json,
                                payload_json,
                                now,
                            ),
                        )
            existing_report = connection.execute(
                "SELECT status FROM talent_pool_openclaw_reports WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
            if existing_report is None:
                connection.execute(
                    """
                    INSERT INTO talent_pool_openclaw_reports(
                        snapshot_id, run_date, direction, session_key,
                        ordered_draft_ids_json, status, created_at, updated_at
                    ) VALUES (?, ?, ?, 'agent:main:main', ?, 'pending', ?, ?)
                    """,
                    (
                        snapshot_id,
                        run_date,
                        direction,
                        json.dumps(current_ids, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
        return len(drafts)

    def pending_openclaw_report(
        self,
        *,
        session_key: str = "agent:main:main",
        claim: bool = False,
    ) -> dict[str, Any] | None:
        """Return the newest current snapshot that OpenClaw has not reported."""

        stale_before = datetime.now(timezone.utc).timestamp() - 20 * 60
        with self._connect() as connection:
            if claim:
                connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT r.*, s.bundle_json
                FROM talent_pool_openclaw_reports r
                JOIN talent_pool_current_snapshots c
                  ON c.snapshot_id=r.snapshot_id
                 AND c.run_date=r.run_date
                 AND c.direction=r.direction
                JOIN talent_pool_bundle_snapshots s
                  ON s.snapshot_id=r.snapshot_id
                WHERE r.session_key=?
                ORDER BY r.run_date DESC, r.created_at DESC
                LIMIT 1
                """,
                (session_key,),
            ).fetchone()
            selected = rows
            if selected is None or selected["status"] == "reported":
                return None
            delivered = connection.execute(
                "SELECT 1 FROM talent_pool_delivery_ledger "
                "WHERE snapshot_id=? AND status='delivered' LIMIT 1",
                (selected["snapshot_id"],),
            ).fetchone()
            if delivered is not None and selected["last_error"] != "operator_requeue":
                return None
            if selected["status"] in {"reporting", "read"}:
                try:
                    updated_at = datetime.fromisoformat(
                        str(selected["updated_at"]).replace("Z", "+00:00")
                    )
                except ValueError:
                    updated_at = datetime.fromtimestamp(0, timezone.utc)
                if updated_at.timestamp() > stale_before:
                    return None
            if claim:
                connection.execute(
                    """
                    UPDATE talent_pool_openclaw_reports
                    SET status='reporting', updated_at=?, last_error=''
                    WHERE snapshot_id=?
                    """,
                    (_utcnow(), selected["snapshot_id"]),
                )
            result = dict(selected)
        result["bundle"] = json.loads(result.pop("bundle_json"))
        result["ordered_draft_ids"] = json.loads(result.pop("ordered_draft_ids_json"))
        result["deliveries"] = self.delivery_records(result["snapshot_id"])
        return result

    def latest_openclaw_context(
        self, *, session_key: str = "agent:main:main"
    ) -> dict[str, Any] | None:
        """Return the newest committed report that still matches current state."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, s.bundle_json
                FROM talent_pool_openclaw_reports r
                JOIN talent_pool_current_snapshots c
                  ON c.snapshot_id=r.snapshot_id
                 AND c.run_date=r.run_date
                 AND c.direction=r.direction
                JOIN talent_pool_bundle_snapshots s
                  ON s.snapshot_id=r.snapshot_id
                WHERE r.session_key=?
                ORDER BY r.run_date DESC, r.updated_at DESC
                LIMIT 1
                """,
                (session_key,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["bundle"] = json.loads(result.pop("bundle_json"))
        result["ordered_draft_ids"] = json.loads(result.pop("ordered_draft_ids_json"))
        result["deliveries"] = self.delivery_records(result["snapshot_id"])
        return result

    def openclaw_context_by_snapshot(
        self,
        snapshot_id: str,
        *,
        session_key: str = "agent:main:main",
    ) -> dict[str, Any] | None:
        """Return one exact current report for a claimed Agent turn."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, s.bundle_json
                FROM talent_pool_openclaw_reports r
                JOIN talent_pool_current_snapshots c
                  ON c.snapshot_id=r.snapshot_id
                 AND c.run_date=r.run_date
                 AND c.direction=r.direction
                JOIN talent_pool_bundle_snapshots s
                  ON s.snapshot_id=r.snapshot_id
                WHERE r.snapshot_id=? AND r.session_key=?
                """,
                (snapshot_id, session_key),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["bundle"] = json.loads(result.pop("bundle_json"))
        result["ordered_draft_ids"] = json.loads(result.pop("ordered_draft_ids_json"))
        result["deliveries"] = self.delivery_records(result["snapshot_id"])
        return result

    def delivery_records(self, snapshot_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT delivery_channel, status, delivered_at, error_class, error_detail
                FROM talent_pool_delivery_ledger
                WHERE snapshot_id=? ORDER BY delivery_channel
                """,
                (snapshot_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_openclaw_read(self, snapshot_id: str) -> bool:
        """Record that the main Agent loaded the exact bridge-claimed snapshot."""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE talent_pool_openclaw_reports
                SET status='read', updated_at=?, last_error=''
                WHERE snapshot_id=? AND status='reporting'
                """,
                (_utcnow(), snapshot_id),
            )
        return cursor.rowcount == 1

    def mark_openclaw_reported(self, snapshot_id: str) -> bool:
        now = _utcnow()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE talent_pool_openclaw_reports
                SET status='reported', reported_at=?, updated_at=?, last_error=''
                WHERE snapshot_id=? AND status IN ('pending', 'reporting', 'read', 'failed')
                """,
                (now, now, snapshot_id),
            )
            if cursor.rowcount == 1:
                self._record_delivery(
                    connection,
                    snapshot_id,
                    channel="openclaw_hook",
                    status="delivered",
                )
        return cursor.rowcount == 1

    def mark_openclaw_report_failed(
        self, snapshot_id: str, error: BaseException | str
    ) -> bool:
        with self._connect() as connection:
            diagnostic = safe_error(error)
            cursor = connection.execute(
                """
                UPDATE talent_pool_openclaw_reports
                SET status='failed', updated_at=?, last_error=?
                WHERE snapshot_id=? AND status<>'reported'
                """,
                (_utcnow(), diagnostic["error_class"], snapshot_id),
            )
            if cursor.rowcount == 1:
                self._record_delivery(
                    connection,
                    snapshot_id,
                    channel="openclaw_hook",
                    status="failed",
                    error=error,
                )
        return cursor.rowcount == 1

    def record_delivery(
        self,
        snapshot_id: str,
        *,
        channel: str,
        status: str,
        error: BaseException | str = "",
    ) -> None:
        """Persist delivery independently from generation and bridge status.

        Only a successful human-visible delivery enters the cooldown history.  The
        bounded error fields keep operational diagnostics useful without turning
        raw provider responses or accidental secrets into durable state.
        """

        if status not in {"delivered", "failed"}:
            raise ValueError("delivery status must be delivered or failed")
        normalized_channel = re.sub(r"[^a-z0-9_.-]+", "_", channel.casefold()).strip("_")
        if not normalized_channel:
            raise ValueError("delivery channel must not be empty")
        with self._connect() as connection:
            self._record_delivery(
                connection,
                snapshot_id,
                channel=normalized_channel,
                status=status,
                error=error,
            )

    @staticmethod
    def _record_delivery(
        connection: sqlite3.Connection,
        snapshot_id: str,
        *,
        channel: str,
        status: str,
        error: object = "",
    ) -> None:
        now = _utcnow()
        diagnostic = safe_error(error)
        connection.execute(
            """
            INSERT INTO talent_pool_delivery_ledger(
                snapshot_id, delivery_channel, status, delivered_at,
                error_class, error_detail, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id, delivery_channel) DO UPDATE SET
                status=CASE
                    WHEN talent_pool_delivery_ledger.status='delivered'
                    THEN 'delivered' ELSE excluded.status END,
                delivered_at=COALESCE(
                    talent_pool_delivery_ledger.delivered_at,
                    excluded.delivered_at
                ),
                error_class=CASE
                    WHEN talent_pool_delivery_ledger.status='delivered'
                    THEN talent_pool_delivery_ledger.error_class
                    ELSE excluded.error_class END,
                error_detail=CASE
                    WHEN talent_pool_delivery_ledger.status='delivered'
                    THEN talent_pool_delivery_ledger.error_detail
                    ELSE excluded.error_detail END,
                updated_at=excluded.updated_at
            """,
            (
                snapshot_id,
                channel,
                status,
                now if status == "delivered" else None,
                diagnostic["error_class"] if status == "failed" else "",
                diagnostic["detail"] if status == "failed" else "",
                now,
                now,
            ),
        )

    def requeue_openclaw_report(
        self,
        snapshot_id: str,
        *,
        session_key: str = "agent:main:main",
    ) -> bool:
        """Explicitly requeue one exact current report for an operator resend."""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE talent_pool_openclaw_reports
                SET status='pending', reported_at=NULL, updated_at=?, last_error='operator_requeue'
                WHERE snapshot_id=? AND session_key=?
                  AND status IN ('reported', 'failed')
                  AND snapshot_id = (
                    SELECT r2.snapshot_id
                    FROM talent_pool_openclaw_reports r2
                    JOIN talent_pool_current_snapshots c
                      ON c.snapshot_id=r2.snapshot_id
                     AND c.run_date=r2.run_date
                     AND c.direction=r2.direction
                    WHERE r2.session_key=?
                    ORDER BY r2.run_date DESC, r2.created_at DESC
                    LIMIT 1
                  )
                """,
                (_utcnow(), snapshot_id, session_key, session_key),
            )
        return cursor.rowcount == 1

    def current_bundle(
        self,
        run_date: str,
        direction: str,
        *,
        source_run_id: str = "",
    ) -> dict[str, Any] | None:
        """Return the atomically committed current bundle for a daily run."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.snapshot_id, s.bundle_json
                FROM talent_pool_current_snapshots c
                JOIN talent_pool_bundle_snapshots s
                  ON s.snapshot_id=c.snapshot_id
                WHERE c.run_date=? AND c.direction=?
                  AND (?='' OR c.source_run_id=?)
                """,
                (run_date, direction, source_run_id, source_run_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["bundle_json"])
        payload["_snapshot_id"] = row["snapshot_id"]
        return payload

    def historical_bundle_for_source_run(
        self, run_date: str, direction: str, source_run_id: str
    ) -> dict[str, Any] | None:
        """Return a prior immutable snapshot without changing the current pointer."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT snapshot_id, bundle_json FROM talent_pool_bundle_snapshots
                WHERE run_date=? AND direction=? AND source_run_id=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (run_date, direction, source_run_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["bundle_json"])
        payload["_snapshot_id"] = row["snapshot_id"]
        return payload

    def batch(self, run_date: str, direction: str) -> list[dict[str, Any]]:
        self.expire(run_date=run_date, direction=direction)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT d.* FROM talent_pool_drafts d
                JOIN talent_pool_current_snapshot_drafts c
                  ON c.run_date=d.run_date AND c.direction=d.direction
                 AND c.draft_id=d.draft_id
                WHERE d.run_date=? AND d.direction=?
                ORDER BY c.ordinal
                """,
                (run_date, direction),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def find_opportunities(
        self,
        *,
        terms: tuple[str, ...] | list[str] = (),
        direction: str = "",
        current_only: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return persisted company-role-Liepin mappings for later float analysis."""

        if limit <= 0:
            return []
        conditions: list[str] = []
        parameters: list[Any] = []
        if current_only:
            conditions.append("active=1")
        if direction:
            conditions.append("direction=?")
            parameters.append(direction)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM talent_pool_opportunity_links"
                + where
                + " ORDER BY created_at DESC, company, recommended_title",
                parameters,
            ).fetchall()
        normalized_terms = [
            str(item).strip().lower() for item in terms if str(item).strip()
        ]
        output: list[dict[str, Any]] = []
        for row in rows:
            value = _row_to_dict(row)
            searchable = " ".join(
                str(value.get(key) or "")
                for key in (
                    "direction",
                    "recommended_title",
                    "role_family",
                    "talent_persona",
                    "company",
                    "company_role",
                    "liepin_payload_json",
                )
            ).lower()
            if normalized_terms and not any(
                term in searchable for term in normalized_terms
            ):
                continue
            value["evidence_urls"] = json.loads(value.pop("evidence_urls_json"))
            value["liepin_payload"] = json.loads(value.pop("liepin_payload_json"))
            output.append(value)
            if len(output) >= limit:
                break
        return output

    def apply_command(
        self,
        *,
        run_date: str,
        direction: str,
        command: str,
        actor: str,
        expected_snapshot_id: str = "",
        recorded_command: str = "",
    ) -> dict[str, Any]:
        now = _utcnow()
        audit_command = recorded_command.strip() or command
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE talent_pool_drafts SET status='expired', updated_at=?
                WHERE run_date=? AND direction=? AND expires_at < ?
                  AND status IN ('draft', 'pending_approval', 'approved',
                                 'publish_failed', 'rejected')
                """,
                (now, run_date, direction, product_date_iso()),
            )
            rows = connection.execute(
                """
                SELECT d.*, c.snapshot_id FROM talent_pool_drafts d
                JOIN talent_pool_current_snapshot_drafts c
                  ON c.run_date=d.run_date AND c.direction=d.direction
                 AND c.draft_id=d.draft_id
                WHERE d.run_date=? AND d.direction=?
                ORDER BY c.ordinal
                """,
                (run_date, direction),
            ).fetchall()
            if (
                rows
                and expected_snapshot_id
                and any(row["snapshot_id"] != expected_snapshot_id for row in rows)
            ):
                raise RuntimeError(
                    "displayed daily report is no longer current; show the latest report first"
                )
            if expected_snapshot_id:
                report_state = connection.execute(
                    """
                    SELECT status FROM talent_pool_openclaw_reports
                    WHERE snapshot_id=?
                    """,
                    (expected_snapshot_id,),
                ).fetchone()
                if report_state is None or report_state["status"] != "reported":
                    raise RuntimeError(
                        "current daily report has not been shown completely; report it before approval"
                    )
            parsed = parse_approval_command(command, draft_count=len(rows))
            if parsed is None:
                raise ValueError("指令不明确；未执行审批或发布")
            selected = [rows[index - 1] for index in parsed.indexes]
            if parsed.action == "view":
                return {
                    "action": "view",
                    "draft": json.loads(selected[0]["draft_json"]),
                }
            new_status = "approved" if parsed.action == "publish" else "rejected"
            for row in selected:
                if row["status"] in {"published", "expired"}:
                    continue
                if row["status"] not in {
                    "draft",
                    "pending_approval",
                    "approved",
                    "publish_failed",
                    "rejected",
                }:
                    raise ValueError(
                        f"{row['draft_id']} is not approvable from {row['status']}"
                    )
                cursor = connection.execute(
                    """
                    UPDATE talent_pool_drafts SET status=?, approved_at=?,
                        approved_by=?, approval_command=?, updated_at=?,
                        last_error_code=NULL, last_error_message=NULL
                    WHERE draft_id=? AND payload_hash=?
                      AND EXISTS (
                        SELECT 1 FROM talent_pool_current_snapshot_drafts c
                        WHERE c.run_date=? AND c.direction=?
                          AND c.snapshot_id=? AND c.draft_id=?
                      )
                    """,
                    (
                        new_status,
                        now if new_status == "approved" else None,
                        actor if new_status == "approved" else None,
                        audit_command if new_status == "approved" else None,
                        now,
                        row["draft_id"],
                        row["payload_hash"],
                        run_date,
                        direction,
                        row["snapshot_id"],
                        row["draft_id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "current talent-pool snapshot changed during approval"
                    )
                connection.execute(
                    """
                    INSERT INTO talent_pool_approval_events(
                        draft_id, actor, command, action, payload_hash,
                        source_run_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["draft_id"],
                        actor,
                        audit_command,
                        parsed.action,
                        row["payload_hash"],
                        row["source_run_id"],
                        now,
                    ),
                )
        return {
            "action": parsed.action,
            "draft_ids": [row["draft_id"] for row in selected],
        }

    def expire(self, *, run_date: str, direction: str, today: str | None = None) -> int:
        today = today or product_date_iso()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE talent_pool_drafts SET status='expired', updated_at=?
                WHERE run_date=? AND direction=? AND expires_at < ?
                  AND status IN ('draft', 'pending_approval', 'approved',
                                 'publish_failed', 'rejected')
                """,
                (_utcnow(), run_date, direction, today),
            )
        return cursor.rowcount

    def acquire_publish_lease(self, run_date: str, direction: str) -> str:
        token = secrets.token_hex(16)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            unresolved = connection.execute(
                """
                SELECT 1 FROM talent_pool_drafts d
                WHERE (
                    d.status='publishing'
                    OR EXISTS (
                      SELECT 1 FROM talent_pool_publish_attempts a
                      WHERE a.draft_id=d.draft_id
                        AND a.outcome IN ('started', 'ambiguous')
                    )
                  )
                LIMIT 1
                """,
            ).fetchone()
            if unresolved:
                raise RuntimeError("unresolved publish attempt requires manual review")
            lease = connection.execute(
                """
                SELECT lease_token FROM talent_pool_publish_leases
                WHERE lease_key='liepin-account' AND released_at IS NULL
                """,
            ).fetchone()
            if lease:
                raise RuntimeError("another serial publish queue is active")
            connection.execute(
                """
                INSERT INTO talent_pool_publish_leases(
                    lease_key, run_date, direction, lease_token,
                    acquired_at, released_at
                ) VALUES ('liepin-account', ?, ?, ?, ?, NULL)
                ON CONFLICT(lease_key) DO UPDATE SET
                    run_date=excluded.run_date,
                    direction=excluded.direction,
                    lease_token=excluded.lease_token,
                    acquired_at=excluded.acquired_at,
                    released_at=NULL
                """,
                (run_date, direction, token, _utcnow()),
            )
        return token

    def release_publish_lease(self, run_date: str, direction: str, token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE talent_pool_publish_leases SET released_at=?
                WHERE lease_key='liepin-account' AND lease_token=?
                  AND run_date=? AND direction=?
                """,
                (_utcnow(), token, run_date, direction),
            )

    def begin_publish(
        self, draft_id: str, *, lease_token: str
    ) -> tuple[dict[str, Any], str] | None:
        """Atomically claim an approved current-snapshot draft."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT d.*, c.snapshot_id
                FROM talent_pool_drafts d
                JOIN talent_pool_current_snapshot_drafts c
                  ON c.run_date=d.run_date AND c.direction=d.direction
                 AND c.draft_id=d.draft_id
                WHERE d.draft_id=?
                """,
                (draft_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"{draft_id} is not in the current snapshot")
            lease = connection.execute(
                """
                SELECT 1 FROM talent_pool_publish_leases
                WHERE lease_key='liepin-account' AND lease_token=?
                  AND run_date=? AND direction=? AND released_at IS NULL
                """,
                (lease_token, row["run_date"], row["direction"]),
            ).fetchone()
            if lease is None:
                raise RuntimeError("matching active serial publish lease is required")
            if row["status"] == "published":
                return None
            if row["status"] != "approved":
                raise ValueError(f"{draft_id} is not approved")
            if row["expires_at"] < product_date_iso():
                connection.execute(
                    "UPDATE talent_pool_drafts SET status='expired', updated_at=? "
                    "WHERE draft_id=?",
                    (_utcnow(), draft_id),
                )
                connection.commit()
                raise ValueError(f"{draft_id} is expired")
            draft = json.loads(row["draft_json"])
            actual_hash = canonical_payload_hash(draft["public_payload"])
            if actual_hash != row["payload_hash"]:
                connection.execute(
                    """
                    UPDATE talent_pool_drafts SET status='pending_approval',
                        approved_at=NULL, approved_by=NULL, approval_command=NULL,
                        updated_at=? WHERE draft_id=?
                    """,
                    (_utcnow(), draft_id),
                )
                connection.commit()
                raise ValueError(f"{draft_id} payload changed; approval invalidated")
            prior = connection.execute(
                """
                SELECT outcome FROM talent_pool_publish_attempts
                WHERE draft_id=? AND payload_hash=?
                ORDER BY id DESC LIMIT 1
                """,
                (draft_id, actual_hash),
            ).fetchone()
            if prior and prior["outcome"] == "published":
                return None
            if prior and prior["outcome"] in {"started", "ambiguous"}:
                raise RuntimeError(
                    f"{draft_id} has an unresolved prior publish attempt"
                )
            attempt_number = (
                connection.execute(
                    "SELECT COUNT(*) FROM talent_pool_publish_attempts "
                    "WHERE draft_id=? AND payload_hash=?",
                    (draft_id, actual_hash),
                ).fetchone()[0]
                + 1
            )
            attempt_key = f"{draft_id}:{actual_hash}:{attempt_number}"
            connection.execute(
                """
                INSERT INTO talent_pool_publish_attempts(
                    draft_id, payload_hash, attempt_key, source_run_id,
                    started_at, outcome
                ) VALUES (?, ?, ?, ?, ?, 'started')
                """,
                (draft_id, actual_hash, attempt_key, row["source_run_id"], _utcnow()),
            )
            cursor = connection.execute(
                "UPDATE talent_pool_drafts SET status='publishing', updated_at=? "
                "WHERE draft_id=? AND payload_hash=? AND status='approved'",
                (_utcnow(), draft_id, actual_hash),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("draft changed while publish was being claimed")
        return _row_to_dict(row), attempt_key

    def finish_publish(
        self,
        *,
        draft_id: str,
        attempt_key: str,
        outcome: str,
        job_id: str = "",
        job_url: str = "",
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        if outcome not in {"published", "failed", "ambiguous"}:
            raise ValueError("invalid publish outcome")
        status = "published" if outcome == "published" else "publish_failed"
        now = _utcnow()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            attempt = connection.execute(
                """
                SELECT draft_id, payload_hash, outcome
                FROM talent_pool_publish_attempts WHERE attempt_key=?
                """,
                (attempt_key,),
            ).fetchone()
            if attempt is None:
                raise KeyError(attempt_key)
            if attempt["draft_id"] != draft_id:
                raise ValueError("publish attempt does not belong to draft")
            if attempt["outcome"] != "started":
                raise ValueError("publish attempt is not open")
            draft = connection.execute(
                "SELECT payload_hash, status FROM talent_pool_drafts WHERE draft_id=?",
                (draft_id,),
            ).fetchone()
            if draft is None or draft["payload_hash"] != attempt["payload_hash"]:
                raise RuntimeError("draft payload no longer matches publish attempt")
            if draft["status"] != "publishing":
                raise RuntimeError("draft is not currently publishing")
            attempt_cursor = connection.execute(
                """
                UPDATE talent_pool_publish_attempts SET finished_at=?, outcome=?,
                    error_code=?, error_message=?, job_id=?, job_url=?
                WHERE attempt_key=? AND draft_id=? AND payload_hash=?
                  AND outcome='started'
                """,
                (
                    now,
                    outcome,
                    sanitize_text(error_code, limit=120),
                    sanitize_text(error_message, limit=1000),
                    job_id,
                    sanitize_url(job_url, limit=1000),
                    attempt_key,
                    draft_id,
                    attempt["payload_hash"],
                ),
            )
            draft_cursor = connection.execute(
                """
                UPDATE talent_pool_drafts SET status=?, liepin_job_id=?,
                    liepin_job_url=?, published_at=?, last_error_code=?,
                    last_error_message=?, updated_at=?
                WHERE draft_id=? AND payload_hash=? AND status='publishing'
                """,
                (
                    status,
                    job_id,
                    sanitize_url(job_url, limit=1000),
                    now if outcome == "published" else None,
                    sanitize_text(error_code, limit=120),
                    sanitize_text(error_message, limit=1000),
                    now,
                    draft_id,
                    attempt["payload_hash"],
                ),
            )
            if attempt_cursor.rowcount != 1 or draft_cursor.rowcount != 1:
                raise RuntimeError("publish completion lost its atomic claim")

    def approved_ids(self, run_date: str, direction: str) -> list[str]:
        return [
            row["draft_id"]
            for row in self.batch(run_date, direction)
            if row["status"] == "approved"
        ]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _utcnow() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


__all__ = [
    "ApprovalCommand",
    "COMPLETION_STATUS_ENUMS",
    "TalentPoolStore",
    "parse_approval_command",
]
