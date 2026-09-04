#!/usr/bin/env python3
"""Open and validate a protected runtime env without path-reopen races."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
try:  # POSIX-only production modules; tests import this module on Windows.
    import grp
    import pwd
except ImportError:  # pragma: no cover - production rejects this platform below
    grp = None  # type: ignore[assignment]
    pwd = None  # type: ignore[assignment]
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, Protocol, TextIO


class _StatLike(Protocol):
    st_mode: int
    st_uid: int
    st_gid: int
    st_dev: int
    st_ino: int
    st_nlink: int


class UnsafeRuntimeEnvError(ValueError):
    """The credential file does not meet the production filesystem contract."""


_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _service_identity(service_account: str = "admin") -> tuple[int, int]:
    if not all(hasattr(os, name) for name in ("geteuid", "getegid")):
        raise UnsafeRuntimeEnvError(
            "credential ownership validation is unavailable on this platform"
        )
    if pwd is None or grp is None:
        raise UnsafeRuntimeEnvError("required service identity is unavailable")
    try:
        account = pwd.getpwnam(service_account)
        group = grp.getgrnam(service_account)
    except KeyError as error:
        raise UnsafeRuntimeEnvError("required service identity is unavailable") from error
    if account.pw_gid != group.gr_gid:
        raise UnsafeRuntimeEnvError("service account primary group is not canonical")
    if os.geteuid() != account.pw_uid or os.getegid() != group.gr_gid:
        raise UnsafeRuntimeEnvError("runtime process is not the admin:admin service identity")
    return account.pw_uid, group.gr_gid


def validate_runtime_env_metadata(
    file_metadata: _StatLike,
    parent_metadata: _StatLike,
    *,
    expected_owner_uid: int,
    expected_owner_gid: int | None = None,
) -> None:
    """Validate file 0600 and parent secrets directory 0700 contracts."""

    if expected_owner_gid is None:
        expected_owner_gid = expected_owner_uid
    if stat.S_ISLNK(parent_metadata.st_mode):
        raise UnsafeRuntimeEnvError("secrets directory must not be a symlink")
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise UnsafeRuntimeEnvError("secrets parent must be a directory")
    if parent_metadata.st_uid != expected_owner_uid:
        raise UnsafeRuntimeEnvError("secrets directory owner does not match runtime user")
    if parent_metadata.st_gid != expected_owner_gid:
        raise UnsafeRuntimeEnvError("secrets directory group does not match service group")
    if stat.S_IMODE(parent_metadata.st_mode) != 0o700:
        raise UnsafeRuntimeEnvError("secrets directory permissions must be exactly 0700")
    if stat.S_ISLNK(file_metadata.st_mode):
        raise UnsafeRuntimeEnvError("credential file must not be a symlink")
    if not stat.S_ISREG(file_metadata.st_mode):
        raise UnsafeRuntimeEnvError("credential file must be a regular file")
    if file_metadata.st_nlink != 1:
        raise UnsafeRuntimeEnvError("credential file must have exactly one hard link")
    if file_metadata.st_uid != expected_owner_uid:
        raise UnsafeRuntimeEnvError("credential file owner does not match runtime user")
    if file_metadata.st_gid != expected_owner_gid:
        raise UnsafeRuntimeEnvError("credential file group does not match service group")
    if stat.S_IMODE(file_metadata.st_mode) != 0o600:
        raise UnsafeRuntimeEnvError("credential file permissions must be exactly 0600")


def _absolute_path(path: str | Path) -> Path:
    raw = str(path)
    value = Path(path)
    if not (value.is_absolute() or PurePosixPath(raw).is_absolute()):
        raise UnsafeRuntimeEnvError("credential file path must be absolute")
    if value.name in {"", ".", ".."}:
        raise UnsafeRuntimeEnvError("credential file name is invalid")
    return value


@contextmanager
def open_validated_runtime_env(
    path: str | Path,
    *,
    expected_owner_uid: int | None = None,
    expected_owner_gid: int | None = None,
    identity_getter: Callable[[], tuple[int, int]] = _service_identity,
) -> Iterator[TextIO]:
    """Yield the validated file opened through its verified parent directory.

    ``openat``-style ``dir_fd`` lookup plus ``O_NOFOLLOW`` and ``fstat`` means
    consumers parse the same inode that passed metadata validation. The shell
    must therefore use :mod:`exec_with_runtime_env` rather than validate a path
    and reopen it later.
    """

    env_path = _absolute_path(path)
    if (expected_owner_uid is None) != (expected_owner_gid is None):
        raise UnsafeRuntimeEnvError("owner UID and GID must be supplied together")
    if expected_owner_uid is None:
        owner_uid, owner_gid = identity_getter()
    else:
        owner_uid, owner_gid = expected_owner_uid, expected_owner_gid
    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
        raise UnsafeRuntimeEnvError(
            "secure descriptor-based credential opening is unavailable"
        )
    parent_path = env_path.parent
    parent_fd = -1
    file_fd = -1
    try:
        parent_before = os.lstat(parent_path)
        parent_fd = os.open(
            parent_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        parent_after = os.fstat(parent_fd)
        if (parent_before.st_dev, parent_before.st_ino) != (
            parent_after.st_dev,
            parent_after.st_ino,
        ):
            raise UnsafeRuntimeEnvError("secrets directory changed during validation")
        file_before = os.stat(env_path.name, dir_fd=parent_fd, follow_symlinks=False)
        file_fd = os.open(
            env_path.name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        file_after = os.fstat(file_fd)
        if (file_before.st_dev, file_before.st_ino) != (
            file_after.st_dev,
            file_after.st_ino,
        ):
            raise UnsafeRuntimeEnvError("credential file changed during validation")
        validate_runtime_env_metadata(
            file_after,
            parent_after,
            expected_owner_uid=owner_uid,
            expected_owner_gid=owner_gid,
        )
        stream = os.fdopen(file_fd, "r", encoding="utf-8", closefd=True)
        file_fd = -1
        with stream:
            yield stream
    except OSError as error:
        raise UnsafeRuntimeEnvError("credential boundary is not securely accessible") from error
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if parent_fd >= 0:
            os.close(parent_fd)


def load_validated_runtime_env(
    path: str | Path,
    *,
    expected_owner_uid: int | None = None,
    expected_owner_gid: int | None = None,
) -> dict[str, str]:
    """Parse simple dotenv values from the already-validated open descriptor."""

    values: dict[str, str] = {}
    with open_validated_runtime_env(
        path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    ) as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise UnsafeRuntimeEnvError(
                    f"credential file line {line_number} is not KEY=VALUE"
                )
            key, value = line.split("=", 1)
            key = key.strip()
            if not _ENV_KEY.fullmatch(key):
                raise UnsafeRuntimeEnvError(
                    f"credential file line {line_number} has an invalid key"
                )
            if key in values:
                raise UnsafeRuntimeEnvError(
                    f"credential file line {line_number} repeats a key"
                )
            parsed_value = value.strip()
            if (
                len(parsed_value) >= 2
                and parsed_value[0] == parsed_value[-1]
                and parsed_value[0] in {'"', "'"}
            ):
                parsed_value = parsed_value[1:-1]
            if "\x00" in parsed_value:
                raise UnsafeRuntimeEnvError(
                    f"credential file line {line_number} contains a null byte"
                )
            values[key] = parsed_value
    return values


def validate_runtime_env_file(
    path: str | Path,
    *,
    expected_owner_uid: int | None = None,
    expected_owner_gid: int | None = None,
) -> Path:
    with open_validated_runtime_env(
        path,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    ):
        pass
    return _absolute_path(path)


def main(
    argv: list[str] | None = None,
    *,
    expected_owner_uid: int | None = None,
    expected_owner_gid: int | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a protected 0700 directory and owned 0600 env file."
    )
    parser.add_argument("env_file", help="absolute path; contents are never printed")
    args = parser.parse_args(argv)
    try:
        validate_runtime_env_file(
            args.env_file,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        )
    except UnsafeRuntimeEnvError as error:
        print(f"unsafe runtime env file: {error}", file=sys.stderr)
        return 64
    print("runtime env file metadata validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
