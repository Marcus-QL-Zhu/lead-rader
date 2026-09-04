#!/usr/bin/env python3
"""Consume and authenticate the one-shot launcher capability."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import sys

try:
    from .exec_with_runtime_env import REQUIRED_RUNTIME_KEYS, validate_runtime_secret_values
    from .validate_runtime_env import UnsafeRuntimeEnvError, load_validated_runtime_env
except ImportError:  # direct script execution
    from exec_with_runtime_env import REQUIRED_RUNTIME_KEYS, validate_runtime_secret_values
    from validate_runtime_env import UnsafeRuntimeEnvError, load_validated_runtime_env


def main() -> int:
    try:
        fd_text = os.environ.pop("HT_LEAD_RUNTIME_CAPABILITY_FD")
        fd = int(fd_text)
        if fd < 3:
            raise ValueError
        with os.fdopen(fd, "rb", closefd=True) as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError
        payload = json.loads(raw)
        if set(payload) != {"app_root", "env_file", "nonce", "mac"}:
            raise ValueError
        app_root = Path(__file__).resolve().parent.parent
        if Path(payload["app_root"]).resolve() != app_root:
            raise ValueError
        values = load_validated_runtime_env(payload["env_file"])
        validate_runtime_secret_values(values)
        for key in REQUIRED_RUNTIME_KEYS:
            if not hmac.compare_digest(os.environ.get(key, ""), values[key]):
                raise ValueError
        body = {key: payload[key] for key in ("app_root", "env_file", "nonce")}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        expected = hmac.new(values["FEISHU_APP_SECRET"].encode(), canonical, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(payload["mac"], expected):
            raise ValueError
    except (KeyError, ValueError, OSError, json.JSONDecodeError, UnsafeRuntimeEnvError):
        print("daily launcher capability rejected", file=sys.stderr)
        return 64
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
