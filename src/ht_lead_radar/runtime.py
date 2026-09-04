"""Resumable, checkpointed execution for the lead-radar pipeline.

Stages are intentionally data-agnostic.  Each handler receives a
``StageContext`` and returns JSON-serialisable output for the next stage:

    collect -> normalize -> eventize -> score -> basic_research -> publish

The SQLite store keeps immutable checkpoint attempts.  A rerun with the same
idempotency key resumes a failed run, while a completed run returns immediately.
Replay can recompute cheap stages and reuse completed costly-stage checkpoints.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .sanitization import safe_error, sanitize_tree


_PERSISTENCE_SANITIZER_VERSION = "4"


def _sanitize_runtime_value(value: Any) -> Any:
    """Sanitize every runtime persistence boundary except approved job JSON.

    ``sanitize_tree`` deliberately preserves ``public_payload``,
    ``liepin_payload`` and their hashes byte-for-byte.  All other runtime
    input/output is operational state, so PII and signed credentials are not
    useful enough to justify persisting them.
    """

    return sanitize_tree(value, redact_pii=True)


def _opaque_idempotency_key(run_id: str) -> str:
    """Return a stable non-secret DB representation of an idempotency key."""

    return f"run-ref:{run_id.removeprefix('run_')}"


def _safe_error_text(error: object) -> str:
    diagnostic = safe_error(error)
    detail = diagnostic["detail"]
    if detail == diagnostic["error_class"] or detail.startswith(
        f"{diagnostic['error_class']}:"
    ):
        return detail
    return (
        f"{diagnostic['error_class']}: {detail}"
        if detail
        else diagnostic["error_class"]
    )


STAGES = (
    'collect',
    'normalize',
    'eventize',
    'score',
    'basic_research',
    'publish',
)
DEFAULT_COSTLY_STAGES = frozenset({'collect', 'basic_research'})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise TypeError(f'pipeline values must be JSON serialisable: {error}') from error


def _hash_value(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()


def make_run_id(idempotency_key: str) -> str:
    key = idempotency_key.strip()
    if not key:
        raise ValueError('idempotency_key is required')
    return 'run_' + hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]


class IdempotencyConflict(RuntimeError):
    """The same idempotency key was used with different input."""


class UnknownRun(KeyError):
    """The requested run does not exist."""


class StageExecutionError(RuntimeError):
    def __init__(self, run_id: str, stage: str, cause: Exception):
        self.run_id = run_id
        self.stage = stage
        self.cause = cause
        super().__init__(
            f'run {run_id} failed at {stage}: {_safe_error_text(cause)}'
        )


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    stage: str
    attempt: int
    status: str
    input_hash: str
    output: Any
    output_hash: str | None
    costly: bool
    replay: bool
    started_at: str
    completed_at: str | None
    error: str | None


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    idempotency_key: str
    input: Any
    input_hash: str
    status: str
    current_stage: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            'run_id': self.run_id,
            'idempotency_key': self.idempotency_key,
            'input': self.input,
            'input_hash': self.input_hash,
            'status': self.status,
            'current_stage': self.current_stage,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'completed_at': self.completed_at,
            'error': self.error,
        }


@dataclass(frozen=True)
class RuntimeResult:
    run_id: str
    status: str
    output: Any
    checkpoints: Mapping[str, Checkpoint]
    reused_stages: tuple[str, ...] = ()
    executed_stages: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            'run_id': self.run_id,
            'status': self.status,
            'output': self.output,
            'reused_stages': list(self.reused_stages),
            'executed_stages': list(self.executed_stages),
            'checkpoints': {
                name: {
                    'attempt': checkpoint.attempt,
                    'status': checkpoint.status,
                    'output_hash': checkpoint.output_hash,
                    'costly': checkpoint.costly,
                    'replay': checkpoint.replay,
                }
                for name, checkpoint in self.checkpoints.items()
            },
        }


StageHandler = Callable[['StageContext'], Any]


@dataclass
class StageContext:
    run_id: str
    stage: str
    value: Any
    original_input: Any
    outputs: Mapping[str, Any]
    replay: bool
    store: 'RunStore'

    def effect_token(self, effect_key: str) -> str:
        """Stable token for providers that support their own idempotency key."""

        if not effect_key.strip():
            raise ValueError('effect_key is required')
        raw = f'{self.run_id}:{self.stage}:{effect_key}'
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def effect_once(
        self, effect_key: str, operation: Callable[[str], Any]
    ) -> Any:
        """Cache a costly call inside a stage.

        ``operation`` receives a stable token which can be forwarded to a
        provider supporting idempotency. Completed effects are never consumed
        again on resume/replay.
        """

        cached = self.store.get_effect(self.run_id, self.stage, effect_key)
        if cached is not None and cached['status'] == 'completed':
            return cached['result']
        token = self.effect_token(effect_key)
        self.store.start_effect(self.run_id, self.stage, effect_key, token)
        try:
            result = _sanitize_runtime_value(operation(token))
            _canonical_json(result)
        except Exception as error:
            self.store.fail_effect(self.run_id, self.stage, effect_key, error)
            raise
        self.store.complete_effect(self.run_id, self.stage, effect_key, result)
        return result


class RunStore:
    """SQLite persistence for pipeline runs, checkpoint attempts, and effects."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.executescript(
                '''
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    input_json TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_stage TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS pipeline_checkpoints (
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    output_json TEXT,
                    output_hash TEXT,
                    costly INTEGER NOT NULL,
                    replay INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    error TEXT,
                    PRIMARY KEY (run_id, stage, attempt),
                    FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id)
                );

                CREATE INDEX IF NOT EXISTS checkpoint_latest
                    ON pipeline_checkpoints(run_id, stage, attempt DESC);

                CREATE TABLE IF NOT EXISTS pipeline_effects (
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    effect_key TEXT NOT NULL,
                    idempotency_token TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, stage, effect_key),
                    FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS pipeline_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                '''
            )
            self._migrate_persisted_payloads(connection)

    @staticmethod
    def _safe_json_blob(value: object) -> tuple[str, str]:
        """Return canonical safe JSON plus its digest for a legacy blob."""

        try:
            parsed = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = str(value or "")
        rendered = _canonical_json(_sanitize_runtime_value(parsed))
        return rendered, hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    def _migrate_persisted_payloads(self, connection: sqlite3.Connection) -> None:
        """One-time cleanup of credentials/PII left by earlier runtimes."""

        row = connection.execute(
            "SELECT value FROM pipeline_metadata "
            "WHERE key='persistence_sanitizer_version'"
        ).fetchone()
        if row and str(row["value"]) == _PERSISTENCE_SANITIZER_VERSION:
            return

        for item in connection.execute(
            "SELECT run_id, input_json, error FROM pipeline_runs"
        ).fetchall():
            safe_json, digest = self._safe_json_blob(item["input_json"])
            connection.execute(
                "UPDATE pipeline_runs SET idempotency_key=?, input_json=?, "
                "input_hash=?, error=? "
                "WHERE run_id=?",
                (
                    _opaque_idempotency_key(str(item["run_id"])),
                    safe_json,
                    digest,
                    _safe_error_text(item["error"]) if item["error"] else None,
                    item["run_id"],
                ),
            )
        for item in connection.execute(
            "SELECT rowid, output_json, error FROM pipeline_checkpoints"
        ).fetchall():
            safe_json: str | None = None
            digest: str | None = None
            if item["output_json"] is not None:
                safe_json, digest = self._safe_json_blob(item["output_json"])
            connection.execute(
                "UPDATE pipeline_checkpoints SET output_json=?, output_hash=?, error=? "
                "WHERE rowid=?",
                (
                    safe_json,
                    digest,
                    _safe_error_text(item["error"]) if item["error"] else None,
                    int(item["rowid"]),
                ),
            )
        for item in connection.execute(
            "SELECT rowid, result_json, error FROM pipeline_effects"
        ).fetchall():
            safe_json = None
            if item["result_json"] is not None:
                safe_json, _ = self._safe_json_blob(item["result_json"])
            connection.execute(
                "UPDATE pipeline_effects SET result_json=?, error=? WHERE rowid=?",
                (
                    safe_json,
                    _safe_error_text(item["error"]) if item["error"] else None,
                    int(item["rowid"]),
                ),
            )
        connection.execute(
            """
            INSERT INTO pipeline_metadata(key, value)
            VALUES ('persistence_sanitizer_version', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (_PERSISTENCE_SANITIZER_VERSION,),
        )

    def ensure_run(self, idempotency_key: str, payload: Any) -> RunRecord:
        run_id = make_run_id(idempotency_key)
        payload = _sanitize_runtime_value(payload)
        input_json = _canonical_json(payload)
        input_hash = hashlib.sha256(input_json.encode('utf-8')).hexdigest()
        now = _iso(self.clock())
        with self._lock, self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT * FROM pipeline_runs WHERE run_id = ?', (run_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    '''
                    INSERT INTO pipeline_runs (
                        run_id, idempotency_key, input_json, input_hash, status,
                        current_stage, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', NULL, ?, ?)
                    ''',
                    (
                        run_id,
                        _opaque_idempotency_key(run_id),
                        input_json,
                        input_hash,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    'SELECT * FROM pipeline_runs WHERE run_id = ?', (run_id,)
                ).fetchone()
            elif row['input_hash'] != input_hash:
                raise IdempotencyConflict(
                    'idempotency key already belongs to a different input hash'
                )
        return _run_from_row(row)

    def get_run(self, run_id: str) -> RunRecord:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM pipeline_runs WHERE run_id = ?', (run_id,)
            ).fetchone()
        if row is None:
            raise UnknownRun(run_id)
        return _run_from_row(row)

    def list_runs(self, limit: int = 100) -> list[RunRecord]:
        if limit <= 0:
            raise ValueError('limit must be positive')
        with self._connect() as connection:
            rows = connection.execute(
                '''
                SELECT * FROM pipeline_runs
                ORDER BY created_at DESC, run_id DESC LIMIT ?
                ''',
                (limit,),
            ).fetchall()
        return [_run_from_row(row) for row in rows]

    def set_run_state(
        self,
        run_id: str,
        status: str,
        *,
        current_stage: str | None = None,
        error: object | None = None,
    ) -> None:
        now = _iso(self.clock())
        completed = now if status in {'completed', 'failed'} else None
        with self._connect() as connection:
            result = connection.execute(
                '''
                UPDATE pipeline_runs
                SET status = ?, current_stage = ?, updated_at = ?,
                    completed_at = ?, error = ?
                WHERE run_id = ?
                ''',
                (
                    status,
                    current_stage,
                    now,
                    completed,
                    _safe_error_text(error) if error else None,
                    run_id,
                ),
            )
        if result.rowcount == 0:
            raise UnknownRun(run_id)

    def finalize_interrupted_run(self, run_id: str, error: object) -> bool:
        """Close in-flight runtime rows after an out-of-process watchdog kill.

        The daily launcher owns the wall-clock watchdog, so the killed Python
        process cannot reliably update SQLite itself.  This operation is
        deliberately conditional and idempotent: completed or already-failed
        runs are never rewritten.
        """

        now = _iso(self.clock())
        safe_error = _safe_error_text(error)
        with self._lock, self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT status FROM pipeline_runs WHERE run_id = ?',
                (run_id,),
            ).fetchone()
            if row is None:
                raise UnknownRun(run_id)
            if str(row['status']) not in {'pending', 'running'}:
                return False
            connection.execute(
                '''
                UPDATE pipeline_checkpoints
                SET status = 'failed', completed_at = ?, error = ?
                WHERE run_id = ? AND status = 'running'
                ''',
                (now, safe_error, run_id),
            )
            connection.execute(
                '''
                UPDATE pipeline_effects
                SET status = 'failed', updated_at = ?, error = ?
                WHERE run_id = ? AND status = 'running'
                ''',
                (now, safe_error, run_id),
            )
            connection.execute(
                '''
                UPDATE pipeline_runs
                SET status = 'failed', updated_at = ?, completed_at = ?, error = ?
                WHERE run_id = ? AND status IN ('pending', 'running')
                ''',
                (now, now, safe_error, run_id),
            )
        return True

    def latest_checkpoint(
        self, run_id: str, stage: str
    ) -> Checkpoint | None:
        with self._connect() as connection:
            row = connection.execute(
                '''
                SELECT * FROM pipeline_checkpoints
                WHERE run_id = ? AND stage = ?
                ORDER BY attempt DESC LIMIT 1
                ''',
                (run_id, stage),
            ).fetchone()
        return _checkpoint_from_row(row) if row else None

    def latest_checkpoints(self, run_id: str) -> dict[str, Checkpoint]:
        with self._connect() as connection:
            rows = connection.execute(
                '''
                SELECT checkpoint.*
                FROM pipeline_checkpoints AS checkpoint
                JOIN (
                    SELECT stage, MAX(attempt) AS attempt
                    FROM pipeline_checkpoints
                    WHERE run_id = ?
                    GROUP BY stage
                ) AS latest
                  ON latest.stage = checkpoint.stage
                 AND latest.attempt = checkpoint.attempt
                WHERE checkpoint.run_id = ?
                ''',
                (run_id, run_id),
            ).fetchall()
        return {row['stage']: _checkpoint_from_row(row) for row in rows}

    def checkpoint_history(
        self, run_id: str, stage: str | None = None
    ) -> list[Checkpoint]:
        query = (
            'SELECT * FROM pipeline_checkpoints WHERE run_id = ?'
            + (' AND stage = ?' if stage else '')
            + ' ORDER BY started_at, stage, attempt'
        )
        parameters: tuple[Any, ...] = (run_id, stage) if stage else (run_id,)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_checkpoint_from_row(row) for row in rows]

    def start_checkpoint(
        self,
        run_id: str,
        stage: str,
        input_value: Any,
        *,
        costly: bool,
        replay: bool,
    ) -> int:
        input_hash = _hash_value(input_value)
        now = _iso(self.clock())
        with self._lock, self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                '''
                SELECT COALESCE(MAX(attempt), 0) AS attempt
                FROM pipeline_checkpoints WHERE run_id = ? AND stage = ?
                ''',
                (run_id, stage),
            ).fetchone()
            attempt = int(row['attempt']) + 1
            connection.execute(
                '''
                INSERT INTO pipeline_checkpoints (
                    run_id, stage, attempt, status, input_hash, costly,
                    replay, started_at
                ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?)
                ''',
                (
                    run_id,
                    stage,
                    attempt,
                    input_hash,
                    int(costly),
                    int(replay),
                    now,
                ),
            )
        return attempt

    def complete_checkpoint(
        self,
        run_id: str,
        stage: str,
        attempt: int,
        output: Any,
    ) -> Checkpoint:
        output = _sanitize_runtime_value(output)
        output_json = _canonical_json(output)
        output_hash = hashlib.sha256(output_json.encode('utf-8')).hexdigest()
        now = _iso(self.clock())
        with self._connect() as connection:
            result = connection.execute(
                '''
                UPDATE pipeline_checkpoints
                SET status = 'completed', output_json = ?, output_hash = ?,
                    completed_at = ?, error = NULL
                WHERE run_id = ? AND stage = ? AND attempt = ?
                ''',
                (output_json, output_hash, now, run_id, stage, attempt),
            )
        if result.rowcount == 0:
            raise UnknownRun(f'{run_id}/{stage}/{attempt}')
        checkpoint = self.latest_checkpoint(run_id, stage)
        assert checkpoint is not None
        return checkpoint

    def fail_checkpoint(
        self,
        run_id: str,
        stage: str,
        attempt: int,
        error: object,
    ) -> None:
        now = _iso(self.clock())
        with self._connect() as connection:
            connection.execute(
                '''
                UPDATE pipeline_checkpoints
                SET status = 'failed', completed_at = ?, error = ?
                WHERE run_id = ? AND stage = ? AND attempt = ?
                ''',
                (now, _safe_error_text(error), run_id, stage, attempt),
            )

    def get_effect(
        self, run_id: str, stage: str, effect_key: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                '''
                SELECT * FROM pipeline_effects
                WHERE run_id = ? AND stage = ? AND effect_key = ?
                ''',
                (run_id, stage, effect_key),
            ).fetchone()
        if row is None:
            return None
        return {
            'status': row['status'],
            'idempotency_token': row['idempotency_token'],
            'result': (
                json.loads(row['result_json'])
                if row['result_json'] is not None
                else None
            ),
            'error': row['error'],
        }

    def start_effect(
        self,
        run_id: str,
        stage: str,
        effect_key: str,
        idempotency_token: str,
    ) -> None:
        if not effect_key.strip():
            raise ValueError('effect_key is required')
        now = _iso(self.clock())
        with self._connect() as connection:
            connection.execute(
                '''
                INSERT INTO pipeline_effects (
                    run_id, stage, effect_key, idempotency_token, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?)
                ON CONFLICT(run_id, stage, effect_key) DO UPDATE SET
                    status = 'running',
                    error = NULL,
                    updated_at = excluded.updated_at
                WHERE pipeline_effects.status != 'completed'
                ''',
                (
                    run_id,
                    stage,
                    effect_key,
                    idempotency_token,
                    now,
                    now,
                ),
            )

    def complete_effect(
        self, run_id: str, stage: str, effect_key: str, result: Any
    ) -> None:
        result_json = _canonical_json(_sanitize_runtime_value(result))
        with self._connect() as connection:
            connection.execute(
                '''
                UPDATE pipeline_effects
                SET status = 'completed', result_json = ?, error = NULL,
                    updated_at = ?
                WHERE run_id = ? AND stage = ? AND effect_key = ?
                ''',
                (
                    result_json,
                    _iso(self.clock()),
                    run_id,
                    stage,
                    effect_key,
                ),
            )

    def fail_effect(
        self, run_id: str, stage: str, effect_key: str, error: object
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                '''
                UPDATE pipeline_effects
                SET status = 'failed', error = ?, updated_at = ?
                WHERE run_id = ? AND stage = ? AND effect_key = ?
                ''',
                (
                    _safe_error_text(error),
                    _iso(self.clock()),
                    run_id,
                    stage,
                    effect_key,
                ),
            )

    def status(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        checkpoints = self.latest_checkpoints(run_id)
        return {
            **run.to_dict(),
            'stages': {
                stage: (
                    {
                        'status': checkpoints[stage].status,
                        'attempt': checkpoints[stage].attempt,
                        'costly': checkpoints[stage].costly,
                        'output_hash': checkpoints[stage].output_hash,
                        'error': checkpoints[stage].error,
                    }
                    if stage in checkpoints
                    else {'status': 'pending', 'attempt': 0}
                )
                for stage in STAGES
            },
        }


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row['run_id'],
        idempotency_key=row['idempotency_key'],
        input=json.loads(row['input_json']),
        input_hash=row['input_hash'],
        status=row['status'],
        current_stage=row['current_stage'],
        created_at=row['created_at'],
        updated_at=row['updated_at'],
        completed_at=row['completed_at'],
        error=row['error'],
    )


def _checkpoint_from_row(row: sqlite3.Row) -> Checkpoint:
    return Checkpoint(
        run_id=row['run_id'],
        stage=row['stage'],
        attempt=int(row['attempt']),
        status=row['status'],
        input_hash=row['input_hash'],
        output=(
            json.loads(row['output_json'])
            if row['output_json'] is not None
            else None
        ),
        output_hash=row['output_hash'],
        costly=bool(row['costly']),
        replay=bool(row['replay']),
        started_at=row['started_at'],
        completed_at=row['completed_at'],
        error=row['error'],
    )


class StagedRuntime:
    """Execute, resume, and replay the six-stage pipeline."""

    def __init__(
        self,
        store: RunStore,
        handlers: Mapping[str, StageHandler],
        *,
        costly_stages: Iterable[str] = DEFAULT_COSTLY_STAGES,
    ):
        missing = [stage for stage in STAGES if stage not in handlers]
        extra = [stage for stage in handlers if stage not in STAGES]
        if missing or extra:
            details = []
            if missing:
                details.append(f'missing handlers: {missing}')
            if extra:
                details.append(f'unknown handlers: {extra}')
            raise ValueError('; '.join(details))
        costly = frozenset(costly_stages)
        unknown_costly = costly.difference(STAGES)
        if unknown_costly:
            raise ValueError(f'unknown costly stages: {sorted(unknown_costly)}')
        self.store = store
        self.handlers = dict(handlers)
        self.costly_stages = costly
        self._lock = threading.RLock()

    def run(self, idempotency_key: str, payload: Any) -> RuntimeResult:
        record = self.store.ensure_run(idempotency_key, payload)
        if record.status == 'completed':
            return self._result(record.run_id, reused=STAGES, executed=())
        return self._execute(record.run_id)

    def resume(self, run_id: str) -> RuntimeResult:
        self.store.get_run(run_id)
        return self._execute(run_id)

    def replay(
        self,
        run_id: str,
        *,
        from_stage: str = 'normalize',
        reuse_costly: bool = True,
    ) -> RuntimeResult:
        if from_stage not in STAGES:
            raise ValueError(f'unknown stage: {from_stage}')
        self.store.get_run(run_id)
        return self._execute(
            run_id,
            replay_from=from_stage,
            reuse_costly=reuse_costly,
        )

    def _execute(
        self,
        run_id: str,
        *,
        replay_from: str | None = None,
        reuse_costly: bool = True,
    ) -> RuntimeResult:
        with self._lock:
            record = self.store.get_run(run_id)
            outputs: dict[str, Any] = {}
            value = record.input
            reused: list[str] = []
            executed: list[str] = []
            replay_index = STAGES.index(replay_from) if replay_from else None
            self.store.set_run_state(run_id, 'running')

            for index, stage in enumerate(STAGES):
                checkpoint = self.store.latest_checkpoint(run_id, stage)
                replaying = replay_index is not None and index >= replay_index
                completed = checkpoint is not None and checkpoint.status == 'completed'
                same_input = completed and checkpoint.input_hash == _hash_value(value)
                should_reuse = completed and (
                    not replaying
                    or (
                        reuse_costly
                        and stage in self.costly_stages
                        and same_input
                    )
                )
                if should_reuse:
                    value = checkpoint.output
                    outputs[stage] = value
                    reused.append(stage)
                    continue

                self.store.set_run_state(
                    run_id, 'running', current_stage=stage
                )
                attempt = self.store.start_checkpoint(
                    run_id,
                    stage,
                    value,
                    costly=stage in self.costly_stages,
                    replay=replaying,
                )
                context = StageContext(
                    run_id=run_id,
                    stage=stage,
                    value=value,
                    original_input=record.input,
                    outputs=dict(outputs),
                    replay=replaying,
                    store=self.store,
                )
                try:
                    value = _sanitize_runtime_value(self.handlers[stage](context))
                    _canonical_json(value)
                except Exception as error:
                    self.store.fail_checkpoint(run_id, stage, attempt, error)
                    self.store.set_run_state(
                        run_id,
                        'failed',
                        current_stage=stage,
                        error=error,
                    )
                    # Keep the original exception available only through the
                    # in-memory ``cause`` attribute. An uncaught chained
                    # traceback must not print provider credentials.
                    raise StageExecutionError(run_id, stage, error) from None
                self.store.complete_checkpoint(run_id, stage, attempt, value)
                outputs[stage] = value
                executed.append(stage)

            self.store.set_run_state(run_id, 'completed')
            return self._result(run_id, reused=reused, executed=executed)

    def _result(
        self,
        run_id: str,
        *,
        reused: Iterable[str],
        executed: Iterable[str],
    ) -> RuntimeResult:
        record = self.store.get_run(run_id)
        checkpoints = self.store.latest_checkpoints(run_id)
        output = (
            checkpoints['publish'].output if 'publish' in checkpoints else None
        )
        return RuntimeResult(
            run_id=run_id,
            status=record.status,
            output=output,
            checkpoints=checkpoints,
            reused_stages=tuple(reused),
            executed_stages=tuple(executed),
        )
