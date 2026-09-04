from __future__ import annotations

from collections import Counter
import json
import sqlite3

import pytest

from ht_lead_radar.runtime import (
    STAGES,
    IdempotencyConflict,
    RunStore,
    StageExecutionError,
    StagedRuntime,
    make_run_id,
)


def _handlers(counts=None):
    counts = counts if counts is not None else Counter()

    def handler(stage):
        def run(context):
            counts[stage] += 1
            value = (
                dict(context.value)
                if isinstance(context.value, dict)
                else {'value': context.value}
            )
            value.setdefault('trace', [])
            value['trace'] = [*value['trace'], stage]
            return value

        return run

    return {stage: handler(stage) for stage in STAGES}


def test_run_id_is_stable_and_requires_key():
    assert make_run_id('daily:2026-07-25') == make_run_id('daily:2026-07-25')
    assert make_run_id('daily:2026-07-25') != make_run_id('daily:2026-07-26')
    with pytest.raises(ValueError):
        make_run_id(' ')


def test_complete_run_is_idempotent_and_checkpointed(tmp_path):
    counts = Counter()
    store = RunStore(tmp_path / 'runs.sqlite')
    runtime = StagedRuntime(store, _handlers(counts))
    payload = {'query': '脑机接口'}

    first = runtime.run('query-1', payload)
    second = runtime.run('query-1', payload)

    assert first.status == second.status == 'completed'
    assert first.run_id == second.run_id
    assert first.executed_stages == STAGES
    assert second.executed_stages == ()
    assert second.reused_stages == STAGES
    assert counts == Counter({stage: 1 for stage in STAGES})
    assert first.output['trace'] == list(STAGES)
    assert store.status(first.run_id)['stages']['publish']['status'] == 'completed'


def test_same_idempotency_key_with_different_input_is_rejected(tmp_path):
    runtime = StagedRuntime(RunStore(tmp_path / 'runs.sqlite'), _handlers())
    runtime.run('same-key', {'query': 'A'})

    with pytest.raises(IdempotencyConflict):
        runtime.run('same-key', {'query': 'B'})


def test_resume_after_failure_only_reexecutes_failed_and_later_stages(tmp_path):
    counts = Counter()
    fail_once = {'eventize': True}
    handlers = _handlers(counts)
    normal_eventize = handlers['eventize']

    def flaky(context):
        counts['eventize'] += 1
        if fail_once.pop('eventize', False):
            raise RuntimeError('temporary parse failure')
        value = dict(context.value)
        value['trace'] = [*value['trace'], 'eventize']
        return value

    handlers['eventize'] = flaky
    store = RunStore(tmp_path / 'runs.sqlite')
    runtime = StagedRuntime(store, handlers)

    with pytest.raises(StageExecutionError) as caught:
        runtime.run('resume-me', {'trace': []})

    assert caught.value.stage == 'eventize'
    run_id = caught.value.run_id
    assert store.get_run(run_id).status == 'failed'
    assert store.latest_checkpoint(run_id, 'eventize').status == 'failed'

    result = runtime.resume(run_id)

    assert result.status == 'completed'
    assert result.reused_stages == ('collect', 'normalize')
    assert result.executed_stages == (
        'eventize',
        'score',
        'basic_research',
        'publish',
    )
    assert counts['collect'] == 1
    assert counts['normalize'] == 1
    assert counts['eventize'] == 2
    assert len(store.checkpoint_history(run_id, 'eventize')) == 2
    assert store.checkpoint_history(run_id, 'eventize')[0].error
    del normal_eventize  # documents that the replacement is intentional


def test_runtime_persists_only_bounded_redacted_error_diagnostics(tmp_path):
    secret = "Bearer runtime-secret-7F31A9"
    handlers = _handlers()

    def fail_collect(_context):
        raise RuntimeError(
            f"upstream rejected Authorization: {secret}; token=second-secret"
        )

    handlers["collect"] = fail_collect
    database = tmp_path / "runs.sqlite"
    store = RunStore(database)
    runtime = StagedRuntime(store, handlers)

    with pytest.raises(StageExecutionError) as caught:
        runtime.run("safe-errors", {"trace": []})

    content = database.read_bytes()
    assert "runtime-secret-7F31A9" not in str(caught.value)
    assert "second-secret" not in str(caught.value)
    assert "[redacted]" in str(caught.value)
    assert b"runtime-secret-7F31A9" not in content
    assert b"second-secret" not in content
    run = store.list_runs(1)[0]
    checkpoint = store.latest_checkpoint(run.run_id, "collect")
    assert run.error is not None and "[redacted]" in run.error
    assert checkpoint is not None and "[redacted]" in checkpoint.error


