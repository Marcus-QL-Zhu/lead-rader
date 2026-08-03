import http.client
import socket
import threading
import time

import pytest

from ht_lead_radar.http_runtime import read_response_body


def _chunked_response(delay: float):
    client, server = socket.socketpair()
    response = http.client.HTTPResponse(client)

    def writer():
        try:
            server.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
            )
            server.sendall(b"4\r\npart\r\n")
            time.sleep(delay)
            server.sendall(b"3\r\nial\r\n0\r\n\r\n")
        finally:
            server.close()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    response.begin()
    return client, response, thread


def test_read_response_body_handles_chunked_socket_response():
    client, response, thread = _chunked_response(0.01)
    try:
        assert read_response_body(response, max_bytes=100, timeout=1) == b"partial"
    finally:
        response.close()
        thread.join(timeout=1)


def test_read_response_body_stops_slow_trickle_at_deadline():
    client, response, thread = _chunked_response(0.15)
    try:
        with pytest.raises(TimeoutError):
            read_response_body(response, max_bytes=100, timeout=0.03)
    finally:
        response.close()
        client.close()
        thread.join(timeout=1)
