from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from ht_lead_radar.sources import (
    AdapterResult,
    AdaptiveSourceScheduler,
    HTTPResponse,
    SourceHealthStore,
    SourceRegistry,
    SourceRegistryError,
    adapter_client,
    health_check_source,
    spike_source,
)


def _source(
    *,
    source_id='official-news',
    adapter='direct_http',
    config=None,
    base=3600,
    minimum=600,
    maximum=86400,
    threshold=3,
    jitter=0.0,
    timeout=20.0,
):
    adapter_configs = {
        'direct_http': {'url': 'https://example.com/news'},
        'miniflux': {
            'base_url': 'https://feeds.example.com/',
            'feed_id': 7,
            'api_token': 'secret',
        },
        'rsshub': {
            'base_url': 'https://rss.example.com/',
            'route': '/company/news',
        },
        'changedetection': {
            'base_url': 'https://changes.example.com/',
            'watch_id': 'watch-1',
            'api_token': 'secret',
            'api_token_header': 'x-api-key',
        },
    }
    registry = SourceRegistry.from_dict(
        {
            'sources': [
                {
                    'id': source_id,
                    'name': 'Official company news',
                    'tier': 'A',
                    'ownership': 'official',
                    'adapter': adapter,
                    'schedule': {
                        'base_interval_seconds': base,
                        'min_interval_seconds': minimum,
                        'max_interval_seconds': maximum,
                        'failure_threshold': threshold,
                        'jitter_ratio': jitter,
                        'timeout_seconds': timeout,
                    },
                    'content_policy': {
                        'retention': 'snippet',
                        'retention_days': 365,
                        'license': 'all-rights-reserved',
                        'commercial_use': 'restricted',
                        'terms_url': 'https://example.com/terms',
                    },
                    'config': config or adapter_configs[adapter],
                    'estimated_cost': 0.25,
                    'tags': ['robotics', 'china'],
                }
            ]
        }
    )
    return registry.get(source_id)


def test_registry_round_trip_and_required_governance_fields(tmp_path):
    source = _source()
    path = tmp_path / 'sources.json'
    path.write_text(
        json.dumps({'sources': [source.to_dict()]}, ensure_ascii=False),
        encoding='utf-8',
    )

    loaded = SourceRegistry.from_json(path)

    assert len(loaded) == 1
    assert loaded.get('official-news').tier == 'A'
    assert loaded.get('official-news').ownership == 'official'
    assert loaded.get('official-news').content_policy.retention == 'snippet'
    assert loaded.enabled() == tuple(loaded)


@pytest.mark.parametrize(
    'change, expected',
    [
        ({'tier': 'Z'}, '.tier'),
        ({'ownership': ''}, '.ownership'),
        ({'adapter': 'magic'}, '.adapter'),
        ({'content_policy': {}}, '.license'),
        (
            {
                'content_policy': {
                    'retention': 'none',
                    'license': 'proprietary',
                    'commercial_use': 'prohibited',
                }
            },
            'cannot be enabled',
        ),
        (
            {
                'schedule': {
                    'base_interval_seconds': 5,
                    'min_interval_seconds': 10,
                    'max_interval_seconds': 20,
                }
            },
            'min_interval <= base_interval',
        ),
    ],
)
def test_registry_rejects_unsafe_or_incomplete_sources(change, expected):
    base = {
        'id': 'source',
        'name': 'Source',
        'tier': 'B',
        'ownership': 'media',
        'adapter': 'direct_http',
        'schedule': {
            'base_interval_seconds': 60,
            'min_interval_seconds': 30,
            'max_interval_seconds': 600,
        },
        'content_policy': {
            'retention': 'metadata_only',
            'license': 'unknown',
            'commercial_use': 'unknown',
        },
        'config': {'url': 'https://example.com'},
    }
    base.update(change)

    with pytest.raises(SourceRegistryError) as caught:
        SourceRegistry.from_dict({'sources': [base]})

    assert expected in str(caught.value)


