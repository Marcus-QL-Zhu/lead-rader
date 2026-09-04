import json
import pytest
from deployment import exec_with_runtime_env

REQUIRED = {"FEISHU_APP_ID": "id", "FEISHU_APP_SECRET": "secret", "FEISHU_NOTIFY_RECEIVE_ID": "receiver", "METASO_API_KEY": "metaso"}

def test_exec_wrapper_builds_minimal_environment(monkeypatch):
    observed = {}
    for key in ("LD_PRELOAD", "PYTHONPATH", "HT_LEAD_APP_DIR", "OPENCLAW_CONFIG_PATH"):
        monkeypatch.setenv(key, "evil")
    monkeypatch.setattr(exec_with_runtime_env, "load_validated_runtime_env", lambda _p: REQUIRED)
    monkeypatch.setattr(exec_with_runtime_env.os, "pipe", lambda: (40, 41))
    monkeypatch.setattr(exec_with_runtime_env.os, "set_inheritable", lambda *_a: None)
    monkeypatch.setattr(exec_with_runtime_env.os, "write", lambda _fd, data: len(data))
    monkeypatch.setattr(exec_with_runtime_env.os, "close", lambda _fd: None)
    def fake_exec(program, command, environment):
        observed.update(program=program, command=command, environment=environment)
        raise RuntimeError("intercepted")
    monkeypatch.setattr(exec_with_runtime_env.os, "execve", fake_exec)
    with pytest.raises(RuntimeError, match="intercepted"):
        exec_with_runtime_env.main(["--env-file", "/protected/env"])
    assert observed["program"] == "/bin/sh"
    assert observed["environment"]["PATH"] == "/usr/bin:/bin"
    assert observed["environment"]["HT_LEAD_RUNTIME_CAPABILITY_FD"] == "40"
    assert not ({"LD_PRELOAD", "PYTHONPATH", "HT_LEAD_APP_DIR", "OPENCLAW_CONFIG_PATH", "MINIMAX_API_KEY"} & set(observed["environment"]))

@pytest.mark.parametrize("key", ["PATH", "PYTHONPATH", "HT_LEAD_APP_DIR", "LD_PRELOAD", "GIT_DIR", "HTTPS_PROXY"])
def test_exec_wrapper_rejects_control_keys(monkeypatch, key):
    monkeypatch.setattr(exec_with_runtime_env, "load_validated_runtime_env", lambda _p: {**REQUIRED, key: "evil"})
    assert exec_with_runtime_env.main(["--env-file", "/protected/env"]) == 64

def test_capability_is_authenticated_and_fresh():
    first = json.loads(exec_with_runtime_env._capability(REQUIRED, exec_with_runtime_env.Path("/app"), exec_with_runtime_env.Path("/env")))
    second = json.loads(exec_with_runtime_env._capability(REQUIRED, exec_with_runtime_env.Path("/app"), exec_with_runtime_env.Path("/env")))
    assert len(first["mac"]) == 64
    assert first["nonce"] != second["nonce"]
