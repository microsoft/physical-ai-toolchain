"""Tests for training environment utility helpers."""

import pytest
from conftest import load_training_module

_ENV_MODULE = load_training_module("training_utils_env", "training/utils/env.py")
require_env = _ENV_MODULE.require_env
set_env_defaults = _ENV_MODULE.set_env_defaults


def test_require_env_returns_present_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the environment variable value when present and non-empty."""
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-123")

    result = require_env("AZURE_SUBSCRIPTION_ID")

    assert result == "sub-123"


@pytest.mark.parametrize("value", [None, ""])
def test_require_env_raises_runtime_error_for_missing_or_empty(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """Raise RuntimeError when variable is unset or empty by default."""
    key = "AZURE_RESOURCE_GROUP"
    if value is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, value)

    with pytest.raises(RuntimeError):
        require_env(key)


def test_require_env_honors_custom_error_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise provided error type when variable is missing."""
    monkeypatch.delenv("AZURE_WORKSPACE_NAME", raising=False)

    with pytest.raises(ValueError):
        require_env("AZURE_WORKSPACE_NAME", error_type=ValueError)


def test_set_env_defaults_sets_missing_and_preserves_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set only missing defaults and keep pre-existing values unchanged."""
    monkeypatch.setenv("EXISTING_KEY", "current")
    monkeypatch.delenv("MISSING_KEY", raising=False)

    set_env_defaults(
        {
            "EXISTING_KEY": "new-default",
            "MISSING_KEY": "applied-default",
        }
    )

    assert require_env("EXISTING_KEY") == "current"
    assert require_env("MISSING_KEY") == "applied-default"