def test_runtime_never_persists_basic_auth_or_cookie_headers(tmp_path):
    database = tmp_path / "runtime-http-secrets.sqlite"
    handlers = _handlers()

    def fail_collect(_context):
        raise RuntimeError(
            "upstream failed\n"
            "Authorization: Basic dXNlcjpwYXNzd29yZA==\n"
            "Cookie: session=top-secret-cookie; csrf=top-secret-csrf\n"
            "Set-Cookie: response=top-secret-response; Path=/"
        )

    handlers["collect"] = fail_collect
    runtime = StagedRuntime(RunStore(database), handlers)
    with pytest.raises(StageExecutionError):
        runtime.run("http-secret-boundary", {"trace": []})

    persisted = database.read_bytes()
    for secret in (
        b"dXNlcjpwYXNzd29yZA==",
        b"top-secret-cookie",
        b"top-secret-csrf",
        b"top-secret-response",
    ):
        assert secret not in persisted


def test_successful_checkpoint_redacts_diagnostic_fields_only(tmp_path):
    handlers = _handlers()

    def collect(_context):
        return {
            "public_payload": {
                "position_scope": "负责 Authorization 与 Cookie 产品商业化"
            },
            "trace": ["Cookie: session=checkpoint-cookie"],
            "provider_error": "Authorization: Basic checkpoint-basic",
        }

    handlers["collect"] = collect
    result = StagedRuntime(RunStore(tmp_path / "successful.sqlite"), handlers).run(
        "successful-diagnostic-boundary",
        {"trace": ["Set-Cookie: input=input-cookie-secret"]},
    )
    collected = result.checkpoints["collect"].output

    assert collected["public_payload"]["position_scope"] == (
        "负责 Authorization 与 Cookie 产品商业化"
    )
    assert "checkpoint-cookie" not in repr(collected)
    assert "checkpoint-basic" not in repr(collected)
    assert b"input-cookie-secret" not in (
        tmp_path / "successful.sqlite"
    ).read_bytes()


def test_runtime_sanitizes_nested_credentials_and_migrates_all_legacy_blobs(tmp_path):
    database = tmp_path / "legacy-runtime.sqlite"
    store = RunStore(database)
    result = StagedRuntime(store, _handlers()).run(
        "credential-boundary",
        {
            "query": "机器人",
            "nested": {"feishu_app_secret": "input-secret"},
            "headers": {"Authorization": "Bearer header-secret"},
        },
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE pipeline_runs SET idempotency_key=?, input_json=?, error=? "
            "WHERE run_id=?",
            (
                "customer@example.com:+8613800138000:private-key",
                json.dumps(
                    {
                        "nested": {"access_token": "legacy-input-secret"},
                        "source_url": (
                            "https://user:pass@example.test/a?access_token=url-secret"
                        ),
                    }
                ),
                "RuntimeError: call +44 20 7946 0958 token=legacy-run-secret",
                result.run_id,
            ),
        )
        connection.execute(
            "UPDATE pipeline_checkpoints SET output_json=?, error=? "
            "WHERE run_id=?",
            (
                json.dumps({"raw_completion": "legacy-output-secret"}),
                "Authorization: Basic legacy-checkpoint-secret",
                result.run_id,
            ),
        )
        connection.execute(
            "INSERT INTO pipeline_effects(run_id, stage, effect_key, "
            "idempotency_token, status, result_json, error, created_at, updated_at) "
            "VALUES (?, 'collect', 'legacy', 'stable-id', 'failed', ?, ?, 'now', 'now')",
            (
                result.run_id,
                json.dumps({"token": "legacy-effect-secret"}),
                "Cookie: sid=legacy-cookie-secret",
            ),
        )
        connection.execute(
            "UPDATE pipeline_metadata SET value='2' "
            "WHERE key='persistence_sanitizer_version'"
        )

    RunStore(database)
    persisted = database.read_bytes()
    for secret in (
        b"input-secret",
        b"header-secret",
        b"legacy-input-secret",
        b"url-secret",
        b"legacy-run-secret",
        b"legacy-output-secret",
        b"legacy-checkpoint-secret",
        b"legacy-effect-secret",
        b"legacy-cookie-secret",
        b"7946 0958",
        b"customer@example.com",
        b"private-key",
    ):
        assert secret not in persisted
    with sqlite3.connect(database) as connection:
        stored_key = connection.execute(
            "SELECT idempotency_key FROM pipeline_runs WHERE run_id=?",
            (result.run_id,),
        ).fetchone()[0]
    assert stored_key == f"run-ref:{result.run_id.removeprefix('run_')}"


