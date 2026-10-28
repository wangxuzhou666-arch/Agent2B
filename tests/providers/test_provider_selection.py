"""``provider_from_env`` says so when the mock is the default and model settings sit unused.

A model configured without ``TWOTRACK_PROVIDER=openai_compat`` used to get the mock in silence,
and the first sign was canned answers. Selecting the mock is unchanged; the start now logs one
warning that names the ignored settings (never their values) and the variable that switches them
on. An explicit ``TWOTRACK_PROVIDER=mock`` is a choice: docker-compose.yml sets it beside default
``TWOTRACK_LLM_*`` values on every start, so it must stay quiet.
"""

import logging

import pytest

from twotrack.providers import MockProvider, OpenAICompatProvider, provider_from_env

LOGGER = "twotrack.providers"
MODEL = {"TWOTRACK_LLM_MODEL": "local-model", "TWOTRACK_LLM_BASE_URL": "http://localhost:8000/v1",
         "TWOTRACK_LLM_API_KEY": "placeholder-not-a-key"}


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == LOGGER and r.levelno == logging.WARNING]


def test_model_settings_without_a_provider_warn_once_and_still_select_the_mock(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        provider = provider_from_env(MODEL)
    assert isinstance(provider, MockProvider)  # the behaviour is the one it always had
    (message,) = warnings(caplog)
    assert "mock" in message and "TWOTRACK_PROVIDER=openai_compat" in message
    assert all(name in message for name in MODEL)
    assert not any(value in caplog.text for value in MODEL.values())  # names, never values


def test_a_timeout_alone_is_a_setting_the_mock_ignores_too(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        provider_from_env({"TWOTRACK_LLM_TIMEOUT_SECONDS": "60"})
    (message,) = warnings(caplog)
    assert "TWOTRACK_LLM_TIMEOUT_SECONDS" in message


@pytest.mark.parametrize("env", [
    {},
    {"TWOTRACK_DATA_DIR": "data"},
    {"TWOTRACK_LLM_MODEL": "  ", "TWOTRACK_LLM_API_KEY": ""},
    {"TWOTRACK_PROVIDER": "mock", **MODEL},
    {"TWOTRACK_PROVIDER": " Mock ", **MODEL},
], ids=["nothing-set", "unrelated-setting", "blank-values", "mock-chosen", "mock-chosen-loosely"])
def test_nothing_unused_or_the_mock_chosen_is_quiet(caplog, env):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert isinstance(provider_from_env(env), MockProvider)
    assert warnings(caplog) == []


def test_a_real_provider_is_quiet(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        provider = provider_from_env({"TWOTRACK_PROVIDER": "openai_compat", **MODEL})
    assert isinstance(provider, OpenAICompatProvider)
    assert warnings(caplog) == []
