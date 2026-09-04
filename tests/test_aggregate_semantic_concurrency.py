from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import sqlite3
import threading
import time

from ht_lead_radar.aggregate_adapters.base import (
    AdapterContext,
    AggregateAdapter,
)
from ht_lead_radar.aggregate_adapters.coordinator import (
    DedicatedAggregateCoordinator,
)
from ht_lead_radar.aggregate_adapters.models import (
    CleanArticle,
    SemanticEvent,
    SourceArticleIndex,
    SourceChannel,
)
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
CHANNEL = SourceChannel(
    source_id="parallel-test",
    name="Parallel test",
    url="https://example.com/list",
    source_grade="B",
    event_prior=("funding",),
    allowed_hosts=("example.com",),
    allowed_path_patterns=(r"/article/\d+",),
)


class _ParallelAdapter(AggregateAdapter):
    adapter_id = "parallel"
    channels = (CHANNEL,)

    def parse_listing(
        self,
        channel: SourceChannel,
        html: bytes,
        context: AdapterContext,
    ) -> list[SourceArticleIndex]:
        del html, context
        items = []
        for position in range(1, 4):
            body = f"Company {position} completed a funding round."
            items.append(
                SourceArticleIndex(
                    source_id=channel.source_id,
                    source_article_id=str(position),
                    channel=channel.name,
                    canonical_url=f"https://example.com/article/{position}",
                    title=f"Company {position} funding update",
                    published_at="2026-07-29",
                    discovered_at=NOW.isoformat(),
                    cursor_value=str(position),
                    listing_page="1",
                    listing_position=position,
                    content_hash=sha256(body.encode()).hexdigest(),
                    discovery_method="exact",
                    summary=body,
                )
            )
        self.validate_listing(channel, items)
        return items

    def parse_detail(
        self,
        channel: SourceChannel,
        index: SourceArticleIndex,
        html: bytes,
        context: AdapterContext,
    ) -> CleanArticle:
        del channel, context
        body = html.decode()
        return CleanArticle(
            index=index,
            clean_body=body,
            content_hash=sha256(body.encode()).hexdigest(),
        )

    def rule_events(
        self,
        channel: SourceChannel,
        article: CleanArticle,
    ) -> list[SemanticEvent]:
        del channel
        company = f"Company {article.index.source_article_id}"
        quote = article.clean_body
        return [
            SemanticEvent(
                source_id=article.index.source_id,
                source_article_id=article.index.source_article_id,
                canonical_url=article.index.canonical_url,
                company_mentions=(company,),
                canonical_company=company,
                event_type="funding",
                event_date=article.index.published_at,
                industry_tags=("technology",),
                event_summary=quote,
                evidence_quotes=(quote,),
                processor="rules:test",
                content_hash=article.content_hash,
                event_status="completed",
            )
        ]


class _BarrierRunner:
    config = SimpleNamespace(provider="minimax", model="MiniMax-M3")

    def __init__(self) -> None:
        self.barrier = threading.Barrier(3)
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = 0

    def run(
        self,
        prompt: str,
        *,
        session_id: str,
        system_prompt: str = "",
    ) -> str:
        del prompt, system_prompt
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.barrier.wait(timeout=2)
            article_id = session_id.split(":")[2]
            company = f"Company {article_id}"
            quote = f"{company} completed a funding round."
            return json.dumps(
                {
                    "events": [
                        {
                            "company": company,
                            "event_type": "funding",
                            "industry_tags": ["technology"],
                            "funding_round": "",
                            "funding_amount": "",
                            "cumulative_funding_amount": "",
                            "investors": [],
                            "event_status": "completed",
                            "event_summary": quote,
                            "evidence_quotes": [quote],
                            "confidence": "high",
                        }
                    ],
                    "ambiguities": [],
                }
            )
        finally:
            with self.lock:
                self.active -= 1


def test_semantics_run_concurrently_but_sqlite_persists_in_listing_order(tmp_path):
    runner = _BarrierRunner()
    routes = {
        "https://example.com/list": b"listing",
        **{
            f"https://example.com/article/{position}": (
                f"Company {position} completed a funding round."
            ).encode()
            for position in range(1, 4)
        },
    }
    state_db = tmp_path / "state.sqlite3"
    coordinator = DedicatedAggregateCoordinator(
        state_db=state_db,
        registry=DedicatedAdapterRegistry((_ParallelAdapter(),)),
        fetch=lambda url: routes[url],
        llm_runner=runner,
        now=NOW,
        semantic_workers=3,
    )

    result = coordinator.collect_source("parallel-test", "hardtech")

    assert result.run.status == "ok"
    assert result.run.detail_success_count == 3
    assert runner.calls == 3
    assert runner.max_active == 3
    with sqlite3.connect(state_db) as connection:
        article_ids = [
            row[0]
            for row in connection.execute(
                """
                SELECT source_article_id
                FROM aggregate_semantic_attempts
                ORDER BY rowid
                """
            )
        ]
    assert article_ids == ["1", "2", "3"]


