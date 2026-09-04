"""Small, dependency-free guards for public HTTP response bodies.

``urllib``'s ``timeout`` is a socket inactivity timeout. It is not a
deadline for consuming a chunked response: a peer can keep sending tiny
chunks and make ``HTTPResponse.read()`` wait indefinitely. The collectors
use this module to impose both a response-size limit and a wall-clock read
deadline while retaining the normal ``urllib`` transport.
"""

from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic
from typing import Any, Callable


class DaemonWorkerPool:
    """A tiny executor that never registers an interpreter-exit join.

    ``ThreadPoolExecutor`` workers are joined by ``concurrent.futures`` during
    interpreter shutdown, even after ``shutdown(wait=False)``.  That defeats a
    watchdog when a third-party ``read`` or injected transport ignores its
    timeout.  These bounded daemon workers deliberately trade abandonment of a
    wedged operation for a truthful wall-clock boundary.
    """

    _STOP = object()

    def __init__(self, max_workers: int, *, name: str = "deadline-worker") -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self._queue: Queue[object] = Queue()
        self._stopped = Event()
        self._threads = [
            Thread(target=self._work, name=f"{name}-{index + 1}", daemon=True)
            for index in range(max_workers)
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, operation: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        if self._stopped.is_set():
            raise RuntimeError("daemon worker pool is shut down")
        future: Future = Future()
        self._queue.put((future, operation, args, kwargs))
        return future

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is self._STOP:
                return
            future, operation, args, kwargs = item  # type: ignore[misc]
            if self._stopped.is_set():
                future.cancel()
                continue
            if not future.set_running_or_notify_cancel():
                continue
            try:
                result = operation(*args, **kwargs)
            except BaseException as error:
                future.set_exception(error)
            else:
                future.set_result(result)

    def shutdown(self, *, cancel_futures: bool = True) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        if cancel_futures:
            while True:
                try:
                    item = self._queue.get_nowait()
                except Empty:
                    break
                if item is not self._STOP:
                    future = item[0]  # type: ignore[index]
                    future.cancel()
        for _thread in self._threads:
            self._queue.put(self._STOP)

    def __enter__(self) -> "DaemonWorkerPool":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> bool:
        self.shutdown(cancel_futures=True)
        return False


def call_with_wallclock(
    operation: Callable[..., Any],
    deadline_seconds: float,
    /,
    *args: Any,
    timeout_message: str = "operation exceeded wall-clock deadline",
    worker_name: str = "deadline-call",
    **kwargs: Any,
) -> Any:
    """Run one potentially non-cooperative call behind a daemon deadline.

    Socket timeouts do not cover every DNS resolver and injected transports are
    free to ignore their ``timeout`` argument.  Callers therefore need a real
    wall-clock boundary outside the transport.  A timed-out worker is
    deliberately abandoned as a daemon so it cannot hold the daily process (or
    interpreter shutdown) open.
    """

    if deadline_seconds <= 0:
        raise TimeoutError(timeout_message)
    with DaemonWorkerPool(1, name=worker_name) as workers:
        future = workers.submit(operation, *args, **kwargs)
        try:
            return future.result(timeout=deadline_seconds)
        except FutureTimeoutError as error:
            future.cancel()
            raise TimeoutError(timeout_message) from error


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
    chunk_size: int = 256 * 1024,
) -> bytes:
    """Read a response under size and wall-clock budgets.

    Every incremental ``read`` runs behind a daemon boundary.  This matters for
    custom/non-socket transports and broken wrappers that ignore socket timeout:
    a 20ms remaining budget expires in about 20ms, rather than waiting for the
    blocking method to cooperate or for interpreter shutdown to join it.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")

    deadline = monotonic() + timeout
    if _response_socket(response) is None:
        # Test doubles and third-party wrappers often have no cursor/EOF
        # contract: ``read(n)`` returns the same prefix on every call. Keep the
        # one-shot compatibility path, but put even that non-cooperative read
        # behind the exact daemon deadline rather than checking only after it
        # eventually returns.
        with DaemonWorkerPool(1, name="http-body-read") as workers:
            future = workers.submit(response.read, max_bytes + 1)
            try:
                piece = future.result(timeout=timeout)
            except FutureTimeoutError as error:
                future.cancel()
                raise TimeoutError("HTTP response read exceeded deadline") from error
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
    with DaemonWorkerPool(1, name="http-body-read") as workers:
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("HTTP response read exceeded deadline")
            _set_response_timeout(response, remaining)
            requested = min(chunk_size, max_bytes - total + 1)
            future = workers.submit(response.read, requested)
            try:
                piece = future.result(timeout=remaining)
            except FutureTimeoutError as error:
                future.cancel()
                raise TimeoutError("HTTP response read exceeded deadline") from error
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
