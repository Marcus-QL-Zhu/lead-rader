#!/usr/bin/env python3
"""Verify a release against its commit tree, never against its mutable index."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha1
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUNTIME_NAMES = frozenset({"data", "logs", "backups", "reports-daily"})
METADATA_NAMES = frozenset({".deployed_git_sha", ".release-manifest.json"})
SUPPORTED_MODES = frozenset({"100644", "100755", "120000"})


class ReleaseTreeError(ValueError):
    """The checkout differs from the immutable Git commit universe."""


@dataclass(frozen=True)
class _TreeEntry:
    mode: str
    oid: str
    path: str


def _git_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith("GIT_"):
            environment.pop(key, None)
    environment.update(
        {
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ALLOW_PROTOCOL": "https",
        }
    )
    return environment


def _git(release: Path, *arguments: str, index: bool = False) -> bytes:
    command = [
        "git",
        "--no-replace-objects",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "protocol.ext.allow=never",
        "-c",
        "http.followRedirects=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        f"--git-dir={release / '.git'}",
    ]
    if index:
        # Explicitly override a malicious core.worktree. The index is inspected
        # only for forbidden flags; it is never the content authority.
        command.append(f"--work-tree={release}")
    command.extend(arguments)
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            env=_git_environment(),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseTreeError("release Git verification could not execute") from error
    if completed.returncode != 0:
        raise ReleaseTreeError("release Git object verification failed")
    return completed.stdout


def _validate_index_flags(release: Path) -> None:
    verbose = _git(release, "ls-files", "-v", "-z", index=True)
    for record in verbose.split(b"\0"):
        if record and 97 <= record[0] <= 122:
            raise ReleaseTreeError("release index contains assume-unchanged entries")
    tagged = _git(release, "ls-files", "-t", "-z", index=True)
    for record in tagged.split(b"\0"):
        if record.startswith(b"S "):
            raise ReleaseTreeError("release index contains skip-worktree entries")


def _commit_tree(release: Path, sha: str) -> dict[str, _TreeEntry]:
    resolved = _git(release, "rev-parse", "--verify", f"{sha}^{{commit}}")
    if resolved.decode("ascii", "replace").strip() != sha:
        raise ReleaseTreeError("release HEAD commit is unavailable")
    head = _git(release, "rev-parse", "HEAD")
    if head.decode("ascii", "replace").strip() != sha:
        raise ReleaseTreeError("release HEAD does not match requested SHA")
    raw = _git(release, "ls-tree", "-r", "-z", "--full-tree", sha)
    entries: dict[str, _TreeEntry] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            raw_mode, raw_type, raw_oid = metadata.split(b" ", 2)
            mode = raw_mode.decode("ascii", "strict")
            object_type = raw_type.decode("ascii", "strict")
            oid = raw_oid.decode("ascii", "strict")
            path = raw_path.decode("utf-8", "surrogateescape")
        except (ValueError, UnicodeDecodeError) as error:
            raise ReleaseTreeError("release commit tree is invalid") from error
        pure = PurePosixPath(path)
        if object_type != "blob" or mode == "160000" or mode not in SUPPORTED_MODES:
            raise ReleaseTreeError("release submodules or unsupported objects are forbidden")
        if not SHA_RE.fullmatch(oid):
            raise ReleaseTreeError("release commit contains an invalid blob id")
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ReleaseTreeError("release commit contains an unsafe path")
        if pure.parts[0] in RUNTIME_NAMES:
            raise ReleaseTreeError("runtime paths must not be tracked")
        if pure.parts[0] in METADATA_NAMES:
            raise ReleaseTreeError("deployment metadata must not be tracked")
        if path in entries:
            raise ReleaseTreeError("release commit contains duplicate paths")
        entries[path] = _TreeEntry(mode=mode, oid=oid, path=path)
    return entries


def _blob_oid(payload: bytes) -> str:
    return sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()


def _read_regular_same_inode(path: Path, before: os.stat_result) -> tuple[os.stat_result, bytes]:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        after = os.fstat(descriptor)
        if os.name != "nt" and (before.st_dev, before.st_ino) != (
            after.st_dev,
            after.st_ino,
        ):
            raise ReleaseTreeError("release file changed during verification")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read()
        after_read = os.fstat(descriptor)
        if (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != (
            after_read.st_size,
            after_read.st_mtime_ns,
            after_read.st_ctime_ns,
        ):
            raise ReleaseTreeError("release file changed while being hashed")
        path_after = os.lstat(path)
        if os.name != "nt" and (after_read.st_dev, after_read.st_ino) != (
            path_after.st_dev,
            path_after.st_ino,
        ):
            raise ReleaseTreeError("release file changed after verification")
        return after_read, payload
    except OSError as error:
        raise ReleaseTreeError("release file could not be opened safely") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _verify_tracked(path: Path, metadata: os.stat_result, entry: _TreeEntry) -> None:
    if entry.mode == "120000":
        if not stat.S_ISLNK(metadata.st_mode):
            raise ReleaseTreeError("tracked symlink type differs from commit")
        payload = os.fsencode(os.readlink(path))
    else:
        verified, payload = _read_regular_same_inode(path, metadata)
        if not stat.S_ISREG(verified.st_mode):
            raise ReleaseTreeError("tracked regular-file type differs from commit")
        if os.name != "nt" and verified.st_nlink != 1:
            raise ReleaseTreeError("release regular files must have exactly one hard link")
        executable = bool(stat.S_IMODE(verified.st_mode) & 0o111)
        if executable != (entry.mode == "100755"):
            raise ReleaseTreeError("tracked executable mode differs from commit")
    if _blob_oid(payload) != entry.oid:
        raise ReleaseTreeError("release tracked files differ from requested SHA")


def verify_release_tree(
    release_dir: str | Path,
    *,
    sha: str,
    runtime_dir: str | Path,
) -> None:
    if not SHA_RE.fullmatch(sha):
        raise ReleaseTreeError("requested SHA is invalid")
    release = Path(release_dir)
    runtime = Path(runtime_dir)
    if not release.is_absolute() or not runtime.is_absolute():
        raise ReleaseTreeError("release/runtime paths must be absolute")
    if release.is_symlink() or runtime.is_symlink():
        raise ReleaseTreeError("release/runtime roots must not be symlinks")
    try:
        release = release.resolve(strict=True)
        runtime = runtime.resolve(strict=True)
    except OSError as error:
        raise ReleaseTreeError("release/runtime path is unavailable") from error
    git_dir = release / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        raise ReleaseTreeError("release .git must be a real directory")

    tracked = _commit_tree(release, sha)
    _validate_index_flags(release)
    tracked_prefixes = {
        "/".join(PurePosixPath(path).parts[:index])
        for path in tracked
        for index in range(1, len(PurePosixPath(path).parts))
    }
    observed_tracked: set[str] = set()
    pending = [release]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise ReleaseTreeError("release tree cannot be enumerated") from error
        for item in entries:
            path = Path(item.path)
            relative = path.relative_to(release).as_posix()
            if relative == ".git":
                continue
            metadata = item.stat(follow_symlinks=False)
            tracked_entry = tracked.get(relative)
            if tracked_entry is not None:
                _verify_tracked(path, metadata, tracked_entry)
                observed_tracked.add(relative)
                continue
            if relative in RUNTIME_NAMES:
                if not stat.S_ISLNK(metadata.st_mode):
                    raise ReleaseTreeError("runtime release entries must be symlinks")
                try:
                    target = path.resolve(strict=True)
                except OSError as error:
                    raise ReleaseTreeError("runtime release symlink is broken") from error
                if target != runtime / relative:
                    raise ReleaseTreeError("runtime release symlink target is invalid")
                continue
            if relative in METADATA_NAMES:
                verified, _payload = _read_regular_same_inode(path, metadata)
                if not stat.S_ISREG(verified.st_mode) or (
                    os.name != "nt" and verified.st_nlink != 1
                ):
                    raise ReleaseTreeError(
                        "release metadata must be single-link regular files"
                    )
                if stat.S_IMODE(verified.st_mode) & 0o111:
                    raise ReleaseTreeError("release metadata must not be executable")
                continue
            if stat.S_ISDIR(metadata.st_mode) and relative in tracked_prefixes:
                pending.append(path)
                continue
            raise ReleaseTreeError("release contains unexpected untracked content")
    if observed_tracked != set(tracked):
        raise ReleaseTreeError("release tracked manifest is incomplete")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--runtime-dir", required=True)
    args = parser.parse_args(argv)
    try:
        verify_release_tree(args.release_dir, sha=args.sha, runtime_dir=args.runtime_dir)
    except ReleaseTreeError as error:
        print(f"release tree rejected: {error}", file=sys.stderr)
        return 74
    print("release commit tree verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
