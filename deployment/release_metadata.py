#!/usr/bin/env python3
"""Write or verify bounded exact-SHA deployment metadata as strict JSON."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys


CANONICAL_REPO = "https://github.com/Marcus-QL-Zhu/lead-rader.git"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReleaseMetadataError(ValueError):
    pass


def _single_link_regular(path: Path, *, maximum_bytes: int) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (os.name != "nt" and metadata.st_nlink != 1)
            or metadata.st_size > maximum_bytes
            or stat.S_IMODE(metadata.st_mode) & 0o111
        ):
            raise ReleaseMetadataError(
                "release metadata is not a bounded single-link file"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(maximum_bytes + 1)
        after = os.fstat(descriptor)
        if (
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ReleaseMetadataError("release metadata changed while being read")
        path_after = os.lstat(path)
        if os.name != "nt" and (after.st_dev, after.st_ino) != (
            path_after.st_dev,
            path_after.st_ino,
        ):
            raise ReleaseMetadataError("release metadata path changed during validation")
        if len(payload) > maximum_bytes:
            raise ReleaseMetadataError("release metadata exceeds its size bound")
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _strict_object(payload: bytes) -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, value in items:
            if key in output:
                raise ReleaseMetadataError("release manifest has duplicate keys")
            output[key] = value
        return output

    try:
        value = json.loads(payload.decode("utf-8", "strict"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseMetadataError("release manifest is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ReleaseMetadataError("release manifest root must be an object")
    return value


def verify_metadata(release: Path, *, sha: str, releases_dir: Path) -> str:
    if not SHA_RE.fullmatch(sha):
        raise ReleaseMetadataError("release SHA is invalid")
    marker = _single_link_regular(release / ".deployed_git_sha", maximum_bytes=64)
    if marker != f"{sha}\n".encode("ascii"):
        raise ReleaseMetadataError("release marker does not match exact SHA")
    manifest_bytes = _single_link_regular(
        release / ".release-manifest.json", maximum_bytes=4096
    )
    manifest = _strict_object(manifest_bytes)
    expected_keys = {"schema_version", "git_sha", "repository", "previous_release"}
    if set(manifest) != expected_keys:
        raise ReleaseMetadataError("release manifest keys are invalid")
    if (
        type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["git_sha"] != sha
        or manifest["repository"] != CANONICAL_REPO
        or not isinstance(manifest["previous_release"], str)
    ):
        raise ReleaseMetadataError("release manifest values are invalid")
    canonical = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if canonical != manifest_bytes:
        raise ReleaseMetadataError("release manifest is not canonical JSON")
    previous = str(manifest["previous_release"])
    if previous:
        try:
            candidate = Path(previous)
            relative = candidate.relative_to(releases_dir)
        except ValueError as error:
            raise ReleaseMetadataError("manifest previous release escaped root") from error
        if len(relative.parts) != 1 or not SHA_RE.fullmatch(relative.name):
            raise ReleaseMetadataError("manifest previous release is not an exact SHA path")
    return previous


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def verify_pointer(pointer: Path, *, releases_dir: Path) -> str:
    if not pointer.exists() and not pointer.is_symlink():
        return ""
    payload = _single_link_regular(pointer, maximum_bytes=4096)
    if not payload.endswith(b"\n") or payload.count(b"\n") != 1:
        raise ReleaseMetadataError("previous pointer is not one canonical line")
    try:
        value = payload[:-1].decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise ReleaseMetadataError("previous pointer is not UTF-8") from error
    if not value:
        return ""
    candidate = Path(value)
    try:
        relative = candidate.relative_to(releases_dir)
    except ValueError as error:
        raise ReleaseMetadataError("previous pointer escaped release root") from error
    if len(relative.parts) != 1 or not SHA_RE.fullmatch(relative.name):
        raise ReleaseMetadataError("previous pointer is not an exact SHA path")
    if candidate.is_symlink() or not candidate.is_dir() or candidate.resolve() != candidate:
        raise ReleaseMetadataError("previous pointer target is not a real release directory")
    return value


def write_metadata(
    *,
    marker: Path,
    manifest: Path,
    previous_file: Path,
    sha: str,
    previous_release: str,
) -> None:
    if not SHA_RE.fullmatch(sha):
        raise ReleaseMetadataError("release SHA is invalid")
    if previous_release:
        previous = Path(previous_release)
        if not previous.is_absolute() or not SHA_RE.fullmatch(previous.name):
            raise ReleaseMetadataError("previous release is not an exact-SHA path")
    document = {
        "schema_version": 1,
        "git_sha": sha,
        "repository": CANONICAL_REPO,
        "previous_release": previous_release,
    }
    created: list[Path] = []
    try:
        for path, payload in (
            (marker, f"{sha}\n".encode("ascii")),
            (
                manifest,
                (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
            ),
            (previous_file, f"{previous_release}\n".encode("utf-8")),
        ):
            _write_exclusive(path, payload)
            created.append(path)
    except Exception:
        for path in created:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--release-dir", required=True)
    verify.add_argument("--sha", required=True)
    verify.add_argument("--releases-dir", required=True)
    write = commands.add_parser("write")
    write.add_argument("--marker", required=True)
    write.add_argument("--manifest", required=True)
    write.add_argument("--previous-file", required=True)
    write.add_argument("--sha", required=True)
    write.add_argument("--previous-release", default="")
    pointer = commands.add_parser("verify-pointer")
    pointer.add_argument("--pointer", required=True)
    pointer.add_argument("--releases-dir", required=True)
    write_pointer = commands.add_parser("write-pointer")
    write_pointer.add_argument("--pointer", required=True)
    write_pointer.add_argument("--release", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            verify_metadata(
                Path(args.release_dir),
                sha=args.sha,
                releases_dir=Path(args.releases_dir),
            )
        elif args.command == "write":
            write_metadata(
                marker=Path(args.marker),
                manifest=Path(args.manifest),
                previous_file=Path(args.previous_file),
                sha=args.sha,
                previous_release=args.previous_release,
            )
        elif args.command == "verify-pointer":
            verify_pointer(Path(args.pointer), releases_dir=Path(args.releases_dir))
        else:
            release = Path(args.release)
            if not release.is_absolute() or not SHA_RE.fullmatch(release.name):
                raise ReleaseMetadataError("pointer release is not an exact SHA path")
            _write_exclusive(Path(args.pointer), f"{release}\n".encode("utf-8"))
    except (OSError, ReleaseMetadataError) as error:
        print(f"release metadata rejected: {error}", file=sys.stderr)
        return 74
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