def test_registry_rejects_duplicate_ids_and_adapter_config():
    value = _source().to_dict()
    duplicate = dict(value)
    duplicate['config'] = {}

    with pytest.raises(SourceRegistryError) as caught:
        SourceRegistry.from_dict({'sources': [value, duplicate]})

    assert 'duplicates' in str(caught.value)
    assert 'config.url is required' in str(caught.value)


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, url, *, method='GET', headers=None, timeout=20.0):
        self.calls.append(
            {
                'url': url,
                'method': method,
                'headers': dict(headers or {}),
                'timeout': timeout,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response(body, content_type='application/json', status=200):
    if isinstance(body, str):
        body = body.encode()
    return HTTPResponse(
        status_code=status,
        body=body,
        headers={'content-type': content_type, 'etag': 'abc'},
        elapsed_ms=12.5,
        url='https://resolved.example/item',
    )


def test_direct_http_adapter_and_command_friendly_checks():
    source = _source()
    transport = FakeTransport(_response('<html>news</html>', 'text/html'))

    health = health_check_source(source, transport)
    spike = spike_source(source, transport, sample_size=1)

    assert health == {
        'adapter': 'direct_http',
        'source_id': 'official-news',
        'ok': True,
        'status_code': 200,
        'latency_ms': 12.5,
        'parse_yield': 1,
        'error': None,
        'metadata': {},
        'check': 'health',
    }
    assert spike['check'] == 'spike'
    assert spike['items'][0]['content'] == '<html>news</html>'
    assert transport.calls[0]['headers']['Accept'].startswith('text/html')


def test_miniflux_adapter_normalizes_entries_and_uses_token():
    source = _source(adapter='miniflux')
    body = {
        'total': 2,
        'entries': [
            {
                'id': 1,
                'url': 'https://a.example/1',
                'title': 'A',
                'content': 'one',
                'published_at': '2026-01-01T00:00:00Z',
                'feed_id': 7,
            },
            {
                'id': 2,
                'url': 'https://a.example/2',
                'title': 'B',
                'content': 'two',
                'published_at': '2026-01-02T00:00:00Z',
                'feed_id': 7,
            },
        ],
    }
    transport = FakeTransport(_response(json.dumps(body)))

    result = adapter_client('miniflux', transport).fetch(source)

    assert result.ok is True
    assert result.parse_yield == 2
    assert result.items[1]['title'] == 'B'
    assert 'feed_id=7' in transport.calls[0]['url']
    assert transport.calls[0]['headers']['X-Auth-Token'] == 'secret'


def test_rsshub_and_changedetection_are_optional_http_adapters():
    rss = _source(adapter='rsshub')
    rss_transport = FakeTransport(
        _response('<rss><channel/></rss>', 'application/rss+xml')
    )
    rss_result = adapter_client('rsshub', rss_transport).fetch(rss)
    assert rss_result.ok is True
    assert rss_result.items[0]['content'].startswith('<rss>')
    assert rss_transport.calls[0]['url'] == 'https://rss.example.com/company/news'

    changed = _source(adapter='changedetection')
    changed_transport = FakeTransport(
        _response(json.dumps({'history': {'1': 'snapshot-a', '2': 'snapshot-b'}}))
    )
    changed_result = adapter_client(
        'changedetection', changed_transport
    ).fetch(changed)
    assert changed_result.parse_yield == 2
    assert changed_result.items[0] == {
        'timestamp': '1',
        'snapshot': 'snapshot-a',
    }
    assert changed_transport.calls[0]['headers']['x-api-key'] == 'secret'


def test_adapter_failure_is_returned_not_raised():
    source = _source()
    transport = FakeTransport(OSError('offline'))

    result = adapter_client('direct_http', transport).fetch(source)

    assert result.ok is False
    assert result.status_code is None
    assert 'offline' in result.error


def test_injected_source_transport_cannot_ignore_wall_clock_timeout():
    class HangingTransport:
        @staticmethod
        def request(_url, **_kwargs):
            time.sleep(0.5)
            return _response("late")

    source = _source(timeout=0.02)
    started = time.monotonic()
    result = adapter_client('direct_http', HangingTransport()).fetch(source)

    assert time.monotonic() - started < 0.15
    assert result.ok is False
    assert result.status_code is None
    assert "TimeoutError" in str(result.error)


@pytest.mark.parametrize('kind', ['miniflux', 'changedetection'])
def test_json_adapters_preserve_http_status_for_non_json_error(kind):
    source = _source(adapter=kind)
    transport = FakeTransport(_response('<h1>unauthorized</h1>', 'text/html', 401))

    result = adapter_client(kind, transport).fetch(source)

    assert result.ok is False
    assert result.status_code == 401
    assert result.error == 'HTTP 401'


def test_health_persistence_success_metrics_and_due_selection(tmp_path):
    now = datetime(2026, 7, 25, 5, tzinfo=timezone.utc)
    source = _source(base=3600, minimum=600, maximum=86400)
    registry = SourceRegistry([source])
    store = SourceHealthStore(tmp_path / 'health.sqlite')
    scheduler = AdaptiveSourceScheduler(store, clock=lambda: now, jitter=lambda *_: 0)

    assert scheduler.due_sources(registry) == (source,)
    health = scheduler.record_success(
        source,
        http_status=200,
        latency_ms=125.5,
        parse_yield=10,
        duplicate_ratio=0.2,
        new_items=3,
        estimated_cost=0.5,
    )

    assert health.last_success == now.isoformat()
    assert health.last_new_item == now.isoformat()
    assert health.http_status == 200
    assert health.latency_ms == 125.5
    assert health.parse_yield == 10
    assert health.duplicate_ratio == 0.2
    assert health.consecutive_failures == 0
    assert health.estimated_cost == 0.5
    assert health.next_due == (now + timedelta(seconds=2700)).isoformat()
    assert scheduler.is_due(source, now + timedelta(seconds=2699)) is False
    assert scheduler.is_due(source, now + timedelta(seconds=2700)) is True
    assert SourceHealthStore(tmp_path / 'health.sqlite').get(source.id) == health


def test_adaptive_empty_and_duplicate_sources_slow_down(tmp_path):
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    source = _source(base=1000, minimum=100, maximum=5000)
    scheduler = AdaptiveSourceScheduler(
        SourceHealthStore(tmp_path / 'health.sqlite'),
        clock=lambda: now,
        jitter=lambda *_: 0,
    )

    empty = scheduler.record_success(
        source,
        http_status=200,
        latency_ms=10,
        parse_yield=0,
        duplicate_ratio=0,
        new_items=0,
    )
    assert empty.next_due == (now + timedelta(seconds=2000)).isoformat()

    duplicate = scheduler.record_success(
        source,
        http_status=200,
        latency_ms=10,
        parse_yield=20,
        duplicate_ratio=0.95,
        new_items=0,
    )
    assert duplicate.next_due == (now + timedelta(seconds=1750)).isoformat()


def test_exponential_backoff_circuit_breaker_and_success_reset(tmp_path):
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    source = _source(
        base=100,
        minimum=10,
        maximum=1000,
        threshold=3,
        jitter=0.0,
    )
    scheduler = AdaptiveSourceScheduler(
        SourceHealthStore(tmp_path / 'health.sqlite'),
        clock=lambda: now,
        jitter=lambda *_: 0,
    )

    first = scheduler.record_failure(source, error='timeout', http_status=504)
    second = scheduler.record_failure(source, error='timeout', http_status=504)
    third = scheduler.record_failure(source, error='timeout', http_status=504)

    assert first.next_due == (now + timedelta(seconds=100)).isoformat()
    assert second.next_due == (now + timedelta(seconds=200)).isoformat()
    assert third.next_due == (now + timedelta(seconds=400)).isoformat()
    assert third.circuit_open_until == third.next_due
    assert scheduler.is_due(source, now + timedelta(seconds=399)) is False
    assert scheduler.is_due(source, now + timedelta(seconds=400)) is True

    recovered = scheduler.record_success(
        source,
        http_status=200,
        latency_ms=20,
        parse_yield=1,
        duplicate_ratio=0,
        new_items=1,
    )
    assert recovered.consecutive_failures == 0
    assert recovered.circuit_open_until is None
    assert recovered.last_error is None


def test_source_health_never_persists_http_credentials(tmp_path):
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    source = _source()
    database = tmp_path / "health-secrets.sqlite"
    scheduler = AdaptiveSourceScheduler(
        SourceHealthStore(database),
        clock=lambda: now,
        jitter=lambda *_: 0,
    )

    health = scheduler.record_failure(
        source,
        error=(
            "Authorization: Basic c291cmNlOnNlY3JldA==\n"
            "Cookie: session=source-cookie-secret"
        ),
    )

    assert "c291cmNlOnNlY3JldA==" not in str(health.last_error)
    assert "source-cookie-secret" not in str(health.last_error)
    persisted = database.read_bytes()
    assert b"c291cmNlOnNlY3JldA==" not in persisted
    assert b"source-cookie-secret" not in persisted


def test_jitter_is_deterministic_and_never_exceeds_schedule_bounds(tmp_path):
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    source = _source(
        base=100,
        minimum=50,
        maximum=120,
        threshold=1,
        jitter=1.0,
    )
    scheduler = AdaptiveSourceScheduler(
        SourceHealthStore(tmp_path / 'health.sqlite'),
        clock=lambda: now,
        jitter=lambda *_: 1.0,
    )

    health = scheduler.record_failure(source, error='down')

    assert health.next_due == (now + timedelta(seconds=120)).isoformat()
    assert health.circuit_open_until == health.next_due


def test_record_result_bridges_adapter_and_health(tmp_path):
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    source = _source()
    scheduler = AdaptiveSourceScheduler(
        SourceHealthStore(tmp_path / 'health.sqlite'),
        clock=lambda: now,
        jitter=lambda *_: 0,
    )
    result = AdapterResult(
        adapter='direct_http',
        source_id=source.id,
        ok=True,
        status_code=304,
        latency_ms=4,
        items=(),
    )

    health = scheduler.record_result(source, result)

    assert health.http_status == 304
    assert health.parse_yield == 0
    assert health.last_success == now.isoformat()


def test_metric_validation_rejects_impossible_new_item_count(tmp_path):
    source = _source()
    scheduler = AdaptiveSourceScheduler(
        SourceHealthStore(tmp_path / 'health.sqlite')
    )

    with pytest.raises(ValueError, match='cannot exceed'):
        scheduler.record_success(
            source,
            http_status=200,
            latency_ms=1,
            parse_yield=1,
            duplicate_ratio=0,
            new_items=2,
        )
