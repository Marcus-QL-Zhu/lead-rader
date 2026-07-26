"""Direct LLM calls using the provider configuration owned by OpenClaw."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


class LLMConfigurationError(ValueError):
    """Raised when OpenClaw's active model cannot be resolved safely."""


class DirectLLMError(RuntimeError):
    """Raised when the provider request or response is invalid."""


@dataclass(frozen=True)
class OpenClawLLMConfig:
    provider: str
    model: str
    base_url: str
    api_kind: str
    api_key: str = field(repr=False)


def _mapping_at(value: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    current: Any = value
    for key in keys:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key)
    return current if isinstance(current, Mapping) else {}


def _primary_model(config: Mapping[str, Any]) -> str:
    model = _mapping_at(config, "agents", "defaults").get("model")
    if isinstance(model, Mapping):
        model = model.get("primary")
    value = str(model or "").strip()
    if "/" not in value:
        raise LLMConfigurationError(
            "OpenClaw primary model must use provider/model format"
        )
    return value


def _provider_env_name(provider: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", provider).upper().strip("_")
    return f"{normalized}_API_KEY"


def _resolve_key(value: Any, env: Mapping[str, str]) -> str:
    raw = str(value or "").strip()
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", raw)
    if match:
        return str(env.get(match.group(1)) or "").strip()
    if raw.startswith("$") and re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", raw):
        return str(env.get(raw[1:]) or "").strip()
    return raw


def load_openclaw_llm_config(
    *,
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
    models_path: str | Path | None = None,
) -> OpenClawLLMConfig:
    """Resolve OpenClaw's primary model and credentials without invoking it."""

    active_env = dict(os.environ if env is None else env)
    root = Path(active_env.get("OPENCLAW_HOME") or Path.home() / ".openclaw")
    resolved_config = Path(
        config_path
        or active_env.get("OPENCLAW_CONFIG_PATH")
        or root / "openclaw.json"
    )
    resolved_models = Path(
        models_path
        or active_env.get("OPENCLAW_MODELS_PATH")
        or root / "agents" / "main" / "agent" / "models.json"
    )
    try:
        config = json.loads(resolved_config.read_text(encoding="utf-8"))
        models = json.loads(resolved_models.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LLMConfigurationError(
            f"cannot read OpenClaw model configuration: {type(error).__name__}"
        ) from error
    if not isinstance(config, Mapping) or not isinstance(models, Mapping):
        raise LLMConfigurationError("OpenClaw model configuration must be JSON objects")

    selected = str(
        active_env.get("LEAD_RADAR_LLM_MODEL") or _primary_model(config)
    ).strip()
    provider, model = selected.split("/", 1)
    providers = models.get("providers")
    provider_config = providers.get(provider) if isinstance(providers, Mapping) else None
    if not isinstance(provider_config, Mapping):
        raise LLMConfigurationError(
            f"OpenClaw provider configuration is missing: {provider}"
        )
    base_url = str(provider_config.get("baseUrl") or "").strip().rstrip("/")
    api_kind = str(provider_config.get("api") or "").strip()
    if not base_url:
        raise LLMConfigurationError(f"OpenClaw provider has no baseUrl: {provider}")
    if api_kind != "openai-completions":
        raise LLMConfigurationError(
            f"unsupported OpenClaw provider API kind: {api_kind or '<empty>'}"
        )

    env_key = _provider_env_name(provider)
    api_key = str(active_env.get(env_key) or "").strip()
    if not api_key:
        api_key = _resolve_key(provider_config.get("apiKey"), active_env)
    if not api_key:
        raise LLMConfigurationError(
            f"OpenClaw provider credentials are unavailable: {provider}"
        )

    configured_models = provider_config.get("models")
    if isinstance(configured_models, list):
        known_ids = {
            str(item.get("id") or "")
            for item in configured_models
            if isinstance(item, Mapping)
        }
        if known_ids and model not in known_ids:
            raise LLMConfigurationError(
                f"OpenClaw primary model is not configured for {provider}: {model}"
            )
    return OpenClawLLMConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_kind=api_kind,
        api_key=api_key,
    )


Transport = Callable[[urllib.request.Request, float], Mapping[str, Any]]


def _default_transport(
    request: urllib.request.Request,
    timeout: float,
) -> Mapping[str, Any]:
    retryable_statuses = {429, 500, 502, 503, 504}
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, Mapping):
                raise DirectLLMError(
                    "LLM provider response must be a JSON object"
                )
            return payload
        except urllib.error.HTTPError as error:
            detail = error.read(1000).decode("utf-8", errors="replace")
            if error.code in retryable_statuses and attempt < 2:
                retry_after = str(error.headers.get("Retry-After") or "").strip()
                delay = float(retry_after) if retry_after.isdigit() else 2**attempt
                time.sleep(min(delay, 8.0))
                continue
            raise DirectLLMError(
                f"LLM provider returned HTTP {error.code}: {detail}"
            ) from error
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            if isinstance(error, OSError) and attempt < 2:
                time.sleep(2**attempt)
                continue
            raise DirectLLMError(
                f"LLM provider request failed: {type(error).__name__}"
            ) from error
    raise DirectLLMError("LLM provider retries exhausted")


def _message_text(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise DirectLLMError("LLM provider response has no choices")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, Mapping) and item.get("type") == "text"
        ]
        text = "".join(parts).strip()
        if text:
            return text
    raise DirectLLMError("LLM provider response has no assistant text")


class OpenClawConfiguredLLMRunner:
    """Call the active OpenClaw provider directly, bypassing OpenClaw Agent."""

    def __init__(
        self,
        *,
        config: OpenClawLLMConfig | None = None,
        timeout_seconds: float = 240,
        max_completion_tokens: int = 16384,
        transport: Transport | None = None,
    ) -> None:
        self.config = config or load_openclaw_llm_config()
        self.timeout_seconds = timeout_seconds
        self.max_completion_tokens = max_completion_tokens
        self.transport = transport or _default_transport

    def run(
        self,
        prompt: str,
        *,
        session_id: str,
        system_prompt: str = "",
    ) -> str:
        del session_id  # Retained only to satisfy the shared PromptRunner contract.
        messages = []
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt.strip()})
        messages.append({"role": "user", "content": prompt})
        body = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "temperature": 0.2,
            "max_completion_tokens": self.max_completion_tokens,
            "reasoning_split": True,
        }
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return _message_text(self.transport(request, self.timeout_seconds))


__all__ = [
    "DirectLLMError",
    "LLMConfigurationError",
    "OpenClawConfiguredLLMRunner",
    "OpenClawLLMConfig",
    "load_openclaw_llm_config",
]
