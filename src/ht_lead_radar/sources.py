"""Source registry, adapter clients, health telemetry, and adaptive scheduling.

The module deliberately uses only the Python standard library.  Sidecars such
as Miniflux, RSSHub, and changedetection.io are optional HTTP dependencies:
configuring an adapter never requires the service to be installed locally.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from .http_runtime import call_with_wallclock, read_response_body
from .sanitization import sanitize_text


ADAPTER_KINDS = frozenset(
    {'direct_http', 'miniflux', 'rsshub', 'changedetection'}
)
SOURCE_TIERS = frozenset({'A', 'B', 'C', 'D'})
OWNERSHIP_KINDS = frozenset(
    {
        'official',
        'government',
        'regulator',
        'exchange',
        'media',
        'aggregator',
        'academic',
        'community',
        'other',
    }
)
RETENTION_MODES = frozenset({'metadata_only', 'snippet', 'full_text', 'none'})
COMMERCIAL_USE_POLICIES = frozenset(
    {'allowed', 'restricted', 'unknown', 'prohibited'}
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class SourceRegistryError(ValueError):
    """Raised when a source registry is incomplete or internally inconsistent."""

    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__('invalid source registry: ' + '; '.join(self.errors))


@dataclass(frozen=True)
class ContentPolicy:
    """Per-source licence and storage boundary.

    ``license`` records the site/data licence or ``"unknown"``.  The field is
    required so that uncertainty is explicit rather than silently ignored.
    """

    retention: str
    license: str
    commercial_use: str
    retention_days: int | None = None
    terms_url: str | None = None
    robots_respected: bool = True
    notes: str = ''

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'ContentPolicy':
        return cls(
            retention=str(value.get('retention', '')).strip(),
            license=str(value.get('license', '')).strip(),
            commercial_use=str(value.get('commercial_use', '')).strip(),
            retention_days=value.get('retention_days'),
            terms_url=_optional_text(value.get('terms_url')),
            robots_respected=bool(value.get('robots_respected', True)),
            notes=str(value.get('notes', '')).strip(),
        )

    def validate(self, prefix: str = 'content_policy') -> list[str]:
        errors: list[str] = []
        if self.retention not in RETENTION_MODES:
            errors.append(
                f'{prefix}.retention must be one of {sorted(RETENTION_MODES)}'
            )
        if not self.license:
            errors.append(f'{prefix}.license is required (use "unknown" explicitly)')
        if self.commercial_use not in COMMERCIAL_USE_POLICIES:
            errors.append(
                f'{prefix}.commercial_use must be one of '
                f'{sorted(COMMERCIAL_USE_POLICIES)}'
            )
        if self.retention_days is not None:
            if (
                isinstance(self.retention_days, bool)
                or not isinstance(self.retention_days, int)
                or self.retention_days <= 0
            ):
                errors.append(f'{prefix}.retention_days must be a positive integer')
        if self.retention == 'none' and self.retention_days is not None:
            errors.append(f'{prefix}.retention_days is invalid when retention=none')
        if not self.robots_respected:
            errors.append(f'{prefix}.robots_respected must be true')
        return errors


@dataclass(frozen=True)
class SourceSchedule:
    base_interval_seconds: int
    min_interval_seconds: int
    max_interval_seconds: int
    timeout_seconds: float = 20.0
    failure_threshold: int = 3
    jitter_ratio: float = 0.15

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'SourceSchedule':
        base = value.get('base_interval_seconds')
        minimum = value.get('min_interval_seconds', base)
        maximum = value.get('max_interval_seconds', base)
        return cls(
            base_interval_seconds=base,
            min_interval_seconds=minimum,
            max_interval_seconds=maximum,
            timeout_seconds=value.get('timeout_seconds', 20.0),
            failure_threshold=value.get('failure_threshold', 3),
            jitter_ratio=value.get('jitter_ratio', 0.15),
        )

    def validate(self, prefix: str = 'schedule') -> list[str]:
        errors: list[str] = []
        numeric = {
            'base_interval_seconds': self.base_interval_seconds,
            'min_interval_seconds': self.min_interval_seconds,
            'max_interval_seconds': self.max_interval_seconds,
            'failure_threshold': self.failure_threshold,
        }
        for name, value in numeric.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                errors.append(f'{prefix}.{name} must be a positive integer')
        if not errors and not (
            self.min_interval_seconds
            <= self.base_interval_seconds
            <= self.max_interval_seconds
        ):
            errors.append(
                f'{prefix} must satisfy min_interval <= base_interval <= max_interval'
            )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
        ):
            errors.append(f'{prefix}.timeout_seconds must be positive')
        if (
            isinstance(self.jitter_ratio, bool)
            or not isinstance(self.jitter_ratio, (int, float))
            or not 0 <= self.jitter_ratio <= 1
        ):
            errors.append(f'{prefix}.jitter_ratio must be between 0 and 1')
        return errors


@dataclass(frozen=True)
class SourceDefinition:
    id: str
    name: str
    tier: str
    ownership: str
    adapter: str
    schedule: SourceSchedule
    content_policy: ContentPolicy
    config: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    estimated_cost: float = 0.0
    tags: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'SourceDefinition':
        return cls(
            id=str(value.get('id', '')).strip(),
            name=str(value.get('name', '')).strip(),
            tier=str(value.get('tier', '')).strip().upper(),
            ownership=str(value.get('ownership', '')).strip().lower(),
            adapter=str(value.get('adapter', '')).strip().lower(),
            schedule=SourceSchedule.from_dict(value.get('schedule') or {}),
            content_policy=ContentPolicy.from_dict(
                value.get('content_policy') or {}
            ),
            config=dict(value.get('config') or {}),
            enabled=bool(value.get('enabled', True)),
            estimated_cost=value.get('estimated_cost', 0.0),
            tags=tuple(str(item) for item in value.get('tags', ())),
        )

    def validate(self, index: int | None = None) -> list[str]:
        prefix = f'sources[{index}]' if index is not None else f'source[{self.id}]'
        errors: list[str] = []
        if not self.id:
            errors.append(f'{prefix}.id is required')
        elif not all(character.isalnum() or character in '._-' for character in self.id):
            errors.append(
                f'{prefix}.id may contain only letters, digits, dot, underscore, hyphen'
            )
        if not self.name:
            errors.append(f'{prefix}.name is required')
        if self.tier not in SOURCE_TIERS:
            errors.append(f'{prefix}.tier must be one of {sorted(SOURCE_TIERS)}')
        if self.ownership not in OWNERSHIP_KINDS:
            errors.append(
                f'{prefix}.ownership must be one of {sorted(OWNERSHIP_KINDS)}'
            )
        if self.adapter not in ADAPTER_KINDS:
            errors.append(
                f'{prefix}.adapter must be one of {sorted(ADAPTER_KINDS)}'
            )
        errors.extend(self.schedule.validate(f'{prefix}.schedule'))
        errors.extend(self.content_policy.validate(f'{prefix}.content_policy'))
        if (
            isinstance(self.estimated_cost, bool)
            or not isinstance(self.estimated_cost, (int, float))
            or self.estimated_cost < 0
        ):
            errors.append(f'{prefix}.estimated_cost must be non-negative')
        if self.enabled and self.content_policy.commercial_use == 'prohibited':
            errors.append(
                f'{prefix} cannot be enabled when commercial_use=prohibited'
            )
        errors.extend(_validate_adapter_config(self.adapter, self.config, prefix))
        return errors

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value['tags'] = list(self.tags)
        value['config'] = dict(self.config)
        return value


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ''
    return text or None


def _is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(('http://', 'https://'))


def _validate_adapter_config(
    adapter: str, config: Mapping[str, Any], prefix: str
) -> list[str]:
    errors: list[str] = []
    required: dict[str, tuple[str, ...]] = {
        'direct_http': ('url',),
        'miniflux': ('base_url', 'feed_id'),
        'rsshub': ('base_url', 'route'),
        'changedetection': ('base_url', 'watch_id'),
    }
    for field_name in required.get(adapter, ()):
        value = config.get(field_name)
        if value is None or value == '':
            errors.append(f'{prefix}.config.{field_name} is required for {adapter}')
    for field_name in ('url', 'base_url'):
        if field_name in config and not _is_http_url(config[field_name]):
            errors.append(f'{prefix}.config.{field_name} must be an HTTP(S) URL')
    headers = config.get('headers', {})
    if headers is not None and not isinstance(headers, Mapping):
        errors.append(f'{prefix}.config.headers must be an object')
    return errors


class SourceRegistry:
    """Validated immutable lookup of source definitions."""

    def __init__(self, sources: Iterable[SourceDefinition]):
        items = tuple(sources)
        errors: list[str] = []
        seen: set[str] = set()
        for index, source in enumerate(items):
            errors.extend(source.validate(index))
            if source.id in seen:
                errors.append(f'sources[{index}].id duplicates {source.id!r}')
            seen.add(source.id)
        if not items:
            errors.append('sources must contain at least one source')
        if errors:
            raise SourceRegistryError(errors)
        self._sources = items
        self._by_id = {source.id: source for source in items}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'SourceRegistry':
        raw_sources = value.get('sources')
        if not isinstance(raw_sources, list):
            raise SourceRegistryError(['sources must be an array'])
        parsed: list[SourceDefinition] = []
        parse_errors: list[str] = []
        for index, source in enumerate(raw_sources):
            if not isinstance(source, Mapping):
                parse_errors.append(f'sources[{index}] must be an object')
                continue
            try:
                parsed.append(SourceDefinition.from_dict(source))
            except (TypeError, ValueError) as error:
                parse_errors.append(f'sources[{index}] cannot be parsed: {error}')
        if parse_errors:
            raise SourceRegistryError(parse_errors)
        return cls(parsed)

    @classmethod
    def from_json(cls, path: str | Path) -> 'SourceRegistry':
        with Path(path).open('r', encoding='utf-8') as handle:
            value = json.load(handle)
        if not isinstance(value, Mapping):
            raise SourceRegistryError(['registry root must be an object'])
        return cls.from_dict(value)

    def get(self, source_id: str) -> SourceDefinition:
        try:
            return self._by_id[source_id]
        except KeyError as error:
            raise KeyError(f'unknown source id: {source_id}') from error

    def enabled(self) -> tuple[SourceDefinition, ...]:
        return tuple(source for source in self._sources if source.enabled)

    def to_dict(self) -> dict[str, Any]:
        return {'sources': [source.to_dict() for source in self._sources]}

    def __iter__(self):
        return iter(self._sources)

    def __len__(self) -> int:
        return len(self._sources)


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]
    elapsed_ms: float
    url: str

    def text(self) -> str:
        content_type = self.headers.get('content-type', '')
        charset = 'utf-8'
        if 'charset=' in content_type:
            charset = content_type.split('charset=', 1)[1].split(';', 1)[0].strip()
        return self.body.decode(charset, errors='replace')


class HTTPTransport(Protocol):
    def request(
        self,
        url: str,
        *,
        method: str = 'GET',
        headers: Mapping[str, str] | None = None,
        timeout: float = 20.0,
    ) -> HTTPResponse: ...


class UrllibTransport:
    """Small injectable transport used by all adapters."""

    def request(
        self,
        url: str,
        *,
        method: str = 'GET',
        headers: Mapping[str, str] | None = None,
        timeout: float = 20.0,
    ) -> HTTPResponse:
        request = Request(url, method=method, headers=dict(headers or {}))
        started = time.monotonic()
        deadline = started + timeout
        try:
            response = call_with_wallclock(
                urlopen,
                timeout,
                request,
                timeout=timeout,
                timeout_message='HTTP connect exceeded deadline',
                worker_name='source-connect',
            )
            with response:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        'HTTP request exceeded deadline before body read'
                    )
                body = read_response_body(
                    response,
                    max_bytes=5_000_000,
                    timeout=remaining,
                )
                response_headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
                return HTTPResponse(
                    status_code=int(response.status),
                    body=body,
                    headers=response_headers,
                    elapsed_ms=(time.monotonic() - started) * 1000,
                    url=response.geturl(),
                )
        except HTTPError as error:
            remaining = deadline - time.monotonic()
            body = (
                read_response_body(
                    error,
                    max_bytes=1_000_000,
                    timeout=remaining,
                )
                if remaining > 0
                else b''
            )
            return HTTPResponse(
                status_code=int(error.code),
                body=body,
                headers={key.lower(): value for key, value in error.headers.items()},
                elapsed_ms=(time.monotonic() - started) * 1000,
                url=url,
            )
        except URLError:
            raise


@dataclass(frozen=True)
class AdapterResult:
    adapter: str
    source_id: str
    ok: bool
    status_code: int | None
    latency_ms: float
    items: tuple[Mapping[str, Any], ...] = ()
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def parse_yield(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            'adapter': self.adapter,
            'source_id': self.source_id,
            'ok': self.ok,
            'status_code': self.status_code,
            'latency_ms': round(self.latency_ms, 3),
            'parse_yield': self.parse_yield,
            'items': [dict(item) for item in self.items],
            'error': self.error,
            'metadata': dict(self.metadata),
        }


class AdapterClient:
    kind = ''

    def __init__(self, transport: HTTPTransport | None = None):
        self.transport = transport or UrllibTransport()

    def fetch(self, source: SourceDefinition) -> AdapterResult:
        raise NotImplementedError

    def health_check(self, source: SourceDefinition) -> dict[str, Any]:
        result = self.fetch(source)
        value = result.to_dict()
        value['check'] = 'health'
        value.pop('items', None)
        return value

    def spike(self, source: SourceDefinition, sample_size: int = 3) -> dict[str, Any]:
        if sample_size <= 0:
            raise ValueError('sample_size must be positive')
        result = self.fetch(source)
        value = result.to_dict()
        value['check'] = 'spike'
        value['items'] = value['items'][:sample_size]
        return value

    def _request(
        self,
        source: SourceDefinition,
        url: str,
        *,
        accept: str,
    ) -> HTTPResponse:
        headers = {
            'Accept': accept,
            'User-Agent': 'HT-Lead-Radar/0.2 (+public-source-research)',
        }
        headers.update(
            {
                str(key): str(value)
                for key, value in (source.config.get('headers') or {}).items()
            }
        )
        token = _optional_text(source.config.get('api_token'))
        if token:
            header_name = str(
                source.config.get('api_token_header', 'X-Auth-Token')
            )
            headers[header_name] = token
        request_budget = float(source.schedule.timeout_seconds)
        return call_with_wallclock(
            self.transport.request,
            request_budget,
            url,
            headers=headers,
            timeout=request_budget,
            timeout_message='source transport exceeded wall-clock deadline',
            worker_name='source-adapter-transport',
        )

    def _failed(
        self,
        source: SourceDefinition,
        error: Exception,
        started: float,
    ) -> AdapterResult:
        return AdapterResult(
            adapter=self.kind,
            source_id=source.id,
            ok=False,
            status_code=None,
            latency_ms=(time.monotonic() - started) * 1000,
            error=f'{type(error).__name__}: {error}',
        )


class DirectHTTPClient(AdapterClient):
    kind = 'direct_http'

    def fetch(self, source: SourceDefinition) -> AdapterResult:
        started = time.monotonic()
        try:
            response = self._request(
                source,
                str(source.config['url']),
                accept='text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
            )
            text = response.text()
            item = {
                'url': response.url,
                'content': text,
                'content_type': response.headers.get('content-type', ''),
                'etag': response.headers.get('etag'),
                'last_modified': response.headers.get('last-modified'),
            }
            return AdapterResult(
                adapter=self.kind,
                source_id=source.id,
                ok=200 <= response.status_code < 400,
                status_code=response.status_code,
                latency_ms=response.elapsed_ms,
                items=(item,) if text else (),
                error=None
                if 200 <= response.status_code < 400
                else f'HTTP {response.status_code}',
            )
        except Exception as error:
            return self._failed(source, error, started)


class MinifluxClient(AdapterClient):
    kind = 'miniflux'

    def fetch(self, source: SourceDefinition) -> AdapterResult:
        started = time.monotonic()
        try:
            base_url = str(source.config['base_url']).rstrip('/') + '/'
            query = {
                'feed_id': source.config['feed_id'],
                'limit': int(source.config.get('limit', 100)),
                'order': 'published_at',
                'direction': 'desc',
            }
            if source.config.get('status'):
                query['status'] = source.config['status']
            url = urljoin(base_url, 'v1/entries') + '?' + urlencode(query)
            response = self._request(
                source, url, accept='application/json'
            )
            ok = 200 <= response.status_code < 300
            if not ok:
                return AdapterResult(
                    adapter=self.kind,
                    source_id=source.id,
                    ok=False,
                    status_code=response.status_code,
                    latency_ms=response.elapsed_ms,
                    error=f'HTTP {response.status_code}',
                )
            payload = json.loads(response.text()) if response.body else {}
            entries = payload.get('entries', []) if isinstance(payload, Mapping) else []
            items = tuple(
                {
                    'id': entry.get('id'),
                    'url': entry.get('url'),
                    'title': entry.get('title'),
                    'content': entry.get('content'),
                    'published_at': entry.get('published_at'),
                    'feed_id': entry.get('feed_id'),
                }
                for entry in entries
                if isinstance(entry, Mapping)
            )
            return AdapterResult(
                adapter=self.kind,
                source_id=source.id,
                ok=ok,
                status_code=response.status_code,
                latency_ms=response.elapsed_ms,
                items=items,
                error=None if ok else f'HTTP {response.status_code}',
                metadata={'total': payload.get('total', len(items))}
                if isinstance(payload, Mapping)
                else {},
            )
        except Exception as error:
            return self._failed(source, error, started)


class RSSHubClient(AdapterClient):
    kind = 'rsshub'

    def fetch(self, source: SourceDefinition) -> AdapterResult:
        """Fetch a route as an opaque feed document.

        Feed parsing belongs to the normalize stage.  Keeping the raw XML here
        allows the same checkpoint/replay machinery to work for RSS and Atom.
        """

        started = time.monotonic()
        try:
            base_url = str(source.config['base_url']).rstrip('/') + '/'
            route = str(source.config['route']).lstrip('/')
            response = self._request(
                source,
                urljoin(base_url, route),
                accept='application/rss+xml,application/atom+xml,application/xml,text/xml',
            )
            text = response.text()
            item = {
                'url': response.url,
                'content': text,
                'content_type': response.headers.get('content-type', ''),
                'etag': response.headers.get('etag'),
                'last_modified': response.headers.get('last-modified'),
            }
            ok = 200 <= response.status_code < 300
            return AdapterResult(
                adapter=self.kind,
                source_id=source.id,
                ok=ok,
                status_code=response.status_code,
                latency_ms=response.elapsed_ms,
                items=(item,) if text else (),
                error=None if ok else f'HTTP {response.status_code}',
            )
        except Exception as error:
            return self._failed(source, error, started)


class ChangeDetectionClient(AdapterClient):
    kind = 'changedetection'

    def fetch(self, source: SourceDefinition) -> AdapterResult:
        started = time.monotonic()
        try:
            base_url = str(source.config['base_url']).rstrip('/') + '/'
            watch_id = str(source.config['watch_id'])
            endpoint = str(
                source.config.get(
                    'endpoint', f'api/v1/watch/{watch_id}/history'
                )
            ).lstrip('/')
            response = self._request(
                source, urljoin(base_url, endpoint), accept='application/json'
            )
            ok = 200 <= response.status_code < 300
            if not ok:
                return AdapterResult(
                    adapter=self.kind,
                    source_id=source.id,
                    ok=False,
                    status_code=response.status_code,
                    latency_ms=response.elapsed_ms,
                    error=f'HTTP {response.status_code}',
                    metadata={'watch_id': watch_id},
                )
            payload = json.loads(response.text()) if response.body else {}
            items = _changedetection_items(payload)
            return AdapterResult(
                adapter=self.kind,
                source_id=source.id,
                ok=ok,
                status_code=response.status_code,
                latency_ms=response.elapsed_ms,
                items=items,
                error=None if ok else f'HTTP {response.status_code}',
                metadata={'watch_id': watch_id},
            )
        except Exception as error:
            return self._failed(source, error, started)


def _changedetection_items(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, Mapping):
        values = payload.get('history') or payload.get('items') or []
        if isinstance(values, Mapping):
            values = [
                {'timestamp': timestamp, 'snapshot': snapshot}
                for timestamp, snapshot in values.items()
            ]
    else:
        values = []
    return tuple(item for item in values if isinstance(item, Mapping))


def adapter_client(
    kind: str, transport: HTTPTransport | None = None
) -> AdapterClient:
    clients: dict[str, type[AdapterClient]] = {
        'direct_http': DirectHTTPClient,
        'miniflux': MinifluxClient,
        'rsshub': RSSHubClient,
        'changedetection': ChangeDetectionClient,
    }
    try:
        return clients[kind](transport)
    except KeyError as error:
        raise ValueError(f'unsupported adapter: {kind}') from error


def health_check_source(
    source: SourceDefinition, transport: HTTPTransport | None = None
) -> dict[str, Any]:
    """Command-friendly one-shot health response (JSON serialisable)."""

    return adapter_client(source.adapter, transport).health_check(source)


def spike_source(
    source: SourceDefinition,
    transport: HTTPTransport | None = None,
    *,
    sample_size: int = 3,
) -> dict[str, Any]:
    """Command-friendly adapter spike with a bounded result sample."""

    return adapter_client(source.adapter, transport).spike(source, sample_size)


@dataclass
class SourceHealth:
    source_id: str
    last_success: str | None = None
    last_new_item: str | None = None
    http_status: int | None = None
    latency_ms: float | None = None
    parse_yield: int = 0
    duplicate_ratio: float = 0.0
    consecutive_failures: int = 0
    next_due: str | None = None
    estimated_cost: float = 0.0
    circuit_open_until: str | None = None
    last_error: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> 'SourceHealth':
        return cls(**dict(row))


class SourceHealthStore:
    """SQLite-backed per-source health state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS source_health (
                    source_id TEXT PRIMARY KEY,
                    last_success TEXT,
                    last_new_item TEXT,
                    http_status INTEGER,
                    latency_ms REAL,
                    parse_yield INTEGER NOT NULL DEFAULT 0,
                    duplicate_ratio REAL NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    next_due TEXT,
                    estimated_cost REAL NOT NULL DEFAULT 0,
                    circuit_open_until TEXT,
                    last_error TEXT,
                    updated_at TEXT
                )
                '''
            )

    def get(self, source_id: str) -> SourceHealth:
        with self._connect() as connection:
            row = connection.execute(
                'SELECT * FROM source_health WHERE source_id = ?', (source_id,)
            ).fetchone()
        return SourceHealth.from_row(row) if row else SourceHealth(source_id)

    def upsert(self, health: SourceHealth) -> None:
        fields = health.to_dict()
        if fields.get("last_error"):
            fields["last_error"] = sanitize_text(fields["last_error"], limit=500)
        names = tuple(fields)
        placeholders = ', '.join('?' for _ in names)
        assignments = ', '.join(
            f'{name}=excluded.{name}' for name in names if name != 'source_id'
        )
        with self._connect() as connection:
            connection.execute(
                f'''
                INSERT INTO source_health ({', '.join(names)})
                VALUES ({placeholders})
                ON CONFLICT(source_id) DO UPDATE SET {assignments}
                ''',
                tuple(fields[name] for name in names),
            )

    def list(self) -> list[SourceHealth]:
        with self._connect() as connection:
            rows = connection.execute(
                'SELECT * FROM source_health ORDER BY source_id'
            ).fetchall()
        return [SourceHealth.from_row(row) for row in rows]


JitterFunction = Callable[[str, int], float]


def deterministic_jitter(source_id: str, attempt: int) -> float:
    """Stable value in ``[-1, 1]`` for repeatable schedules and tests."""

    digest = hashlib.sha256(f'{source_id}:{attempt}'.encode('utf-8')).digest()
    integer = int.from_bytes(digest[:8], 'big')
    return (integer / ((1 << 64) - 1)) * 2 - 1


class AdaptiveSourceScheduler:
    """Update source health and derive the next due time.

    Success with new content schedules closer to the configured base interval.
    Empty/high-duplicate sources slow down. Failures use exponential backoff;
    after ``failure_threshold`` failures the circuit remains open until the
    computed due time.
    """

    def __init__(
        self,
        store: SourceHealthStore,
        *,
        clock: Callable[[], datetime] = _utc_now,
        jitter: JitterFunction = deterministic_jitter,
    ):
        self.store = store
        self.clock = clock
        self.jitter = jitter

    def is_due(
        self, source: SourceDefinition, now: datetime | None = None
    ) -> bool:
        if not source.enabled:
            return False
        current = (now or self.clock()).astimezone(timezone.utc)
        health = self.store.get(source.id)
        circuit = _parse_time(health.circuit_open_until)
        if circuit and current < circuit:
            return False
        due = _parse_time(health.next_due)
        return due is None or current >= due

    def due_sources(
        self,
        registry: SourceRegistry,
        now: datetime | None = None,
    ) -> tuple[SourceDefinition, ...]:
        current = now or self.clock()
        return tuple(
            source for source in registry.enabled() if self.is_due(source, current)
        )

    def record_success(
        self,
        source: SourceDefinition,
        *,
        http_status: int | None,
        latency_ms: float,
        parse_yield: int,
        duplicate_ratio: float,
        new_items: int,
        estimated_cost: float | None = None,
        now: datetime | None = None,
    ) -> SourceHealth:
        _validate_metrics(latency_ms, parse_yield, duplicate_ratio, new_items)
        current = (now or self.clock()).astimezone(timezone.utc)
        health = self.store.get(source.id)
        if new_items > 0:
            multiplier = 0.75
        elif parse_yield == 0:
            multiplier = 2.0
        elif duplicate_ratio >= 0.9:
            multiplier = 1.75
        elif duplicate_ratio >= 0.5:
            multiplier = 1.25
        else:
            multiplier = 1.0
        interval = _bounded_interval(
            source.schedule, source.schedule.base_interval_seconds * multiplier
        )
        interval = self._with_jitter(source, interval, 0)
        health.last_success = _iso(current)
        if new_items > 0:
            health.last_new_item = _iso(current)
        health.http_status = http_status
        health.latency_ms = float(latency_ms)
        health.parse_yield = int(parse_yield)
        health.duplicate_ratio = float(duplicate_ratio)
        health.consecutive_failures = 0
        health.next_due = _iso(current + timedelta(seconds=interval))
        health.estimated_cost = float(
            source.estimated_cost if estimated_cost is None else estimated_cost
        )
        health.circuit_open_until = None
        health.last_error = None
        health.updated_at = _iso(current)
        self.store.upsert(health)
        return health

    def record_failure(
        self,
        source: SourceDefinition,
        *,
        error: str,
        http_status: int | None = None,
        latency_ms: float | None = None,
        estimated_cost: float | None = None,
        now: datetime | None = None,
    ) -> SourceHealth:
        current = (now or self.clock()).astimezone(timezone.utc)
        health = self.store.get(source.id)
        failures = health.consecutive_failures + 1
        raw_interval = source.schedule.base_interval_seconds * (2 ** (failures - 1))
        interval = _bounded_interval(source.schedule, raw_interval)
        interval = self._with_jitter(source, interval, failures)
        due = current + timedelta(seconds=interval)
        health.http_status = http_status
        health.latency_ms = (
            None if latency_ms is None else max(0.0, float(latency_ms))
        )
        health.parse_yield = 0
        health.duplicate_ratio = 0.0
        health.consecutive_failures = failures
        health.next_due = _iso(due)
        health.estimated_cost = float(
            source.estimated_cost if estimated_cost is None else estimated_cost
        )
        health.circuit_open_until = (
            _iso(due)
            if failures >= source.schedule.failure_threshold
            else None
        )
        health.last_error = sanitize_text(error, limit=500)
        health.updated_at = _iso(current)
        self.store.upsert(health)
        return health

    def record_result(
        self,
        source: SourceDefinition,
        result: AdapterResult,
        *,
        duplicate_ratio: float = 0.0,
        new_items: int | None = None,
        estimated_cost: float | None = None,
        now: datetime | None = None,
    ) -> SourceHealth:
        if result.ok:
            return self.record_success(
                source,
                http_status=result.status_code,
                latency_ms=result.latency_ms,
                parse_yield=result.parse_yield,
                duplicate_ratio=duplicate_ratio,
                new_items=result.parse_yield if new_items is None else new_items,
                estimated_cost=estimated_cost,
                now=now,
            )
        return self.record_failure(
            source,
            error=result.error or 'adapter failed',
            http_status=result.status_code,
            latency_ms=result.latency_ms,
            estimated_cost=estimated_cost,
            now=now,
        )

    def _with_jitter(
        self, source: SourceDefinition, interval: float, attempt: int
    ) -> float:
        factor = 1 + source.schedule.jitter_ratio * self.jitter(source.id, attempt)
        return _bounded_interval(source.schedule, interval * factor)


def _bounded_interval(schedule: SourceSchedule, value: float) -> float:
    return max(
        float(schedule.min_interval_seconds),
        min(float(schedule.max_interval_seconds), float(value)),
    )


def _validate_metrics(
    latency_ms: float,
    parse_yield: int,
    duplicate_ratio: float,
    new_items: int,
) -> None:
    if latency_ms < 0:
        raise ValueError('latency_ms must be non-negative')
    if parse_yield < 0 or new_items < 0:
        raise ValueError('parse_yield and new_items must be non-negative')
    if not 0 <= duplicate_ratio <= 1:
        raise ValueError('duplicate_ratio must be between 0 and 1')
    if new_items > parse_yield:
        raise ValueError('new_items cannot exceed parse_yield')
