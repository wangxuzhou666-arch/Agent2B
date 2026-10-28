"""LLM providers. Pick one with ``provider_from_env``; pipeline code never branches on it.

Environment:
* ``TWOTRACK_PROVIDER``      - ``mock`` (default, zero key) or ``openai_compat``
* ``TWOTRACK_LLM_MODEL``     - required for ``openai_compat``
* ``TWOTRACK_LLM_BASE_URL``  - optional; any OpenAI-compatible endpoint (vLLM, Ollama, ...)
* ``TWOTRACK_LLM_API_KEY``   - optional for local endpoints
* ``TWOTRACK_LLM_TIMEOUT_SECONDS``        - wait for a model's answer (default 30)
* ``TWOTRACK_LLM_ROUTE_TIMEOUT_SECONDS``  - wait for its routing opinion (default 10)
* ``TWOTRACK_LLM_TEMPERATURE``            - unset: each call's own (route 0, narration 0.2,
  chat 0.3, agent steps 0); ``omit``: send none, for models that reject the parameter (Claude
  models from 4.7 on refuse any value below 1); a number from 0 to 2: that value for every call

The timeouts and the temperature are checked whichever provider is selected: a malformed one
stops the start (``SettingsError``) instead of waiting for the day a real model is switched on.

With ``TWOTRACK_PROVIDER`` unset the mock is selected, and a ``TWOTRACK_LLM_*`` setting beside it
is not used: ``provider_from_env`` logs one warning naming those settings (never their values).
An explicit ``TWOTRACK_PROVIDER=mock`` is a choice and stays quiet; docker-compose.yml sets it
together with default ``TWOTRACK_LLM_*`` values.
"""

import logging
import os
from collections.abc import Mapping

from twotrack.settings import _number, _temperature

from .base import (
    AgentStep,
    ChatMessage,
    GuardedProvider,
    LLMRoute,
    ModelCallError,
    NoScrub,
    Provider,
    ProviderError,
    ProviderInfo,
    Scrubber,
    ToolCall,
    ToolCard,
    UnparseableReply,
    guarded,
    provider_info,
)
from .mock import MockProvider
from .openai_compat import (
    DEFAULT_ROUTE_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    OpenAICompatProvider,
)

log = logging.getLogger("twotrack.providers")

PROVIDER_ENV = "TWOTRACK_PROVIDER"
LLM_ENV_PREFIX = "TWOTRACK_LLM_"
LLM_TIMEOUT_ENV = "TWOTRACK_LLM_TIMEOUT_SECONDS"
LLM_ROUTE_TIMEOUT_ENV = "TWOTRACK_LLM_ROUTE_TIMEOUT_SECONDS"
LLM_TEMPERATURE_ENV = "TWOTRACK_LLM_TEMPERATURE"
# "Positive" with a floor: no real model call finishes in under a tenth of a second.
MIN_TIMEOUT_SECONDS = 0.1


def _warn_if_llm_settings_are_unused(env: Mapping[str, str]) -> None:
    """One warning when ``TWOTRACK_LLM_*`` settings exist but the mock was selected by default.
    Names only: a key is one of the values."""
    unused = sorted(name for name, value in env.items()
                    if name.startswith(LLM_ENV_PREFIX) and value.strip())
    if unused:
        log.warning("provider: %s is not set, so the mock model is in use and these settings are "
                    "ignored: %s (set %s=openai_compat to use them)",
                    PROVIDER_ENV, ", ".join(unused), PROVIDER_ENV)


def provider_from_env(env: Mapping[str, str] | None = None) -> Provider:
    """Build the configured provider. A half-configured real provider fails loudly; it never
    silently falls back to mock (that would hide a misconfiguration behind canned answers)."""
    env = os.environ if env is None else env
    kind = (env.get(PROVIDER_ENV) or "mock").strip().lower()
    timeout = _number(env, LLM_TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS,
                      minimum=MIN_TIMEOUT_SECONDS)
    route_timeout = _number(env, LLM_ROUTE_TIMEOUT_ENV, DEFAULT_ROUTE_TIMEOUT_SECONDS,
                            minimum=MIN_TIMEOUT_SECONDS)
    temperature = _temperature(env, LLM_TEMPERATURE_ENV)
    if kind == "mock":
        if not env.get(PROVIDER_ENV):  # the default, not a choice
            _warn_if_llm_settings_are_unused(env)
        return MockProvider()
    if kind == "openai_compat":
        model = (env.get("TWOTRACK_LLM_MODEL") or "").strip()
        if not model:
            raise ValueError("TWOTRACK_PROVIDER=openai_compat needs TWOTRACK_LLM_MODEL")
        return OpenAICompatProvider(
            model=model,
            base_url=(env.get("TWOTRACK_LLM_BASE_URL") or "").strip() or None,
            api_key=(env.get("TWOTRACK_LLM_API_KEY") or "").strip() or None,
            timeout=timeout,
            route_timeout=route_timeout,
            temperature=temperature,
        )
    raise ValueError(f"unknown {PROVIDER_ENV} {kind!r}; expected 'mock' or 'openai_compat'")


__all__ = [
    "PROVIDER_ENV",
    "AgentStep",
    "ChatMessage",
    "GuardedProvider",
    "LLMRoute",
    "MockProvider",
    "ModelCallError",
    "NoScrub",
    "OpenAICompatProvider",
    "Provider",
    "ProviderError",
    "ProviderInfo",
    "Scrubber",
    "ToolCall",
    "ToolCard",
    "UnparseableReply",
    "guarded",
    "provider_from_env",
    "provider_info",
]
