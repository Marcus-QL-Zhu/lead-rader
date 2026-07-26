"""SQLite state, approval audit and idempotency for talent-pool drafts."""

from __future__ import annotations

import json
import secrets
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .talent_pool import canonical_payload_hash


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
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def save_bundle(self, bundle: Mapping[str, Any]) -> int:
        run_date = str(bundle.get("run_date") or "")
        direction = str(bundle.get("direction") or "")
        source_run_id = str(bundle.get("source_run_id") or "")
        if not source_run_id:
            raise ValueError("draft bundle requires source_run_id")
        drafts = bundle.get("drafts") or ()
        now = _utcnow()
        with self._connect() as connection:
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
            connection.execute(
                """
                UPDATE talent_pool_drafts SET status='expired', updated_at=?
                WHERE run_date=? AND direction=? AND source_run_id<>?
                  AND status NOT IN ('published', 'expired')
                """,
                (now, run_date, direction, source_run_id),
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
                    and current["payload_hash"] == payload_hash
                    and (
                        current["source_run_id"] == source_run_id
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
        return len(drafts)

    def batch(self, run_date: str, direction: str) -> list[dict[str, Any]]:
        self.expire(run_date=run_date, direction=direction)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT d.* FROM talent_pool_drafts d
                JOIN talent_pool_current_batches b
                  ON b.run_date=d.run_date AND b.direction=d.direction
                 AND b.source_run_id=d.source_run_id
                WHERE d.run_date=? AND d.direction=?
                ORDER BY d.ordinal
                """,
                (run_date, direction),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def apply_command(
        self,
        *,
        run_date: str,
        direction: str,
        command: str,
        actor: str,
    ) -> dict[str, Any]:
        rows = self.batch(run_date, direction)
        parsed = parse_approval_command(command, draft_count=len(rows))
        if parsed is None:
            raise ValueError("指令不明确；未执行审批或发布")
        selected = [rows[index - 1] for index in parsed.indexes]
        if parsed.action == "view":
            return {
                "action": "view",
                "draft": json.loads(selected[0]["draft_json"]),
            }
        now = _utcnow()
        new_status = "approved" if parsed.action == "publish" else "rejected"
        with self._connect() as connection:
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
                connection.execute(
                    """
                    UPDATE talent_pool_drafts SET status=?, approved_at=?,
                        approved_by=?, approval_command=?, updated_at=?,
                        last_error_code=NULL, last_error_message=NULL
                    WHERE draft_id=?
                    """,
                    (
                        new_status,
                        now if new_status == "approved" else None,
                        actor if new_status == "approved" else None,
                        command if new_status == "approved" else None,
                        now,
                        row["draft_id"],
                    ),
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
                        command,
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
        today = today or date.today().isoformat()
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
                """,
                (_utcnow(), token),
            )

    def begin_publish(
        self, draft_id: str, *, lease_token: str
    ) -> tuple[dict[str, Any], str] | None:
        """Atomically claim an approved draft; return None when already handled."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM talent_pool_drafts WHERE draft_id=?", (draft_id,)
            ).fetchone()
            if row is None:
                raise KeyError(draft_id)
            lease = connection.execute(
                """
                SELECT 1 FROM talent_pool_publish_leases
                WHERE lease_key='liepin-account' AND lease_token=?
                  AND released_at IS NULL
                """,
                (lease_token,),
            ).fetchone()
            if lease is None:
                raise RuntimeError("active serial publish lease is required")
            if row["status"] == "published":
                return None
            if row["status"] != "approved":
                raise ValueError(f"{draft_id} is not approved")
            if row["expires_at"] < date.today().isoformat():
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
                ORDER BY id DESC
                LIMIT 1
                """,
                (draft_id, actual_hash),
            ).fetchone()
            if prior and prior["outcome"] == "published":
                return None
            if prior and prior["outcome"] in {"started", "ambiguous"}:
                raise RuntimeError(
                    f"{draft_id} has an unresolved prior publish attempt"
                )
            attempt_number = connection.execute(
                "SELECT COUNT(*) FROM talent_pool_publish_attempts "
                "WHERE draft_id=? AND payload_hash=?",
                (draft_id, actual_hash),
            ).fetchone()[0] + 1
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
            connection.execute(
                "UPDATE talent_pool_drafts SET status='publishing', updated_at=? "
                "WHERE draft_id=?",
                (_utcnow(), draft_id),
            )
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
            connection.execute(
                """
                UPDATE talent_pool_publish_attempts SET finished_at=?, outcome=?,
                    error_code=?, error_message=?, job_id=?, job_url=?
                WHERE attempt_key=?
                """,
                (
                    now,
                    outcome,
                    error_code,
                    error_message[:1000],
                    job_id,
                    job_url,
                    attempt_key,
                ),
            )
            connection.execute(
                """
                UPDATE talent_pool_drafts SET status=?, liepin_job_id=?,
                    liepin_job_url=?, published_at=?, last_error_code=?,
                    last_error_message=?, updated_at=?
                WHERE draft_id=?
                """,
                (
                    status,
                    job_id,
                    job_url,
                    now if outcome == "published" else None,
                    error_code,
                    error_message[:1000],
                    now,
                    draft_id,
                ),
            )

    def approved_ids(self, run_date: str, direction: str) -> list[str]:
        return [
            row["draft_id"]
            for row in self.batch(run_date, direction)
            if row["status"] == "approved"
        ]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "ApprovalCommand",
    "TalentPoolStore",
    "parse_approval_command",
]
