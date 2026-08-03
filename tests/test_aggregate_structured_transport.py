"""Transport and audit regressions for structured aggregate-source requests."""

from contextlib import AbstractContextManager
import json

import pytest

from ht_lead_radar.aggregate_adapters.coordinator import (
    DedicatedAggregateCoordinator,
    PublicHttpFetcher,
)


class _Response(AbstractContextManager):
    def __init__(self, body: bytes, final_url: str = ""):
        self.body = body
        self.final_url = final_url

    def read(self, _limit: int) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.final_url or "https://api.example/detail"

    def __exit__(self, *_args):
        return None


def test_public_fetcher_posts_json_with_explicit_headers():
    captured = {}

    def urlopen(request, *, timeout):
        captured["method"] = request.get_method()
        captured["body"] = request.data
        captured["content_type"] = request.get_header("Content-type")
        captured["accept"] = request.get_header("Accept")
        captured["timeout"] = timeout
        return _Response(b'{"data": {}}')

    fetcher = PublicHttpFetcher(
        urlopen=urlopen,
        minimum_interval_seconds=0,
        timeout=7,
    )

    result = fetcher.post_json("https://api.example/detail", {"content_id": 7})

    assert json.loads(result) == {"data": {}}
    assert captured == {
        "method": "POST",
        "body": b'{"content_id": 7}',
        "content_type": "application/json",
        "accept": "application/json,text/plain,*/*",
        "timeout": 7,
    }


def test_public_fetcher_reuses_only_explicit_shared_get():
    calls = []

    def urlopen(request, *, timeout):
        del timeout
        calls.append(request.full_url)
        return _Response(b"shared-listing", request.full_url)

    fetcher = PublicHttpFetcher(
        urlopen=urlopen,
        minimum_interval_seconds=0,
        shared_get_urls=("https://example.com/shared",),
    )

    first = fetcher("https://example.com/shared")
    second = fetcher("https://example.com/shared")

    assert first == second == b"shared-listing"
    assert calls == ["https://example.com/shared"]


def test_public_fetcher_keeps_cycle_snapshot_and_does_not_cache_details():
    calls = []

    def urlopen(request, *, timeout):
        del timeout
        calls.append(request.full_url)
        return _Response(f"response-{len(calls)}".encode(), request.full_url)

    fetcher = PublicHttpFetcher(
        urlopen=urlopen,
        minimum_interval_seconds=0,
        shared_get_urls=("https://example.com/shared",),
    )

    assert fetcher("https://example.com/shared") == b"response-1"
    assert fetcher("https://example.com/shared") == b"response-1"
    assert fetcher("https://example.com/detail/1") == b"response-2"
    assert fetcher("https://example.com/detail/1") == b"response-3"
    fetcher.clear_shared_cache()
    assert fetcher("https://example.com/shared") == b"response-4"
    assert calls == [
        "https://example.com/shared",
        "https://example.com/detail/1",
        "https://example.com/detail/1",
        "https://example.com/shared",
    ]


def test_post_audit_files_are_unique_per_request_payload(tmp_path):
    class Fetcher:
        def __call__(self, _url):
            return b""

        def post_json(self, _url, payload):
            return json.dumps(payload).encode()

    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "state.sqlite3",
        fetch=Fetcher(),
        acceptance_dir=tmp_path / "audit",
    )
    post = coordinator._post_adapter_json("cyzone-latest")
    assert post is not None

    post("https://api.example/detail", {"content_id": 1})
    post("https://api.example/detail", {"content_id": 2})

    files = sorted((tmp_path / "audit" / "cyzone-latest").glob("adapter-post-*.html"))
    immutable = [item for item in files if item.stem.count("-") == 3]
    assert len(immutable) == 2
    assert {
        json.loads(path.read_text(encoding="utf-8"))["content_id"]
        for path in immutable
    } == {1, 2}


def test_public_fetcher_rejects_private_and_cross_host_targets():
    with pytest.raises(ValueError, match="unsafe aggregate URL"):
        PublicHttpFetcher(minimum_interval_seconds=0)("http://example.com/a")
    with pytest.raises(ValueError, match="non-public aggregate URL"):
        PublicHttpFetcher(minimum_interval_seconds=0)("https://127.0.0.1/a")

    def redirected(_request, *, timeout):
        del timeout
        return _Response(b"private", "https://metadata.internal/latest")

    fetcher = PublicHttpFetcher(urlopen=redirected, minimum_interval_seconds=0)
    with pytest.raises(ValueError, match="cross-host redirect rejected"):
        fetcher("https://example.com/a")


def test_raw_capture_preserves_two_versions_of_same_request(tmp_path):
    coordinator = DedicatedAggregateCoordinator(
        state_db=tmp_path / "state.sqlite3",
        acceptance_dir=tmp_path / "audit",
    )
    coordinator._write_raw("source", "listing", b"version-one")
    coordinator._write_raw("source", "listing", b"version-two")

    target = tmp_path / "audit" / "source"
    immutable = [item for item in target.glob("listing-*.html")]
    assert len(immutable) == 2
    assert {item.read_bytes() for item in immutable} == {
        b"version-one",
        b"version-two",
    }
    assert (target / "listing.html").read_bytes() == b"version-two"


def test_public_fetcher_bounds_non_socket_response_read():
    import time

    class SlowResponse(_Response):
        def read(self, _limit: int) -> bytes:
            time.sleep(0.02)
            return self.body

    def slow_open(_request, *, timeout):
        del timeout
        return SlowResponse(b"slow", "https://example.com/slow")

    fetcher = PublicHttpFetcher(
        urlopen=slow_open,
        minimum_interval_seconds=0,
        timeout=0.001,
    )
    with pytest.raises(TimeoutError, match="response read exceeded deadline"):
        fetcher("https://example.com/slow")
