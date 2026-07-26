import json

import pytest

from ht_lead_radar.openclaw_llm import (
    DirectLLMError,
    LLMConfigurationError,
    OpenClawConfiguredLLMRunner,
    OpenClawLLMConfig,
    load_openclaw_llm_config,
)


def write_openclaw_config(tmp_path, *, api="openai-completions"):
    config_path = tmp_path / "openclaw.json"
    models_path = tmp_path / "models.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "model": {"primary": "minimax/MiniMax-M2.7-highspeed"}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "minimax": {
                        "baseUrl": "https://api.minimaxi.com/v1",
                        "apiKey": "${MINIMAX_API_KEY}",
                        "api": api,
                        "models": [{"id": "MiniMax-M2.7-highspeed"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return config_path, models_path


def test_loads_primary_model_and_key_from_openclaw_configuration(tmp_path):
    config_path, models_path = write_openclaw_config(tmp_path)
    config = load_openclaw_llm_config(
        env={"MINIMAX_API_KEY": "test-secret"},
        config_path=config_path,
        models_path=models_path,
    )

    assert config.provider == "minimax"
    assert config.model == "MiniMax-M2.7-highspeed"
    assert config.base_url == "https://api.minimaxi.com/v1"
    assert config.api_key == "test-secret"
    assert "test-secret" not in repr(config)


def test_direct_runner_calls_provider_without_openclaw_agent():
    captured = {}

    def transport(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return {"choices": [{"message": {"content": '{"drafts":[]}'}}]}

    runner = OpenClawConfiguredLLMRunner(
        config=OpenClawLLMConfig(
            provider="minimax",
            model="MiniMax-M2.7-highspeed",
            base_url="https://api.minimaxi.com/v1",
            api_kind="openai-completions",
            api_key="test-secret",
        ),
        timeout_seconds=12,
        max_completion_tokens=2048,
        transport=transport,
    )

    assert (
        runner.run(
            "structured prompt",
            session_id="ignored",
            system_prompt="stable method",
        )
        == '{"drafts":[]}'
    )
    assert captured["url"] == "https://api.minimaxi.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-secret"
    assert captured["body"] == {
        "model": "MiniMax-M2.7-highspeed",
        "messages": [
            {"role": "system", "content": "stable method"},
            {"role": "user", "content": "structured prompt"},
        ],
        "stream": False,
        "temperature": 0.2,
        "max_completion_tokens": 2048,
        "reasoning_split": True,
    }
    assert captured["timeout"] == 12


def test_rejects_unsupported_provider_protocol(tmp_path):
    config_path, models_path = write_openclaw_config(
        tmp_path,
        api="anthropic-messages",
    )
    with pytest.raises(LLMConfigurationError, match="unsupported"):
        load_openclaw_llm_config(
            env={"MINIMAX_API_KEY": "test-secret"},
            config_path=config_path,
            models_path=models_path,
        )


def test_direct_runner_fails_closed_on_missing_assistant_text():
    runner = OpenClawConfiguredLLMRunner(
        config=OpenClawLLMConfig(
            provider="minimax",
            model="MiniMax-M2.7-highspeed",
            base_url="https://api.minimaxi.com/v1",
            api_kind="openai-completions",
            api_key="test-secret",
        ),
        transport=lambda request, timeout: {"choices": []},
    )
    with pytest.raises(DirectLLMError, match="no choices"):
        runner.run("prompt", session_id="ignored")