def test_semantic_worker_setting_is_bounded(monkeypatch, tmp_path):
    runner = _BarrierRunner()
    monkeypatch.setenv("LEAD_RADAR_AGGREGATE_LLM_WORKERS", "99")

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "state.sqlite3",
        registry=DedicatedAdapterRegistry((_ParallelAdapter(),)),
        fetch=lambda url: b"",
        llm_runner=runner,
        now=NOW,
    )

    assert coordinator.semantic_workers == 8


def test_source_watchdog_records_error_after_slow_listing(tmp_path):
    import time

    def slow_fetch(_url):
        time.sleep(0.02)
        return b"listing"

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "watchdog.sqlite3",
        registry=DedicatedAdapterRegistry((_ParallelAdapter(),)),
        fetch=slow_fetch,
        now=NOW,
        source_timeout_seconds=0.001,
    )

    result = coordinator.collect_source("parallel-test", "hardtech")

    assert result.run.status == "error"
    assert "watchdog" in result.run.error


def test_source_watchdog_is_wall_clock_and_forwards_remaining_transport_timeout(
    tmp_path,
):
    observed_timeouts = []
    calls = []

    def slow_fetch(url, *, timeout):
        calls.append(url)
        observed_timeouts.append(timeout)
        time.sleep(0.5)
        return b"listing"

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "wall-clock.sqlite3",
        registry=DedicatedAdapterRegistry((_ParallelAdapter(),)),
        fetch=slow_fetch,
        now=NOW,
        source_timeout_seconds=0.1,
    )

    started = time.monotonic()
    result = coordinator.collect_source("parallel-test", "hardtech")
    elapsed = time.monotonic() - started

    assert elapsed < 0.3
    assert result.run.status == "error"
    assert "watchdog" in result.run.error
    # On a heavily loaded CI worker the real deadline may expire just before
    # the daemon starts. Both zero and one started transports are valid; no
    # work may be started after that boundary.
    assert calls in ([], ["https://example.com/list"])
    if observed_timeouts:
        assert len(observed_timeouts) == 1
        assert 0 < observed_timeouts[0] <= 0.1


def test_watchdog_cancels_queued_semantic_work_without_waiting_for_worker(tmp_path):
    class SlowRunner:
        config = SimpleNamespace(provider="minimax", model="MiniMax-M3")

        def __init__(self):
            self.calls = 0
            self.lock = threading.Lock()
            self.started = threading.Event()
            self.release = threading.Event()
            self.finished = threading.Event()
            self.second_started = threading.Event()

        def run(self, *_args, **_kwargs):
            with self.lock:
                self.calls += 1
                call_number = self.calls
            if call_number > 1:
                self.second_started.set()
            self.started.set()
            try:
                self.release.wait(timeout=2)
                return '{"events":[],"ambiguities":[]}'
            finally:
                self.finished.set()

    runner = SlowRunner()
    routes = {
        "https://example.com/list": b"listing",
        **{
            f"https://example.com/article/{position}": (
                f"Company {position} completed a funding round."
            ).encode()
            for position in range(1, 4)
        },
    }
    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "semantic-wall-clock.sqlite3",
        registry=DedicatedAdapterRegistry((_ParallelAdapter(),)),
        fetch=lambda url: routes[url],
        llm_runner=runner,
        now=NOW,
        semantic_workers=1,
        source_timeout_seconds=0.1,
    )

    started_at = time.monotonic()
    result = coordinator.collect_source("parallel-test", "hardtech")
    elapsed = time.monotonic() - started_at

    try:
        # Retain a generous wall-clock guard for the configured 0.1s deadline
        # without making normal shared-runner scheduling part of correctness.
        assert elapsed < 1.0
        assert result.run.status == "error"
        assert "watchdog" in result.run.error
        if runner.started.is_set():
            # Returning before the deliberately blocked active worker is the
            # behavior under test; it does not depend on CI wall-clock speed.
            assert not runner.finished.is_set()
    finally:
        runner.release.set()
    # A worker can transition from queued to running at the return boundary;
    # give that race time to declare itself and then drain it deterministically.
    if runner.started.wait(timeout=0.5):
        assert runner.finished.wait(timeout=1)
    assert runner.calls in {0, 1}
    assert not runner.second_started.wait(timeout=0.2)


def test_ignored_transport_timeout_cannot_hold_interpreter_at_exit(tmp_path):
    script = """
import time
from ht_lead_radar.aggregate_adapters.coordinator import DedicatedAggregateCoordinator
from ht_lead_radar.aggregate_adapters.registry import DedicatedAdapterRegistry

registry = DedicatedAdapterRegistry.defaults()
source_id = sorted(registry.source_ids)[0]

def stuck_fetch(_url, **_kwargs):
    time.sleep(30)
    return b''

coordinator = DedicatedAggregateCoordinator(
    state_db=r'%s',
    registry=registry,
    fetch=stuck_fetch,
    source_timeout_seconds=0.02,
)
result = coordinator.collect_source(source_id, 'hardtech')
assert result.run.status == 'error'
""" % str(tmp_path / "child.sqlite3").replace("\\", "\\\\")
    environment = dict(os.environ)
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (source_root, environment.get("PYTHONPATH", "")))
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        timeout=1.5,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
