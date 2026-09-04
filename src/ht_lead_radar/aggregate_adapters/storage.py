"""SQLite persistence for incremental aggregate-source collection."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
import unicodedata

from .models import AdapterRun, CleanArticle, SemanticEvent, SourceArticleIndex
from .diagnostics import redact_diagnostic, sanitize_semantic_audit
from ..sanitization import sanitize_tree, sanitize_url


_PERSISTENCE_SANITIZER_VERSION = "4"

_TERMINAL_SEMANTIC_STATUSES = frozenset(
    {
        "accepted",
        "repaired",
        "rules_only",
        "no_rule_seed",
        "prefiltered",
        "no_claims",
    }
)


_COMPANY_PUNCTUATION = re.compile(
    r"[\s\-_.,\uFF0C\u3002\u00B7:\uFF1A;\uFF1B'\""
    r"\u201C\u201D\u2018\u2019()\uFF08\uFF09\[\]\u3010\u3011]+"
)
_LEGAL_COMPANY_SUFFIX = re.compile(
    r"(?:\u6709\u9650\u8d23\u4efb\u516c\u53f8|"
    r"\u80a1\u4efd\u6709\u9650\u516c\u53f8|"
    r"\u6709\u9650\u516c\u53f8|\u96c6\u56e2|"
    r"Corporation|Company|Co\.?|Inc\.?|Ltd\.?)$",
    re.I,
)


def normalize_company_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    return _COMPANY_PUNCTUATION.sub("", normalized).casefold()


def _looks_legal_company(value: str) -> bool:
    return bool(_LEGAL_COMPANY_SUFFIX.search(str(value or "").strip()))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AggregateStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = path
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "AggregateStateStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS aggregate_article_index (
                source_id TEXT NOT NULL,
                source_article_id TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                published_at TEXT NOT NULL DEFAULT '',
                discovered_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                index_json TEXT NOT NULL,
                PRIMARY KEY (source_id, source_article_id),
                UNIQUE (source_id, canonical_url)
            );
            CREATE TABLE IF NOT EXISTS aggregate_clean_articles (
                source_id TEXT NOT NULL,
                source_article_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                article_json TEXT NOT NULL,
                PRIMARY KEY (source_id, source_article_id)
            );
            CREATE TABLE IF NOT EXISTS aggregate_semantic_events (
                source_id TEXT NOT NULL,
                source_article_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                processor TEXT NOT NULL,
                prompt_version TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                event_json TEXT NOT NULL,
                PRIMARY KEY (source_id, source_article_id, event_key)
            );
            CREATE TABLE IF NOT EXISTS aggregate_company_aliases (
                source_id TEXT NOT NULL,
                source_article_id TEXT NOT NULL,
                alias_key TEXT NOT NULL,
                alias TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                canonical_company TEXT NOT NULL,
                evidence_quote TEXT NOT NULL DEFAULT '',
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (
                    source_id, source_article_id, alias_key, canonical_key
                )
            );
            CREATE INDEX IF NOT EXISTS idx_aggregate_alias_key
            ON aggregate_company_aliases(alias_key);
            CREATE INDEX IF NOT EXISTS idx_aggregate_canonical_key
            ON aggregate_company_aliases(canonical_key);
            CREATE TABLE IF NOT EXISTS aggregate_semantic_attempts (
                source_id TEXT NOT NULL,
                source_article_id TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                status TEXT NOT NULL,
                validation_error TEXT NOT NULL DEFAULT '',
                first_response TEXT NOT NULL DEFAULT '',
                repair_response TEXT NOT NULL DEFAULT '',
                audit_json TEXT NOT NULL,
                PRIMARY KEY (source_id, source_article_id, prompt_version)
            );
            CREATE TABLE IF NOT EXISTS aggregate_source_cursor (
                source_id TEXT PRIMARY KEY,
                cursor_value TEXT NOT NULL DEFAULT '',
                last_success_at TEXT NOT NULL DEFAULT '',
                last_listing_hash TEXT NOT NULL DEFAULT '',
                last_listing_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS aggregate_dead_letters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                source_article_id TEXT NOT NULL DEFAULT '',
                canonical_url TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL,
                error TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                first_failed_at TEXT NOT NULL,
                last_failed_at TEXT NOT NULL,
                resolved_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS aggregate_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                adapter_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                run_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_aggregate_index_published
            ON aggregate_article_index(source_id, published_at);
            CREATE INDEX IF NOT EXISTS idx_aggregate_events_company
            ON aggregate_semantic_events(source_id, source_article_id);
            CREATE INDEX IF NOT EXISTS idx_aggregate_dead_letters_open
            ON aggregate_dead_letters(source_id, resolved_at);
            CREATE TABLE IF NOT EXISTS aggregate_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        # Purge legacy raw model responses and recursively sanitize old audit
        # rows once. Raw responses are not required for production operations.
        sanitizer = self.connection.execute(
            "SELECT value FROM aggregate_metadata WHERE key='audit_sanitizer_version'"
        ).fetchone()
        if not sanitizer or str(sanitizer["value"]) != _PERSISTENCE_SANITIZER_VERSION:
            rows = self.connection.execute(
                "SELECT rowid, validation_error, audit_json "
                "FROM aggregate_semantic_attempts"
            ).fetchall()
            for row in rows:
                try:
                    audit = json.loads(str(row["audit_json"]))
                except (TypeError, json.JSONDecodeError):
                    audit = {}
                safe_audit = sanitize_semantic_audit(
                    audit if isinstance(audit, dict) else {}
                )
                self.connection.execute(
                    """
                    UPDATE aggregate_semantic_attempts
                    SET validation_error=?, first_response='', repair_response='', audit_json=?
                    WHERE rowid=?
                    """,
                    (
                        redact_diagnostic(
                            row["validation_error"]
                            or (audit.get("error") if isinstance(audit, dict) else "")
                        ),
                        json.dumps(safe_audit, ensure_ascii=False, sort_keys=True),
                        int(row["rowid"]),
                    ),
                )
            dead_letters = self.connection.execute(
                "SELECT id, canonical_url, error FROM aggregate_dead_letters"
            ).fetchall()
            for row in dead_letters:
                self.connection.execute(
                    "UPDATE aggregate_dead_letters SET canonical_url=?, error=? WHERE id=?",
                    (
                        sanitize_url(row["canonical_url"], limit=2000),
                        redact_diagnostic(row["error"], limit=2000),
                        int(row["id"]),
                    ),
                )
            runs = self.connection.execute(
                "SELECT id, run_json FROM aggregate_runs"
            ).fetchall()
            for row in runs:
                try:
                    run_payload = json.loads(str(row["run_json"]))
                except (TypeError, json.JSONDecodeError):
                    run_payload = {}
                run_payload = sanitize_tree(run_payload, redact_pii=True)
                self.connection.execute(
                    "UPDATE aggregate_runs SET run_json=? WHERE id=?",
                    (
                        json.dumps(run_payload, ensure_ascii=False, sort_keys=True),
                        int(row["id"]),
                    ),
                )
            for row in self.connection.execute(
                "SELECT rowid, evidence_quote FROM aggregate_company_aliases"
            ).fetchall():
                self.connection.execute(
                    "UPDATE aggregate_company_aliases SET evidence_quote=? "
                    "WHERE rowid=?",
                    (
                        sanitize_tree(row["evidence_quote"], redact_pii=True),
                        int(row["rowid"]),
                    ),
                )
            self.connection.execute(
                """
                INSERT INTO aggregate_metadata (key, value)
                VALUES ('audit_sanitizer_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (_PERSISTENCE_SANITIZER_VERSION,),
            )
            self._sanitize_legacy_urls_and_json()
        self.connection.commit()

    @staticmethod
    def _safe_json(value: object, *, default: Any) -> str:
        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = default
        return json.dumps(
            sanitize_tree(parsed, redact_pii=True),
            ensure_ascii=False,
            sort_keys=True,
        )

    def _sanitize_legacy_urls_and_json(self) -> None:
        """Clean URL credentials and nested diagnostics in pre-v3 rows."""

        for row in self.connection.execute(
            "SELECT rowid, source_id, canonical_url, index_json "
            "FROM aggregate_article_index"
        ).fetchall():
            payload = self._safe_json(row["index_json"], default={})
            safe_url = sanitize_url(row["canonical_url"], limit=4000)
            conflict = self.connection.execute(
                "SELECT 1 FROM aggregate_article_index "
                "WHERE source_id=? AND canonical_url=? AND rowid<>? LIMIT 1",
                (row["source_id"], safe_url, int(row["rowid"])),
            ).fetchone()
            if conflict:
                separator = "&" if "?" in safe_url else "?"
                safe_url = (
                    f"{safe_url}{separator}dedupe_article={int(row['rowid'])}"
                )
            try:
                payload_value = json.loads(payload)
            except json.JSONDecodeError:
                payload_value = {}
            if isinstance(payload_value, dict):
                payload_value["canonical_url"] = safe_url
                payload = json.dumps(
                    payload_value,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            self.connection.execute(
                "UPDATE aggregate_article_index "
                "SET canonical_url=?, index_json=? WHERE rowid=?",
                (
                    safe_url,
                    payload,
                    int(row["rowid"]),
                ),
            )
        for table, json_column in (
            ("aggregate_clean_articles", "article_json"),
            ("aggregate_semantic_events", "event_json"),
        ):
            rows = self.connection.execute(
                f"SELECT rowid, {json_column} FROM {table}"
            ).fetchall()
            for row in rows:
                self.connection.execute(
                    f"UPDATE {table} SET {json_column}=? WHERE rowid=?",
                    (self._safe_json(row[json_column], default={}), int(row["rowid"])),
                )

    def upsert_index(self, index: SourceArticleIndex) -> bool:
        safe_index = replace(
            index,
            canonical_url=sanitize_url(index.canonical_url, limit=4000),
            listing_page=sanitize_url(index.listing_page, limit=4000),
            structured_data=sanitize_tree(index.structured_data, redact_pii=True),
        )
        row = self.connection.execute(
            """
            SELECT content_hash FROM aggregate_article_index
            WHERE source_id = ? AND source_article_id = ?
            """,
            (safe_index.source_id, safe_index.source_article_id),
        ).fetchone()
        changed = row is None or str(row["content_hash"]) != safe_index.content_hash
        self.connection.execute(
            """
            INSERT INTO aggregate_article_index (
                source_id, source_article_id, canonical_url, published_at,
                discovered_at, last_seen_at, content_hash, index_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, source_article_id) DO UPDATE SET
                canonical_url = excluded.canonical_url,
                published_at = excluded.published_at,
                last_seen_at = excluded.last_seen_at,
                content_hash = excluded.content_hash,
                index_json = excluded.index_json
            """,
            (
                safe_index.source_id,
                safe_index.source_article_id,
                safe_index.canonical_url,
                safe_index.published_at,
                safe_index.discovered_at,
                _now(),
                safe_index.content_hash,
                json.dumps(
                    sanitize_tree(safe_index.to_dict(), redact_pii=True),
                    ensure_ascii=False,
                ),
            ),
        )
        self.connection.commit()
        return changed

    def article_is_current(
        self,
        index: SourceArticleIndex,
        *,
        now: datetime | None = None,
        overlap_hours: int = 48,
        recheck_after_hours: int = 12,
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT article_json, fetched_at
            FROM aggregate_clean_articles
            WHERE source_id = ? AND source_article_id = ?
            """,
            (index.source_id, index.source_article_id),
        ).fetchone()
        if row is None:
            return False
        try:
            stored = json.loads(str(row["article_json"]))
        except json.JSONDecodeError:
            return False
        stored_index = stored.get("index") if isinstance(stored, dict) else None
        if not (
            isinstance(stored_index, dict)
            and stored_index.get("content_hash") == index.content_hash
        ):
            return False
        if now is None or overlap_hours <= 0 or recheck_after_hours <= 0:
            return True
        active_now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        try:
            published = datetime.fromisoformat(index.published_at[:10]).replace(
                tzinfo=timezone.utc
            )
            fetched = datetime.fromisoformat(
                str(row["fetched_at"]).replace("Z", "+00:00")
            )
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return True
        age_hours = (active_now - published).total_seconds() / 3600
        if age_hours < 0 or age_hours > overlap_hours:
            return True
        since_fetch_hours = (active_now - fetched).total_seconds() / 3600
        return since_fetch_hours < recheck_after_hours

    def semantic_is_current(
        self,
        index: SourceArticleIndex,
        *,
        prompt_version: str,
        model_identity: str,
        claim_contract_version: str | None = None,
        claim_centric_v27: bool | None = None,
        strict_claim_contract: bool | None = None,
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT audit_json
            FROM aggregate_semantic_attempts
            WHERE source_id = ? AND source_article_id = ? AND prompt_version = ?
            """,
            (index.source_id, index.source_article_id, prompt_version),
        ).fetchone()
        if row is None:
            return False
        try:
            audit = json.loads(str(row["audit_json"]))
        except json.JSONDecodeError:
            return False
        if not isinstance(audit, dict):
            return False
        if audit.get("status") not in _TERMINAL_SEMANTIC_STATUSES:
            return False
        audit = self._repair_legacy_v27_semantic_hashes(
            source_id=index.source_id,
            source_article_id=index.source_article_id,
            prompt_version=prompt_version,
            audit=audit,
        )
        if audit.get("index_content_hash") != index.content_hash:
            return False
        if audit.get("model_identity") != model_identity:
            return False
        if (
            claim_contract_version is not None
            and audit.get("claim_contract_version") != claim_contract_version
        ):
            return False
        if (
            claim_centric_v27 is not None
            and bool(audit.get("claim_centric_v27")) != claim_centric_v27
        ):
            return False
        if (
            strict_claim_contract is not None
            and bool(audit.get("strict_claim_contract")) != strict_claim_contract
        ):
            return False
        return self._semantic_audit_has_complete_materialization(
            index.source_id,
            index.source_article_id,
            audit,
        )

    def has_prior_semantic_attempt(self, index: SourceArticleIndex) -> bool:
        """Return whether this unchanged article has any semantic history."""

        rows = self.connection.execute(
            """
            SELECT audit_json
            FROM aggregate_semantic_attempts
            WHERE source_id = ? AND source_article_id = ?
            ORDER BY attempted_at DESC
            """,
            (index.source_id, index.source_article_id),
        ).fetchall()
        for row in rows:
            try:
                audit = json.loads(str(row["audit_json"]))
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(audit, dict):
                continue
            audit = self._repair_legacy_v27_semantic_hashes(
                source_id=index.source_id,
                source_article_id=index.source_article_id,
                prompt_version=str(audit.get("prompt_version") or ""),
                audit=audit,
            )
            if (
                audit.get("index_content_hash") == index.content_hash
                and audit.get("status") in _TERMINAL_SEMANTIC_STATUSES
                and self._semantic_audit_has_complete_materialization(
                    index.source_id,
                    index.source_article_id,
                    audit,
                )
            ):
                return True
        return False

    def rebind_semantic_cache(
        self,
        index: SourceArticleIndex,
        *,
        article_content_hash: str,
        prompt_version: str,
        model_identity: str,
        claim_contract_version: str | None = None,
        claim_centric_v27: bool | None = None,
        strict_claim_contract: bool | None = None,
    ) -> bool:
        """Rebind a valid semantic audit after listing-only hash drift.

        A detail fetch is still required before this method is called.  Reuse
        is allowed only when the clean article body is byte-for-byte unchanged
        and the prompt/model/contract namespace is current.  This avoids a
        needless MiniMax call without hiding a real article edit.
        """

        if not article_content_hash:
            return False
        row = self.connection.execute(
            """
            SELECT audit_json
            FROM aggregate_semantic_attempts
            WHERE source_id = ? AND source_article_id = ? AND prompt_version = ?
            """,
            (index.source_id, index.source_article_id, prompt_version),
        ).fetchone()
        if row is None:
            return False
        try:
            audit = json.loads(str(row["audit_json"]))
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(audit, dict):
            return False
        if audit.get("status") not in _TERMINAL_SEMANTIC_STATUSES:
            return False
        audit = self._repair_legacy_v27_semantic_hashes(
            source_id=index.source_id,
            source_article_id=index.source_article_id,
            prompt_version=prompt_version,
            audit=audit,
        )
        if audit.get("article_content_hash") != article_content_hash:
            return False
        if audit.get("model_identity") != model_identity:
            return False
        if (
            claim_contract_version is not None
            and audit.get("claim_contract_version") != claim_contract_version
        ):
            return False
        if (
            claim_centric_v27 is not None
            and bool(audit.get("claim_centric_v27")) != claim_centric_v27
        ):
            return False
        if not self._semantic_audit_has_complete_materialization(
            index.source_id,
            index.source_article_id,
            audit,
        ):
            return False
        if (
            strict_claim_contract is not None
            and bool(audit.get("strict_claim_contract")) != strict_claim_contract
        ):
            return False
        previous_hash = str(audit.get("index_content_hash") or "")
        if previous_hash == index.content_hash:
            return True
        audit["index_content_hash"] = index.content_hash
        audit["cache_rebound_from_index_content_hash"] = previous_hash
        audit["cache_rebound_at"] = _now()
        safe_audit = sanitize_semantic_audit(audit)
        self.connection.execute(
            """
            UPDATE aggregate_semantic_attempts
            SET audit_json = ?
            WHERE source_id = ? AND source_article_id = ? AND prompt_version = ?
            """,
            (
                json.dumps(safe_audit, ensure_ascii=False, sort_keys=True),
                index.source_id,
                index.source_article_id,
                prompt_version,
            ),
        )
        self.connection.commit()
        return True

    def _repair_legacy_v27_semantic_hashes(
        self,
        *,
        source_id: str,
        source_article_id: str,
        prompt_version: str,
        audit: dict[str, Any],
    ) -> dict[str, Any]:
        """Safely recover hashes omitted by the original V27 audit writer.

        The repair is deliberately fail closed.  It only trusts the persisted
        clean article when every materialized event belongs to that body, the
        recorded event count agrees, and no unresolved failure exists for the
        article.  This prevents the deployment fix from turning the historical
        V27 corpus into a one-time MiniMax backfill.
        """

        if audit.get("index_content_hash") and audit.get("article_content_hash"):
            return audit
        if (
            audit.get("status") not in _TERMINAL_SEMANTIC_STATUSES
            or audit.get("claim_centric_v27") is not True
            or "strict_claim_contract" not in audit
            or not str(audit.get("claim_contract_version") or "")
            or not str(audit.get("model_identity") or "")
            or not prompt_version
        ):
            return audit
        if self.has_open_dead_letter(
            source_id=source_id,
            source_article_id=source_article_id,
        ):
            return audit
        try:
            expected_events = int(audit["final_event_count"])
        except (KeyError, TypeError, ValueError):
            return audit
        if expected_events < 0:
            return audit
        clean = self.connection.execute(
            """
            SELECT c.content_hash, c.article_json, c.fetched_at, a.attempted_at
            FROM aggregate_clean_articles AS c
            JOIN aggregate_semantic_attempts AS a
              ON a.source_id = c.source_id
             AND a.source_article_id = c.source_article_id
             AND a.prompt_version = ?
            WHERE c.source_id = ? AND c.source_article_id = ?
            """,
            (prompt_version, source_id, source_article_id),
        ).fetchone()
        if clean is None:
            return audit
        clean_hash = str(clean["content_hash"] or "")
        try:
            clean_payload = json.loads(str(clean["article_json"]))
            embedded_hash = str(clean_payload.get("content_hash") or "")
            embedded_index = clean_payload.get("index") or {}
            embedded_index_hash = str(embedded_index.get("content_hash") or "")
        except (AttributeError, TypeError, json.JSONDecodeError):
            return audit
        if not clean_hash or clean_hash != embedded_hash or not embedded_index_hash:
            return audit
        if expected_events == 0:
            # A zero-event audit has no event row that can carry the body hash.
            # It is recoverable only when the clean article was durably written
            # before this exact audit and has not been rewritten since. Equal
            # second-resolution timestamps remain ambiguous and fail closed.
            try:
                fetched_at = datetime.fromisoformat(str(clean["fetched_at"]))
                attempted_at = datetime.fromisoformat(str(clean["attempted_at"]))
            except (TypeError, ValueError):
                return audit
            if fetched_at >= attempted_at:
                return audit
        counts = self.connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN content_hash = ? THEN 1 ELSE 0 END) AS body_matching,
                SUM(CASE WHEN prompt_version = ? THEN 1 ELSE 0 END) AS prompt_matching
            FROM aggregate_semantic_events
            WHERE source_id = ? AND source_article_id = ?
            """,
            (clean_hash, prompt_version, source_id, source_article_id),
        ).fetchone()
        total = int(counts["total"] if counts else 0)
        body_matching = int((counts["body_matching"] if counts else 0) or 0)
        prompt_matching = int((counts["prompt_matching"] if counts else 0) or 0)
        if (
            total != expected_events
            or body_matching != expected_events
            or prompt_matching != expected_events
        ):
            return audit
        existing_index_hash = str(audit.get("index_content_hash") or "")
        existing_article_hash = str(audit.get("article_content_hash") or "")
        if existing_index_hash and existing_index_hash != embedded_index_hash:
            return audit
        if existing_article_hash and existing_article_hash != clean_hash:
            return audit
        repaired = dict(audit)
        repaired["index_content_hash"] = embedded_index_hash
        repaired["article_content_hash"] = clean_hash
        repaired["legacy_hashes_recovered_at"] = _now()
        safe_audit = sanitize_semantic_audit(repaired)
        self.connection.execute(
            """
            UPDATE aggregate_semantic_attempts
            SET audit_json = ?
            WHERE source_id = ? AND source_article_id = ? AND prompt_version = ?
            """,
            (
                json.dumps(safe_audit, ensure_ascii=False, sort_keys=True),
                source_id,
                source_article_id,
                prompt_version,
            ),
        )
        self.connection.commit()
        return safe_audit

    def _semantic_audit_has_complete_materialization(
        self,
        source_id: str,
        source_article_id: str,
        audit: dict[str, Any],
    ) -> bool:
        """Verify that a terminal audit, clean body, and event rows agree."""

        article_hash = str(audit.get("article_content_hash") or "")
        if not article_hash:
            return False
        try:
            expected_events = int(audit["final_event_count"])
        except (KeyError, TypeError, ValueError):
            return False
        if expected_events < 0:
            return False
        clean = self.connection.execute(
            """
            SELECT content_hash FROM aggregate_clean_articles
            WHERE source_id = ? AND source_article_id = ?
            """,
            (source_id, source_article_id),
        ).fetchone()
        if clean is None or str(clean["content_hash"]) != article_hash:
            return False
        events = self.connection.execute(
            """
            SELECT COUNT(*) AS total FROM aggregate_semantic_events
            WHERE source_id = ? AND source_article_id = ?
              AND content_hash = ? AND prompt_version = ?
            """,
            (
                source_id,
                source_article_id,
                article_hash,
                str(audit.get("prompt_version") or ""),
            ),
        ).fetchone()
        return int(events["total"] if events else 0) == expected_events

    def store_article(self, article: CleanArticle) -> None:
        safe_index = replace(
            article.index,
            canonical_url=sanitize_url(article.index.canonical_url, limit=4000),
            listing_page=sanitize_url(article.index.listing_page, limit=4000),
            structured_data=sanitize_tree(
                article.index.structured_data,
                redact_pii=True,
            ),
        )
        safe_article = replace(
            article,
            index=safe_index,
            structured_data=sanitize_tree(article.structured_data, redact_pii=True),
            evidence_locators=sanitize_tree(
                article.evidence_locators,
                redact_pii=True,
            ),
            failure_reason=redact_diagnostic(article.failure_reason),
        )
        self.connection.execute(
            """
            INSERT INTO aggregate_clean_articles (
                source_id, source_article_id, content_hash, fetched_at, article_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_id, source_article_id) DO UPDATE SET
                content_hash = excluded.content_hash,
                fetched_at = excluded.fetched_at,
                article_json = excluded.article_json
            """,
            (
                safe_article.index.source_id,
                safe_article.index.source_article_id,
                safe_article.content_hash,
                _now(),
                json.dumps(
                    sanitize_tree(safe_article.to_dict(), redact_pii=True),
                    ensure_ascii=False,
                ),
            ),
        )
        self.connection.commit()

    def store_events(
        self,
        source_id: str,
        source_article_id: str,
        events: list[SemanticEvent],
        *,
        _commit: bool = True,
    ) -> None:
        self.connection.execute(
            """
            DELETE FROM aggregate_semantic_events
            WHERE source_id = ? AND source_article_id = ?
            """,
            (source_id, source_article_id),
        )
        self.connection.execute(
            """
            DELETE FROM aggregate_company_aliases
            WHERE source_id = ? AND source_article_id = ?
            """,
            (source_id, source_article_id),
        )
        for event in events:
            safe_event = replace(
                event,
                canonical_url=sanitize_url(event.canonical_url, limit=4000),
                evidence_quotes=tuple(
                    sanitize_tree(item, redact_pii=True)
                    for item in event.evidence_quotes
                ),
                ambiguities=tuple(
                    redact_diagnostic(item) for item in event.ambiguities
                ),
            )
            event_key = "|".join(
                (
                    safe_event.canonical_company,
                    safe_event.event_type,
                    safe_event.event_date,
                    safe_event.funding_round,
                    safe_event.event_status,
                )
            )
            self.connection.execute(
                """
                INSERT INTO aggregate_semantic_events (
                    source_id, source_article_id, event_key, processor,
                    prompt_version, content_hash, processed_at, event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, source_article_id, event_key) DO UPDATE SET
                    processor = excluded.processor,
                    prompt_version = excluded.prompt_version,
                    content_hash = excluded.content_hash,
                    processed_at = excluded.processed_at,
                    event_json = excluded.event_json
                """,
                (
                    safe_event.source_id,
                    safe_event.source_article_id,
                    event_key,
                    safe_event.processor,
                    safe_event.prompt_version,
                    safe_event.content_hash,
                    _now(),
                    json.dumps(
                        sanitize_tree(safe_event.to_dict(), redact_pii=True),
                        ensure_ascii=False,
                    ),
                ),
            )
            canonical_key = normalize_company_alias(safe_event.canonical_company)
            if canonical_key:
                aliases = tuple(
                    dict.fromkeys(
                        (
                            safe_event.canonical_company,
                            *safe_event.company_mentions,
                        )
                    )
                )
                for alias in aliases:
                    alias_key = normalize_company_alias(alias)
                    if not alias_key:
                        continue
                    self.connection.execute(
                        """
                        INSERT OR REPLACE INTO aggregate_company_aliases (
                            source_id, source_article_id, alias_key, alias,
                            canonical_key, canonical_company, evidence_quote,
                            recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source_id,
                            source_article_id,
                            alias_key,
                            alias,
                            canonical_key,
                            safe_event.canonical_company,
                            safe_event.evidence_quotes[0]
                            if safe_event.evidence_quotes
                            else "",
                            _now(),
                        ),
                    )
        if _commit:
            self.connection.commit()

    def canonical_alias_map(self) -> dict[str, str]:
        rows = self.connection.execute(
            """
            SELECT alias_key, alias, canonical_key, canonical_company
            FROM aggregate_company_aliases
            """
        ).fetchall()
        graph: dict[str, set[str]] = {}
        names: dict[str, dict[str, int]] = {}
        canonical_votes: dict[str, dict[str, int]] = {}
        for row in rows:
            alias_key = str(row["alias_key"])
            canonical_key = str(row["canonical_key"])
            if not alias_key or not canonical_key:
                continue
            graph.setdefault(alias_key, set()).add(canonical_key)
            graph.setdefault(canonical_key, set()).add(alias_key)
            alias = str(row["alias"])
            canonical = str(row["canonical_company"])
            names.setdefault(alias_key, {})[alias] = (
                names.setdefault(alias_key, {}).get(alias, 0) + 1
            )
            names.setdefault(canonical_key, {})[canonical] = (
                names.setdefault(canonical_key, {}).get(canonical, 0) + 1
            )
            canonical_votes.setdefault(canonical_key, {})[canonical] = (
                canonical_votes.setdefault(canonical_key, {}).get(canonical, 0) + 1
            )

        output: dict[str, str] = {}
        visited: set[str] = set()
        for start in graph:
            if start in visited:
                continue
            component: set[str] = set()
            pending = [start]
            while pending:
                current = pending.pop()
                if current in component:
                    continue
                component.add(current)
                visited.add(current)
                pending.extend(graph.get(current, ()))
            candidate_names = [
                name
                for key in component
                for name in (canonical_votes.get(key) or names.get(key) or {})
            ]
            if not candidate_names:
                continue
            chosen = max(
                candidate_names,
                key=lambda name: (
                    int(_looks_legal_company(name)),
                    sum(votes.get(name, 0) for votes in canonical_votes.values()),
                    len(name),
                    name,
                ),
            )
            for key in component:
                output[key] = chosen
        return output

    def events_for_source(self, source_id: str) -> list[SemanticEvent]:
        rows = self.connection.execute(
            """
            SELECT e.event_json, i.canonical_url
            FROM aggregate_semantic_events AS e
            LEFT JOIN aggregate_article_index AS i
              ON i.source_id = e.source_id
             AND i.source_article_id = e.source_article_id
            WHERE e.source_id = ?
            ORDER BY e.processed_at DESC, e.source_article_id DESC
            """,
            (source_id,),
        ).fetchall()
        return [
            self._event_from_json(
                str(row["event_json"]),
                canonical_url=str(row["canonical_url"] or ""),
            )
            for row in rows
        ]

    def events_for_article(
        self,
        source_id: str,
        source_article_id: str,
        *,
        content_hash: str = "",
    ) -> list[SemanticEvent]:
        query = """
            SELECT e.event_json, i.canonical_url
            FROM aggregate_semantic_events AS e
            LEFT JOIN aggregate_article_index AS i
              ON i.source_id = e.source_id
             AND i.source_article_id = e.source_article_id
            WHERE e.source_id = ? AND e.source_article_id = ?
        """
        parameters: list[Any] = [source_id, source_article_id]
        if content_hash:
            query += " AND e.content_hash = ?"
            parameters.append(content_hash)
        rows = self.connection.execute(query, parameters).fetchall()
        return [
            self._event_from_json(
                str(row["event_json"]),
                canonical_url=str(row["canonical_url"] or ""),
            )
            for row in rows
        ]

    @staticmethod
    def _event_from_json(
        payload: str,
        *,
        canonical_url: str = "",
    ) -> SemanticEvent:
        raw = json.loads(payload)
        if canonical_url:
            # The relational index column is the canonical URL authority.
            # This also repairs reads of legacy event JSON whose numeric path
            # was mistaken for a phone number by an older sanitizer.
            raw["canonical_url"] = canonical_url
        for field in (
            "company_mentions",
            "industry_tags",
            "investors",
            "evidence_quotes",
            "ambiguities",
            "claim_ids",
            "span_ids",
        ):
            raw[field] = tuple(raw.get(field) or ())
        return SemanticEvent(**raw)

    def has_open_dead_letter(
        self,
        *,
        source_id: str,
        source_article_id: str,
    ) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM aggregate_dead_letters
            WHERE source_id = ? AND source_article_id = ? AND resolved_at = ''
            LIMIT 1
            """,
            (source_id, source_article_id),
        ).fetchone()
        return row is not None

    def open_dead_letter_indexes(
        self,
        source_id: str,
        *,
        limit: int = 100,
    ) -> list[SourceArticleIndex]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT i.index_json, i.canonical_url
            FROM aggregate_dead_letters AS d
            JOIN aggregate_article_index AS i
              ON i.source_id = d.source_id
             AND i.source_article_id = d.source_article_id
            WHERE d.source_id = ? AND d.resolved_at = ''
              AND d.source_article_id != ''
            ORDER BY d.last_failed_at ASC
            LIMIT ?
            """,
            (source_id, max(1, int(limit))),
        ).fetchall()
        output: list[SourceArticleIndex] = []
        for row in rows:
            try:
                raw = json.loads(str(row["index_json"]))
                # The relational column is the canonical identity authority.
                # Legacy JSON blobs could have numeric URL path segments
                # mistaken for phone numbers by an old PII sanitizer.
                raw["canonical_url"] = str(row["canonical_url"])
                output.append(SourceArticleIndex(**raw))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return output

    def article_content_hash(
        self,
        source_id: str,
        source_article_id: str,
    ) -> str:
        row = self.connection.execute(
            """
            SELECT content_hash FROM aggregate_clean_articles
            WHERE source_id = ? AND source_article_id = ?
            """,
            (source_id, source_article_id),
        ).fetchone()
        return str(row["content_hash"]) if row else ""

    def record_dead_letter(
        self,
        *,
        source_id: str,
        source_article_id: str,
        canonical_url: str,
        stage: str,
        error: str,
    ) -> None:
        now = _now()
        safe_error = redact_diagnostic(error, limit=2000) or (
            "operation failed without a diagnostic"
        )
        safe_url = sanitize_url(canonical_url, limit=2000)
        existing = self.connection.execute(
            """
            SELECT id, retry_count
            FROM aggregate_dead_letters
            WHERE source_id = ? AND source_article_id = ? AND stage = ?
              AND resolved_at = ''
            ORDER BY id DESC LIMIT 1
            """,
            (source_id, source_article_id, stage),
        ).fetchone()
        if existing:
            self.connection.execute(
                """
                UPDATE aggregate_dead_letters
                SET retry_count = ?, last_failed_at = ?, error = ?
                WHERE id = ?
                """,
                (
                    int(existing["retry_count"]) + 1,
                    now,
                    safe_error,
                    int(existing["id"]),
                ),
            )
        else:
            self.connection.execute(
                """
                INSERT INTO aggregate_dead_letters (
                    source_id, source_article_id, canonical_url, stage, error,
                    retry_count, first_failed_at, last_failed_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    source_id,
                    source_article_id,
                    safe_url,
                    stage,
                    safe_error,
                    now,
                    now,
                ),
            )
        self.connection.commit()

    def resolve_dead_letter(
        self,
        *,
        source_id: str,
        source_article_id: str,
        stage: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE aggregate_dead_letters
            SET resolved_at = ?
            WHERE source_id = ? AND source_article_id = ? AND stage = ?
              AND resolved_at = ''
            """,
            (_now(), source_id, source_article_id, stage),
        )
        self.connection.commit()

    def sync_semantic_claim_dead_letters(
        self,
        *,
        source_id: str,
        source_article_id: str,
        canonical_url: str,
        failed_claim_ids: list[str],
        error: str,
    ) -> None:
        """Keep one recoverable dead-letter record per unresolved semantic claim."""

        claim_ids = {
            str(claim_id).strip()
            for claim_id in failed_claim_ids
            if str(claim_id).strip()
        }
        current_stages = {f"semantic_claim:{claim_id}" for claim_id in claim_ids}
        rows = self.connection.execute(
            """
            SELECT DISTINCT stage
            FROM aggregate_dead_letters
            WHERE source_id = ? AND source_article_id = ?
              AND stage LIKE 'semantic_claim:%' AND resolved_at = ''
            """,
            (source_id, source_article_id),
        ).fetchall()
        now = _now()
        for row in rows:
            stage = str(row["stage"])
            if stage not in current_stages:
                self.connection.execute(
                    """
                    UPDATE aggregate_dead_letters
                    SET resolved_at = ?
                    WHERE source_id = ? AND source_article_id = ? AND stage = ?
                      AND resolved_at = ''
                    """,
                    (now, source_id, source_article_id, stage),
                )
        self.connection.commit()
        for claim_id in sorted(claim_ids):
            self.record_dead_letter(
                source_id=source_id,
                source_article_id=source_article_id,
                canonical_url=canonical_url,
                stage=f"semantic_claim:{claim_id}",
                error=f"{claim_id}: {error}"[:2000],
            )

    def store_semantic_audit(
        self,
        audit: dict[str, Any],
        *,
        _commit: bool = True,
    ) -> None:
        if not audit:
            return
        safe_audit = sanitize_semantic_audit(audit)
        self.connection.execute(
            """
            INSERT INTO aggregate_semantic_attempts (
                source_id, source_article_id, prompt_version, attempted_at,
                status, validation_error, first_response, repair_response,
                audit_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, source_article_id, prompt_version) DO UPDATE SET
                attempted_at = excluded.attempted_at,
                status = excluded.status,
                validation_error = excluded.validation_error,
                first_response = excluded.first_response,
                repair_response = excluded.repair_response,
                audit_json = excluded.audit_json
            """,
            (
                str(safe_audit.get("source_id") or ""),
                str(safe_audit.get("source_article_id") or ""),
                str(safe_audit.get("prompt_version") or ""),
                _now(),
                str(safe_audit.get("status") or ""),
                redact_diagnostic(safe_audit.get("error"), limit=4000),
                "",
                "",
                json.dumps(safe_audit, ensure_ascii=False, sort_keys=True),
            ),
        )
        if _commit:
            self.connection.commit()

    def store_semantic_result(
        self,
        *,
        source_id: str,
        source_article_id: str,
        audit: dict[str, Any],
        events: list[SemanticEvent],
    ) -> None:
        """Atomically replace one article's semantic audit and events."""

        if str(audit.get("source_id") or "") != source_id or str(
            audit.get("source_article_id") or ""
        ) != source_article_id:
            raise ValueError("semantic audit identity does not match article")
        try:
            expected_events = int(audit["final_event_count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("semantic audit has no valid final_event_count") from exc
        if expected_events != len(events):
            raise ValueError("semantic audit event count does not match events")
        article_hash = str(audit.get("article_content_hash") or "")
        if not article_hash:
            raise ValueError("semantic audit has no article_content_hash")
        if any(
            event.source_id != source_id
            or event.source_article_id != source_article_id
            or event.content_hash != article_hash
            for event in events
        ):
            raise ValueError("semantic event identity/body hash does not match audit")

        self.connection.execute("SAVEPOINT aggregate_semantic_result")
        try:
            self.store_semantic_audit(audit, _commit=False)
            self.store_events(
                source_id,
                source_article_id,
                events,
                _commit=False,
            )
            self.connection.execute("RELEASE aggregate_semantic_result")
        except BaseException:
            self.connection.execute("ROLLBACK TO aggregate_semantic_result")
            self.connection.execute("RELEASE aggregate_semantic_result")
            raise

    def update_cursor(
        self,
        *,
        source_id: str,
        cursor_value: str,
        listing_hash: str,
        listing_count: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO aggregate_source_cursor (
                source_id, cursor_value, last_success_at,
                last_listing_hash, last_listing_count
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                cursor_value = excluded.cursor_value,
                last_success_at = excluded.last_success_at,
                last_listing_hash = excluded.last_listing_hash,
                last_listing_count = excluded.last_listing_count
            """,
            (source_id, cursor_value, _now(), listing_hash, listing_count),
        )
        self.connection.commit()

    def record_run(self, run: AdapterRun) -> None:
        payload = sanitize_tree(asdict(run), redact_pii=True)
        payload["error"] = redact_diagnostic(payload.get("error"), limit=2000)
        self.connection.execute(
            """
            INSERT INTO aggregate_runs (
                adapter_id, source_id, started_at, finished_at, status, run_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run.adapter_id,
                run.source_id,
                run.started_at,
                run.finished_at,
                run.status,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def health(self) -> dict[str, Any]:
        runs = self.connection.execute(
            """
            SELECT r.run_json
            FROM aggregate_runs r
            JOIN (
                SELECT source_id, MAX(id) AS max_id
                FROM aggregate_runs GROUP BY source_id
            ) latest ON latest.max_id = r.id
            ORDER BY r.source_id
            """
        ).fetchall()
        latest = [json.loads(str(row["run_json"])) for row in runs]
        open_dead_letters = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM aggregate_dead_letters WHERE resolved_at = ''"
            ).fetchone()[0]
        )
        return {
            "source_count": len(latest),
            "healthy_count": sum(item["status"] == "ok" for item in latest),
            "failed_count": sum(item["status"] != "ok" for item in latest),
            "open_dead_letter_count": open_dead_letters,
            "sources": {item["source_id"]: item for item in latest},
        }
