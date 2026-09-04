#!/usr/bin/env python3
"""Validate credentials, then exec the one fixed daily launcher with a capability."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys

try:
    from .validate_runtime_env import UnsafeRuntimeEnvError, load_validated_runtime_env
except ImportError:  # direct script execution
    from validate_runtime_env import UnsafeRuntimeEnvError, load_validated_runtime_env

REQUIRED_RUNTIME_KEYS = frozenset({
    "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_NOTIFY_RECEIVE_ID", "METASO_API_KEY",
})
OPTIONAL_RUNTIME_KEYS = frozenset({"FEISHU_NOTIFY_RECEIVE_ID_TYPE"})
ALLOWED_RUNTIME_KEYS = REQUIRED_RUNTIME_KEYS | OPTIONAL_RUNTIME_KEYS
SAFE_PATH = "/usr/bin:/bin"
PYTHON_BIN = "/home/admin/.pyenv/versions/3.11.14/bin/python3"


def validate_runtime_secret_values(values: dict[str, str]) -> None:
    unknown = set(values) - ALLOWED_RUNTIME_KEYS
    missing = REQUIRED_RUNTIME_KEYS - set(values)
    empty = {key for key in REQUIRED_RUNTIME_KEYS if not values.get(key, "").strip()}
    if unknown:
        raise UnsafeRuntimeEnvError("runtime env contains process-control or unknown keys")
    if missing or empty:
        names = ", ".join(sorted(missing | empty))
        raise UnsafeRuntimeEnvError(f"runtime env is missing required keys: {names}")


def _capability(values: dict[str, str], app_root: Path, env_file: Path) -> bytes:
    body = {
        "app_root": str(app_root),
        "env_file": str(env_file),
        "nonce": secrets.token_hex(32),
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body["mac"] = hmac.new(
        values["FEISHU_APP_SECRET"].encode(), canonical, hashlib.sha256
    ).hexdigest()
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args(argv)
    app_root = Path(__file__).resolve().parent.parent
    env_file = Path(args.env_file)
    try:
        values = load_validated_runtime_env(env_file)
        validate_runtime_secret_values(values)
    except UnsafeRuntimeEnvError as error:
        print(f"runtime credential boundary rejected: {error}", file=sys.stderr)
        return 64
    read_fd, write_fd = os.pipe()
    try:
        os.set_inheritable(read_fd, True)
        os.write(write_fd, _capability(values, app_root, env_file))
    finally:
        os.close(write_fd)
    child = {
        "PATH": SAFE_PATH,
        "HOME": "/home/admin",
        "USER": "admin",
        "LOGNAME": "admin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "Asia/Shanghai",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HT_LEAD_RUNTIME_CAPABILITY_FD": str(read_fd),
        **values,
    }
    os.execve(
        "/bin/sh",
        ["/bin/sh", str(app_root / "scripts" / "run_daily_fixed_sources_inner.sh")],
        child,
    )
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
