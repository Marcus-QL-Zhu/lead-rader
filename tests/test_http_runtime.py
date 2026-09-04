import time

import pytest

from ht_lead_radar.http_runtime import read_response_body


class _BlockingResponse:
    def read(self, _size=-1):
        time.sleep(1)
        return b"late"


def test_non_socket_read_obeys_tiny_wall_clock_budget():
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline"):
        read_response_body(
            _BlockingResponse(),
            max_bytes=100,
            timeout=0.02,
        )
    assert time.monotonic() - started < 0.12
