"""Small, dependency-free guards for public HTTP response bodies.

``urllib``'s ``timeout`` is a socket inactivity timeout. It is not a
deadline for consuming a chunked response: a peer can keep sending tiny
chunks and make ``HTTPResponse.read()`` wait indefinitely. The collectors
use this module to impose both a response-size limit and a wall-clock read
deadline while retaining the normal ``urllib`` transport.
"""

from __future__ import annotations

from time import monotonic
from typing import Any


def _response_socket(response: Any) -> Any | None:
    """Best-effort access to urllib/http.client's underlying socket."""
    candidates = [getattr(response, "_sock", None)]
    fp = getattr(response, "fp", None)
    candidates.append(fp)
    raw = getattr(fp, "raw", None)
    candidates.append(raw)
    candidates.append(getattr(raw, "_sock", None))
    for candidate in candidates:
        if candidate is not None and callable(getattr(candidate, "settimeout", None)):
            return candidate
    return None


def _set_response_timeout(response: Any, seconds: float) -> None:
    sock = _response_socket(response)
    if sock is not None:
        # Keep a positive value even when the deadline is very close. A zero
        # socket timeout means non-blocking mode, which creates a different
        # failure mode for http.client.
        sock.settimeout(max(0.001, seconds))


def read_response_body(
    response: Any,
    *,
    max_bytes: int,
    timeout: float,
    chunk_size: int = 64 * 1024,
) -> bytes:
    """Read a response under size and wall-clock budgets.

    The hard deadline applies to urllib responses with an accessible socket;
    custom non-socket transports are checked when their ``read`` returns.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")

    deadline = monotonic() + timeout
    # A custom/non-socket transport commonly returns its complete body from a
    # single read and has no EOF-aware streaming contract. Keep compatibility
    # with those transports while applying the same post-read deadline/size
    # checks. Real urllib responses take the socket-aware streaming path below.
    if _response_socket(response) is None:
        piece = response.read(max_bytes + 1)
        if deadline - monotonic() <= 0:
            raise TimeoutError("HTTP response read exceeded deadline")
        if not isinstance(piece, (bytes, bytearray, memoryview)):
            raise TypeError("HTTP response body must be bytes")
        body = bytes(piece)
        if len(body) > max_bytes:
            raise ValueError(f"response exceeds {max_bytes} bytes")
        return body

    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("HTTP response read exceeded deadline")
        _set_response_timeout(response, remaining)
        requested = min(chunk_size, max_bytes - total + 1)
        piece = response.read(requested)
        if deadline - monotonic() <= 0:
            raise TimeoutError("HTTP response read exceeded deadline")
        if not piece:
            break
        if not isinstance(piece, (bytes, bytearray, memoryview)):
            raise TypeError("HTTP response body must be bytes")
        data = bytes(piece)
        chunks.append(data)
        total += len(data)
        if total > max_bytes:
            raise ValueError(f"response exceeds {max_bytes} bytes")
    return b"".join(chunks)
