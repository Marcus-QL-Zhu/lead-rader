import hashlib
import hmac
import json
import os

from deployment import consume_runtime_capability as consumer


VALUES = {
    "FEISHU_APP_ID": "id",
    "FEISHU_APP_SECRET": "secret",
    "FEISHU_NOTIFY_RECEIVE_ID": "receiver",
    "METASO_API_KEY": "metaso",
}


def _payload(*, mac="valid"):
    body = {
        "app_root": str(consumer.Path(consumer.__file__).resolve().parent.parent),
        "env_file": "/protected/env",
        "nonce": "a" * 64,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body["mac"] = hmac.new(VALUES["FEISHU_APP_SECRET"].encode(), canonical, hashlib.sha256).hexdigest() if mac == "valid" else mac
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def _fd(payload: bytes) -> int:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, payload)
    os.close(write_fd)
    return read_fd


def _environment(monkeypatch, fd):
    monkeypatch.setenv("HT_LEAD_RUNTIME_CAPABILITY_FD", str(fd))
    for key, value in VALUES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(consumer, "load_validated_runtime_env", lambda _p: VALUES)


def test_real_capability_consumer_success_and_replay_rejected(monkeypatch):
    fd = _fd(_payload())
    _environment(monkeypatch, fd)
    assert consumer.main() == 0
    _environment(monkeypatch, fd)
    assert consumer.main() == 64


def test_capability_consumer_rejects_forgery_eof_and_direct_call(monkeypatch):
    for payload in (_payload(mac="0" * 64), b""):
        fd = _fd(payload)
        _environment(monkeypatch, fd)
        assert consumer.main() == 64
    monkeypatch.delenv("HT_LEAD_RUNTIME_CAPABILITY_FD", raising=False)
    assert consumer.main() == 64
