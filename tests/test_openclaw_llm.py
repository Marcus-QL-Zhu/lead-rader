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
                    "defaults": {"model": {"primary": "minimax/MiniMax-M2.7-highspeed"}}
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
        "temperature": 0.0,
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


def test_direct_runner_retries_one_transient_empty_assistant_response():
    bodies = []
    responses = iter(
        [
            {"choices": [{"message": {"content": ""}}]},
            {"choices": [{"message": {"content": '{"drafts":[]}'}}]},
        ]
    )

    def transport(request, timeout):
        del timeout
        bodies.append(json.loads(request.data.decode("utf-8")))
        return next(responses)

    runner = OpenClawConfiguredLLMRunner(
        config=OpenClawLLMConfig(
            provider="minimax",
            model="MiniMax-M3",
            base_url="https://api.minimaxi.com/v1",
            api_kind="openai-completions",
            api_key="test-secret",
        ),
        transport=transport,
    )

    assert runner.run("prompt", session_id="ignored") == '{"drafts":[]}'
    assert [body["reasoning_split"] for body in bodies] == [True, False]


def test_direct_runner_can_disable_m3_thinking_for_bounded_extraction():
    captured = {}

    def transport(request, timeout):
        del timeout
        captured.update(json.loads(request.data.decode("utf-8")))
        return {"choices": [{"message": {"content": '{"events":[]}'}}]}

    runner = OpenClawConfiguredLLMRunner(
        config=OpenClawLLMConfig(
            provider="minimax",
            model="MiniMax-M3",
            base_url="https://api.minimaxi.com/v1",
            api_kind="openai-completions",
            api_key="test-secret",
        ),
        thinking_mode="disabled",
        transport=transport,
    )

    assert runner.run("prompt", session_id="ignored") == '{"events":[]}'
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["reasoning_split"] is True


def test_disabled_thinking_does_not_repeat_an_empty_request():
    call_count = 0

    def transport(request, timeout):
        del request, timeout
        nonlocal call_count
        call_count += 1
        return {"choices": [{"message": {"content": ""}}]}

    runner = OpenClawConfiguredLLMRunner(
        config=OpenClawLLMConfig(
            provider="minimax",
            model="MiniMax-M3",
            base_url="https://api.minimaxi.com/v1",
            api_kind="openai-completions",
            api_key="test-secret",
        ),
        thinking_mode="disabled",
        transport=transport,
    )

    with pytest.raises(DirectLLMError, match="no assistant text"):
        runner.run("prompt", session_id="ignored")
    assert call_count == 1


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("minimax", "MiniMax-M2.7-highspeed"),
        ("compatible", "MiniMax-M3"),
    ],
)
def test_thinking_mode_is_capability_gated(provider, model):
    with pytest.raises(LLMConfigurationError, match="only.*MiniMax-M3"):
        OpenClawConfiguredLLMRunner(
            config=OpenClawLLMConfig(
                provider=provider,
                model=model,
                base_url="https://example.test/v1",
                api_kind="openai-completions",
                api_key="test-secret",
            ),
            thinking_mode="disabled",
        )


def test_direct_runner_rejects_provider_application_error():
    runner = OpenClawConfiguredLLMRunner(
        config=OpenClawLLMConfig(
            provider="minimax",
            model="MiniMax-M3",
            base_url="https://api.minimaxi.com/v1",
            api_kind="openai-completions",
            api_key="test-secret",
        ),
        thinking_mode="disabled",
        transport=lambda request, timeout: {
            "base_resp": {"status_code": 1004, "status_msg": "rate limited"},
            "choices": [{"message": {"content": "{}"}}],
        },
    )

    with pytest.raises(DirectLLMError, match="application error 1004"):
        runner.run("prompt", session_id="ignored")


def test_direct_runner_rejects_truncated_completion():
    runner = OpenClawConfiguredLLMRunner(
        config=OpenClawLLMConfig(
            provider="minimax",
            model="MiniMax-M3",
            base_url="https://api.minimaxi.com/v1",
            api_kind="openai-completions",
            api_key="test-secret",
        ),
        thinking_mode="disabled",
        transport=lambda request, timeout: {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "{\"events\":["},
                }
            ]
        },
    )

    with pytest.raises(DirectLLMError, match="truncated"):
        runner.run("prompt", session_id="ignored")


def test_direct_runner_rejects_unknown_thinking_mode():
    with pytest.raises(ValueError, match="thinking_mode"):
        OpenClawConfiguredLLMRunner(
            config=OpenClawLLMConfig(
                provider="minimax",
                model="MiniMax-M3",
                base_url="https://api.minimaxi.com/v1",
                api_kind="openai-completions",
                api_key="test-secret",
            ),
            thinking_mode="unsupported",
        )


def test_explicit_configured_model_override_uses_same_provider(tmp_path):
    config_path, models_path = write_openclaw_config(tmp_path)
    models = json.loads(models_path.read_text(encoding="utf-8"))
    models["providers"]["minimax"]["models"].append({"id": "MiniMax-M3"})
    models_path.write_text(json.dumps(models), encoding="utf-8")

    config = load_openclaw_llm_config(
        env={
            "MINIMAX_API_KEY": "test-secret",
            "LEAD_RADAR_LLM_MODEL": "minimax/MiniMax-M3",
        },
        config_path=config_path,
        models_path=models_path,
    )

    assert config.provider == "minimax"
    assert config.model == "MiniMax-M3"
    assert config.base_url == "https://api.minimaxi.com/v1"


def test_explicit_model_override_fails_closed_when_not_allowlisted(tmp_path):
    config_path, models_path = write_openclaw_config(tmp_path)

    with pytest.raises(LLMConfigurationError, match="not configured"):
        load_openclaw_llm_config(
            env={
                "MINIMAX_API_KEY": "test-secret",
                "LEAD_RADAR_LLM_MODEL": "minimax/MiniMax-Unknown",
            },
            config_path=config_path,
            models_path=models_path,
        )
