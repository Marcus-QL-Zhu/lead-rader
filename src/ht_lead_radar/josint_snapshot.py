"""Fail-closed, metadata-preserving snapshots of a SQLite database family."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from typing import Iterator


class _FamilyChanged(RuntimeError):
    pass


class JOSINTSnapshotUnstable(ValueError):
    """The live SQLite family changed throughout all bounded retries."""


def _metadata(value: os.stat_result) -> tuple[int, ...]:
    common = (
        value.st_dev, value.st_ino, value.st_nlink, value.st_mode,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
    )
    if os.name == "posix":
        return common + (value.st_ctime_ns, value.st_atime_ns)
    return common


def _open_verified(path: Path) -> tuple[int, os.stat_result]:
    before = path.lstat()
    # Windows ``os.open`` otherwise uses text mode: CRLF translation and a
    # byte 0x1a inside a SQLite WAL can truncate/corrupt the supposedly exact
    # family copy.  O_BINARY is a no-op/absent on POSIX.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    if os.name == "posix":
        if not hasattr(os, "O_NOATIME") or not hasattr(os, "O_NOFOLLOW"):
            raise ValueError("safe no-atime SQLite snapshot is unavailable")
        flags |= os.O_NOATIME
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as error:
        raise _FamilyChanged("JOSINT family member disappeared during open") from error
    after = os.fstat(fd)
    if not stat.S_ISREG(after.st_mode) or after.st_nlink != 1:
        os.close(fd)
        raise ValueError("JOSINT family member is not a single-link regular file")
    if _metadata(before) != _metadata(after):
        os.close(fd)
        raise _FamilyChanged("JOSINT family member changed during open")
    return fd, after


def _read_verified(path: Path, target: Path | None = None) -> tuple[tuple[int, ...], str]:
    fd, before = _open_verified(path)
    digest = hashlib.sha256()
    output = None
    try:
        if target is not None:
            output = target.open("xb")
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
            if output is not None:
                output.write(chunk)
        if output is not None:
            output.flush()
            os.fsync(output.fileno())
        after = os.fstat(fd)
        try:
            path_after = path.lstat()
        except FileNotFoundError as error:
            raise _FamilyChanged(
                "JOSINT family member disappeared while reading"
            ) from error
        if _metadata(before) != _metadata(after) or _metadata(after) != _metadata(path_after):
            raise _FamilyChanged("JOSINT family member changed while reading")
        return _metadata(after), digest.hexdigest()
    finally:
        if output is not None:
            output.close()
        os.close(fd)


def sqlite_family_fingerprint(db_path: str | Path) -> dict[str, object]:
    source = Path(db_path)
    if not source.is_absolute():
        source = source.absolute()
    if stat.S_ISLNK(source.lstat().st_mode):
        raise ValueError("JOSINT database must not be a symlink")
    output: dict[str, object] = {}
    for suffix in ("", "-wal", "-shm"):
        member = Path(f"{source}{suffix}")
        try:
            output[suffix] = _read_verified(member)
        except FileNotFoundError:
            output[suffix] = None
    return output


def _snapshot_once(source: Path, target: Path) -> None:
    initial = sqlite_family_fingerprint(source)
    for suffix in ("", "-wal", "-shm"):
        if initial[suffix] is not None:
            try:
                _read_verified(
                    Path(f"{source}{suffix}"),
                    Path(f"{target}{suffix}"),
                )
            except FileNotFoundError as error:
                raise _FamilyChanged(
                    "JOSINT family member disappeared after fingerprint"
                ) from error
    if sqlite_family_fingerprint(source) != initial:
        raise _FamilyChanged("JOSINT database family changed during snapshot")


def _materialize_committed_view(copied: Path, target: Path) -> None:
    """Recover a copied WAL family and backup its committed view.

    SQLite read-only mode is allowed to open a WAL database only when a valid
    shared-memory index already exists.  A live JOSINT database can legitimately
    have a committed ``-wal`` without ``-shm``; opening that copied family
    directly with ``mode=ro`` can therefore miss frames or fail depending on
    timing and SQLite build options.  All recovery happens on disposable copies,
    then the backup API produces a standalone snapshot for query-only readers.
    """

    # The WAL index is derived state.  Never trust a copied, concurrently used
    # mmap image; let SQLite reconstruct it beside the disposable copy.
    Path(f"{copied}-shm").unlink(missing_ok=True)
    recovered = sqlite3.connect(copied, timeout=30)
    destination: sqlite3.Connection | None = None
    try:
        recovered.execute("PRAGMA busy_timeout=30000")
        # Force schema/WAL recovery before backup. The backup then reads a
        # single committed snapshot including every committed WAL frame.
        recovered.execute(
            "SELECT name FROM sqlite_master ORDER BY name LIMIT 1"
        ).fetchall()
        destination = sqlite3.connect(target, timeout=30)
        recovered.backup(destination)
        destination.commit()
    finally:
        if destination is not None:
            destination.close()
        recovered.close()


@contextmanager
def readonly_sqlite_snapshot(db_path: str | Path) -> Iterator[Path]:
    source = Path(db_path)
    if not source.is_absolute():
        source = source.absolute()
    if stat.S_ISLNK(source.lstat().st_mode):
        raise ValueError("JOSINT database must not be a symlink")
    last_error: Exception | None = None
    for _attempt in range(3):
        with tempfile.TemporaryDirectory(prefix="lead-radar-josint-") as raw_tmp:
            copied = Path(raw_tmp) / f"copied-{source.name}"
            target = Path(raw_tmp) / f"snapshot-{source.name}"
            try:
                _snapshot_once(source, copied)
            except _FamilyChanged as error:
                last_error = error
                continue
            _materialize_committed_view(copied, target)
            yield target
            return
    raise JOSINTSnapshotUnstable(
        "JOSINT family did not stabilize after bounded retries"
    ) from last_error
