from __future__ import annotations

from collections import Counter

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