def test_runtime_input_output_and_effect_boundaries_preserve_job_json_only(tmp_path):
    database = tmp_path / "runtime-boundary.sqlite"
    public_payload = {
        "position_name": "商业化总监",
        "position_scope": "【岗位职责】\n• 联系行业客户 13800138000",
    }
    public_bytes = json.dumps(
        public_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    handlers = _handlers()

    def collect(context):
        effect = context.effect_once(
            "probe",
            lambda _token: {
                "response": "call +44 20 7946 0958",
                "source_url": (
                    "https://user:pass@example.test/a?X-Amz-Signature=signed&page=2"
                ),
            },
        )
        return {
            "public_payload": public_payload,
            "payload_hash": "9" * 64,
            "effect": effect,
            "provider.output": b"Authorization: Bearer output-secret",
        }

    handlers["collect"] = collect
    result = StagedRuntime(RunStore(database), handlers).run(
        "marcus@example.com:+8613800138000:raw-idempotency-secret",
        {
            "public_payload": public_payload,
            "input": "phone 138.0013.8000 token=input-secret",
        },
    )

    stored = database.read_bytes()
    for secret in (
        b"marcus@example.com",
        b"raw-idempotency-secret",
        b"138.0013.8000",
        b"input-secret",
        b"7946 0958",
        b"user:pass",
        b"signed",
        b"output-secret",
    ):
        assert secret not in stored
    persisted_payload = result.checkpoints["collect"].output["public_payload"]
    assert json.dumps(
        persisted_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") == public_bytes


def test_runtime_does_not_turn_database_paths_or_business_identity_into_placeholders(
    tmp_path,
):
    runtime_db = tmp_path / "runtime.sqlite"
    ops_db = tmp_path / "operations-metrics.sqlite"
    store = RunStore(runtime_db)

    record = store.ensure_run(
        "path-identity-regression",
        {
            "ops_metrics_db": str(ops_db),
            "output_dir": "reports-daily/production-output",
            "direction": "commercial_space_infrastructure",
            "draft_id": "tp_629df7cd100c02b2",
            "snapshot_id": "1" * 64,
            "source_run_id": "run_48a8601f89687959b042373a51fb9478",
            "content_hash": "2" * 64,
            "evidence_hash": "3" * 64,
        },
    )

    assert record.input["ops_metrics_db"] == str(ops_db)
    assert record.input["output_dir"] == "reports-daily/production-output"
    assert record.input["direction"] == "commercial_space_infrastructure"
    assert record.input["draft_id"] == "tp_629df7cd100c02b2"
    assert record.input["snapshot_id"] == "1" * 64
    assert record.input["source_run_id"].startswith("run_")
    assert record.input["content_hash"] == "2" * 64
    assert record.input["evidence_hash"] == "3" * 64
    assert not (tmp_path / "[redacted-token]").exists()


def test_replay_reuses_costly_stages_but_recomputes_cheap_stages(tmp_path):
    counts = Counter()
    store = RunStore(tmp_path / 'runs.sqlite')
    runtime = StagedRuntime(store, _handlers(counts))
    initial = runtime.run('replay-me', {'trace': []})

    replay = runtime.replay(initial.run_id, from_stage='normalize')

    assert replay.status == 'completed'
    assert replay.reused_stages == ('collect', 'basic_research')
    assert replay.executed_stages == (
        'normalize',
        'eventize',
        'score',
        'publish',
    )
    assert counts['collect'] == 1
    assert counts['basic_research'] == 1
    assert counts['normalize'] == 2
    assert counts['publish'] == 2
    assert (
        store.latest_checkpoint(initial.run_id, 'normalize').replay is True
    )
    assert len(store.checkpoint_history(initial.run_id, 'normalize')) == 2


def test_replay_can_explicitly_reexecute_costly_stage_handler(tmp_path):
    counts = Counter()
    store = RunStore(tmp_path / 'runs.sqlite')
    runtime = StagedRuntime(store, _handlers(counts))
    initial = runtime.run('full-replay', {'trace': []})

    replay = runtime.replay(
        initial.run_id, from_stage='collect', reuse_costly=False
    )

    assert replay.executed_stages == STAGES
    assert counts == Counter({stage: 2 for stage in STAGES})


def test_effect_once_prevents_duplicate_costly_call_across_replay(tmp_path):
    counts = Counter()

    def handler(stage):
        def execute(context):
            counts[f'handler:{stage}'] += 1
            value = dict(context.value)
            value.setdefault('trace', [])
            if stage == 'basic_research':
                research = context.effect_once(
                    'metaso:company-1',
                    lambda token: _costly(counts, token),
                )
                value['research'] = research
            value['trace'] = [*value['trace'], stage]
            return value

        return execute

    handlers = {stage: handler(stage) for stage in STAGES}
    store = RunStore(tmp_path / 'runs.sqlite')
    runtime = StagedRuntime(store, handlers)
    initial = runtime.run('effect-cache', {'trace': []})

    replay = runtime.replay(
        initial.run_id, from_stage='basic_research', reuse_costly=False
    )

    assert counts['handler:basic_research'] == 2
    assert counts['external-call'] == 1
    assert replay.output['research']['matches'] == 3
    effect = store.get_effect(
        initial.run_id, 'basic_research', 'metaso:company-1'
    )
    assert effect['status'] == 'completed'
    assert len(effect['idempotency_token']) == 64


def test_changed_upstream_rebuilds_costly_stage_but_reuses_cached_effect(tmp_path):
    counts = Counter()

    def stage_handler(stage):
        def execute(context):
            counts[f'handler:{stage}'] += 1
            value = dict(context.value)
            value['trace'] = [*value.get('trace', []), stage]
            if stage == 'score':
                value['score_version'] = 1
            if stage == 'basic_research':
                value['research'] = context.effect_once(
                    'metaso:company-1',
                    lambda token: _costly(counts, token),
                )
            return value

        return execute

    handlers = {stage: stage_handler(stage) for stage in STAGES}
    store = RunStore(tmp_path / 'runs.sqlite')
    runtime = StagedRuntime(store, handlers)
    initial = runtime.run('changed-input', {'trace': []})

    def new_score(context):
        counts['handler:score'] += 1
        value = dict(context.value)
        value['trace'] = [*value.get('trace', []), 'score']
        value['score_version'] = 2
        return value

    runtime.handlers['score'] = new_score
    replay = runtime.replay(initial.run_id, from_stage='score')

    assert counts['handler:basic_research'] == 2
    assert counts['external-call'] == 1
    assert 'basic_research' in replay.executed_stages
    assert replay.output['score_version'] == 2


def _costly(counts, token):
    counts['external-call'] += 1
    return {'matches': 3, 'request_token': token}


def test_effect_failure_is_recorded_and_retried_on_resume(tmp_path):
    attempts = Counter()
    should_fail = {'value': True}

    def passthrough(context):
        return context.value

    handlers = {stage: passthrough for stage in STAGES}

    def research(context):
        def operation(_token):
            attempts['external'] += 1
            if should_fail.pop('value', False):
                raise OSError('provider unavailable')
            return {'ok': True}

        return {
            **context.value,
            'research': context.effect_once('provider-call', operation),
        }

    handlers['basic_research'] = research
    store = RunStore(tmp_path / 'runs.sqlite')
    runtime = StagedRuntime(store, handlers)

    with pytest.raises(StageExecutionError) as caught:
        runtime.run('retry-effect', {'query': 'test'})

    failed_effect = store.get_effect(
        caught.value.run_id, 'basic_research', 'provider-call'
    )
    assert failed_effect['status'] == 'failed'
    assert 'provider unavailable' in failed_effect['error']

    result = runtime.resume(caught.value.run_id)

    assert result.status == 'completed'
    assert attempts['external'] == 2
    assert result.output['research'] == {'ok': True}


def test_non_serializable_stage_output_fails_with_checkpoint(tmp_path):
    handlers = _handlers()
    handlers['score'] = lambda _context: object()
    store = RunStore(tmp_path / 'runs.sqlite')
    runtime = StagedRuntime(store, handlers)

    with pytest.raises(StageExecutionError) as caught:
        runtime.run('bad-output', {'trace': []})

    checkpoint = store.latest_checkpoint(caught.value.run_id, 'score')
    assert checkpoint.status == 'failed'
    assert 'JSON serialisable' in checkpoint.error


def test_runtime_requires_exact_stage_handler_set(tmp_path):
    handlers = _handlers()
    handlers.pop('publish')
    with pytest.raises(ValueError, match='missing handlers'):
        StagedRuntime(RunStore(tmp_path / 'runs.sqlite'), handlers)

    handlers = _handlers()
    handlers['unknown'] = lambda context: context.value
    with pytest.raises(ValueError, match='unknown handlers'):
        StagedRuntime(RunStore(tmp_path / 'runs2.sqlite'), handlers)
