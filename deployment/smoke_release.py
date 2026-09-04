#!/usr/bin/env python3
"""Read-only pre/post activation smoke checks for an exact release."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Callable

_SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))
from ht_lead_radar.josint_snapshot import (  # noqa: E402
    readonly_sqlite_snapshot,
    sqlite_family_fingerprint,
)

try:  # module import in tests versus direct script execution in production
    from .exec_with_runtime_env import REQUIRED_RUNTIME_KEYS, validate_runtime_secret_values
    from .validate_runtime_env import load_validated_runtime_env
except ImportError:  # pragma: no cover - exercised by the deployment shell
    from exec_with_runtime_env import REQUIRED_RUNTIME_KEYS, validate_runtime_secret_values
    from validate_runtime_env import load_validated_runtime_env


class ReleaseSmokeError(ValueError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _josint_schema_smoke(path: Path) -> str:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ReleaseSmokeError("JOSINT database must be an absolute regular file")
    try:
        connection = sqlite3.connect(f"{path.resolve(strict=True).as_uri()}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        connection.row_factory = sqlite3.Row
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "canonical_jobs" in tables:
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(canonical_jobs)")
            }
            required = {
                "canonical_job_id",
                "title",
                "company_name",
                "guessed_employer",
                "location",
                "jd_text",
                "industry_label",
                "function_label",
                "target_reason",
                "first_seen_at",
                "last_seen_at",
                "source_urls_json",
                "is_target_job",
            }
            if not required.issubset(columns):
                raise ReleaseSmokeError("JOSINT canonical schema is incomplete")
            return "canonical_jobs"
        if "jobs" not in tables:
            raise ReleaseSmokeError("JOSINT has neither canonical_jobs nor jobs")
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(jobs)")
        }
        if not ({"title", "raw_title"} & columns):
            raise ReleaseSmokeError("JOSINT legacy jobs schema has no title field")
        return "jobs"
    except sqlite3.Error as error:
        raise ReleaseSmokeError("JOSINT database/schema smoke failed") from error
    finally:
        if "connection" in locals():
            connection.close()


def _josint_adapter_smoke(
    release: Path,
    database: Path,
    *,
    runner: Runner = subprocess.run,
) -> None:
    """Exercise the selected release's real JOSINT adapter, read-only."""

    source_root = release / "src"
    code = """
import sys
from pathlib import Path

source_root = Path(sys.argv[1]).resolve(strict=True)
sys.path.insert(0, str(source_root))
import ht_lead_radar
package_file = Path(ht_lead_radar.__file__).resolve(strict=True)
if source_root not in package_file.parents:
    raise SystemExit(21)
from ht_lead_radar.collectors import collect_josint
rows = collect_josint(
    Path(sys.argv[2]),
    "deployment-smoke",
    topics=("deployment-smoke",),
)
if not isinstance(rows, list):
    raise SystemExit(22)
""".strip()
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = runner(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                code,
                str(source_root),
                str(database),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(release),
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseSmokeError(
            "selected release JOSINT adapter smoke could not execute"
        ) from error
    if completed.returncode != 0:
        # Child stdout/stderr may contain data from a malformed runtime and is
        # intentionally not copied into deployment logs.
        raise ReleaseSmokeError("selected release JOSINT adapter smoke failed")


def smoke_release(
    release_dir: str | Path,
    *,
    josint_db: str | Path,
    env_file: str | Path,
    expected_realpath: str | Path | None = None,
    expected_owner_uid: int | None = None,
    expected_owner_gid: int | None = None,
) -> dict[str, str]:
    if sys.version_info < (3, 10):
        raise ReleaseSmokeError("Python 3.10 or newer is required")
    release = Path(release_dir)
    try:
        resolved = release.resolve(strict=True)
    except OSError as error:
        raise ReleaseSmokeError("release path is unavailable") from error
    if expected_realpath is not None:
        expected = Path(expected_realpath).resolve(strict=True)
        if resolved != expected:
            raise ReleaseSmokeError("live release pointer does not match expected release")
    package = resolved / "src" / "ht_lead_radar" / "__init__.py"
    if not package.is_file():
        raise ReleaseSmokeError("release package import root is missing")
    runtime_environment = load_validated_runtime_env(
        env_file,
        expected_owner_uid=expected_owner_uid,
        expected_owner_gid=expected_owner_gid,
    )
    try:
        validate_runtime_secret_values(runtime_environment)
    except ValueError as error:
        raise ReleaseSmokeError(str(error)) from error
    missing_keys = sorted(
        key for key in REQUIRED_RUNTIME_KEYS if not runtime_environment.get(key, "").strip()
    )
    if missing_keys:
        raise ReleaseSmokeError(
            "runtime env is missing required keys: " + ", ".join(missing_keys)
        )
    database = Path(josint_db)
    before = sqlite_family_fingerprint(database)
    try:
        with readonly_sqlite_snapshot(database) as snapshot:
            schema = _josint_schema_smoke(snapshot)
        _josint_adapter_smoke(resolved, database)
    finally:
        after = sqlite_family_fingerprint(database)
        if after != before:
            raise ReleaseSmokeError("JOSINT adapter smoke modified DB/WAL/SHM state")
    return {
        "release": str(resolved),
        "josint_schema": schema,
        "josint_adapter": "passed",
    }


def main(
    argv: list[str] | None = None,
    *,
    expected_owner_uid: int | None = None,
    expected_owner_gid: int | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", required=True)
    parser.add_argument("--expected-realpath")
    parser.add_argument("--josint-db", required=True)
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args(argv)
    try:
        result = smoke_release(
            args.release_dir,
            josint_db=args.josint_db,
            env_file=args.env_file,
            expected_realpath=args.expected_realpath,
            expected_owner_uid=expected_owner_uid,
            expected_owner_gid=expected_owner_gid,
        )
    except (ReleaseSmokeError, ValueError) as error:
        print(f"release smoke failed: {error}", file=sys.stderr)
        return 74
    print(
        f"release smoke passed: schema={result['josint_schema']} "
        f"pid={os.getpid()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
