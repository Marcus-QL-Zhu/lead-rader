from __future__ import annotations

import argparse
import json

from scripts.capture_dedicated_sources import _llm_runner


def test_capture_runner_uses_openclaw_provider_configuration(tmp_path) -> None:
    config_path = tmp_path / "openclaw.json"
    models_path = tmp_path / "models.json"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"model": "minimax/MiniMax-M3"}}}),
        encoding="utf-8",
    )
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "minimax": {
                        "baseUrl": "https://example.invalid/v1",
                        "api": "openai-completions",
                        "apiKey": "test-key",
                        "models": [{"id": "MiniMax-M3"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        use_openclaw_llm=True,
        env_file=None,
        model="minimax/MiniMax-M3",
        openclaw_config=config_path,
        openclaw_models=models_path,
        llm_timeout=12.0,
        max_completion_tokens=1024,
    )

    runner = _llm_runner(args)

    assert runner is not None
    assert runner.config.model == "MiniMax-M3"
    assert runner.thinking_mode == "disabled"


def test_capture_runner_is_none_in_rules_only_mode() -> None:
    assert _llm_runner(argparse.Namespace(use_openclaw_llm=False)) is None
