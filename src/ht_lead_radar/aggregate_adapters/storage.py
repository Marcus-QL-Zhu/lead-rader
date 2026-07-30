"""SQLite persistence for incremental aggregate-source collection."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
import unicodedata

from .models import AdapterRun, CleanArticle, SemanticEvent, SourceArticleIndex


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
            """
        )
        self.connection.commit()

    def upsert_index(self, index: SourceArticleIndex) -> bool:
        row = self.connection.execute(
            """
            SELECT content_hash FROM aggregate_article_index
            WHERE source_id = ? AND source_article_id = ?
            """,
            (index.source_id, index.source_article_id),
        ).fetchone()
        changed = row is None or str(row["content_hash"]) != index.content_hash
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
                index.source_id,
                index.source_article_id,
                index.canonical_url,
                index.published_at,
                index.discovered_at,
                _now(),
                index.content_hash,
                json.dumps(index.to_dict(), ensure_ascii=False),
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
        return (
            isinstance(audit, dict)
            and audit.get("status") != "fallback_to_rules"
            and audit.get("index_content_hash") == index.content_hash
            and audit.get("model_identity") == model_identity
        )

    def store_article(self, article: CleanArticle) -> None:
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
                article.index.source_id,
                article.index.source_article_id,
                article.content_hash,
                _now(),
                json.dumps(article.to_dict(), ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def store_events(
        self,
        source_id: str,
        source_article_id: str,
        events: list[SemanticEvent],
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
            event_key = "|".join(
                (
                    event.canonical_company,
                    event.event_type,
                    event.event_date,
                    event.funding_round,
                    event.event_status,
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
                    event.source_id,
                    event.source_article_id,
                    event_key,
                    event.processor,
                    event.prompt_version,
                    event.content_hash,
                    _now(),
                    json.dumps(event.to_dict(), ensure_ascii=False),
                ),
            )
            canonical_key = normalize_company_alias(event.canonical_company)
            if canonical_key:
                aliases = tuple(
                    dict.fromkeys(
                        (
                            event.canonical_company,
                            *event.company_mentions,
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
                            event.canonical_company,
                            event.evidence_quotes[0]
                            if event.evidence_quotes
                            else "",
                            _now(),
                        ),
                    )
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
                canonical_votes.setdefault(canonical_key, {}).get(canonical, 0)
                + 1
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
                for name in (
                    canonical_votes.get(key) or names.get(key) or {}
                )
            ]
            if not candidate_names:
                continue
            chosen = max(
                candidate_names,
                key=lambda name: (
                    int(_looks_legal_company(name)),
                    sum(
                        votes.get(name, 0)
                        for votes in canonical_votes.values()
                    ),
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
            SELECT event_json FROM aggregate_semantic_events
            WHERE source_id = ?
            ORDER BY processed_at DESC, source_article_id DESC
            """,
            (source_id,),
        ).fetchall()
        return [self._event_from_json(str(row["event_json"])) for row in rows]

    def events_for_article(
        self,
        source_id: str,
        source_article_id: str,
        *,
        content_hash: str = "",
    ) -> list[SemanticEvent]:
        query = """
            SELECT event_json FROM aggregate_semantic_events
            WHERE source_id = ? AND source_article_id = ?
        """
        parameters: list[Any] = [source_id, source_article_id]
        if content_hash:
            query += " AND content_hash = ?"
            parameters.append(content_hash)
        rows = self.connection.execute(query, parameters).fetchall()
        return [self._event_from_json(str(row["event_json"])) for row in rows]

    @staticmethod
    def _event_from_json(payload: str) -> SemanticEvent:
        raw = json.loads(payload)
        for field in (
            "company_mentions",
            "industry_tags",
            "investors",
            "evidence_quotes",
            "ambiguities",
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
            SELECT DISTINCT i.index_json
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
                    error[:2000],
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
                    canonical_url,
                    stage,
                    error[:2000],
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
    def store_semantic_audit(self, audit: dict[str, Any]) -> None:
        if not audit:
            return
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
                str(audit.get("source_id") or ""),
                str(audit.get("source_article_id") or ""),
                str(audit.get("prompt_version") or ""),
                _now(),
                str(audit.get("status") or ""),
                str(audit.get("error") or "")[:4000],
                str(audit.get("first_response") or ""),
                str(audit.get("repair_response") or ""),
                json.dumps(audit, ensure_ascii=False),
            ),
        )
        self.connection.commit()

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
                json.dumps(asdict(run), ensure_ascii=False),
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
