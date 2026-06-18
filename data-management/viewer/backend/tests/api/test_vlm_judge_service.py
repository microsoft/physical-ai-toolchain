"""Unit tests for the dataviewer VLM-judge service factory.

These exercise ``get_vlm_judge_service`` without touching a dataset, the
network, or model weights, so they always run in CI regardless of which
datasets are present.
"""

from __future__ import annotations

import sys

import pytest

import src.api.services.vlm_judge_service as vjs
from src.api.config import load_config


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure each test starts and ends with a clean service singleton."""
    vjs.reset_vlm_judge_service()
    yield
    vjs.reset_vlm_judge_service()


def _config(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> object:
    monkeypatch.setenv("VLM_JUDGE_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("VLM_JUDGE_BACKEND", "echo")
    return load_config()


def test_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    assert vjs.get_vlm_judge_service(_config(monkeypatch, enabled=False)) is None


def test_builds_and_memoizes_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(monkeypatch, enabled=True)
    service = vjs.get_vlm_judge_service(config)
    assert service is not None
    assert service.model_id == "Qwen/Qwen3-VL-4B-Instruct"
    # Second call returns the exact same instance (lazy singleton).
    assert vjs.get_vlm_judge_service(config) is service


def test_reset_drops_the_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(monkeypatch, enabled=True)
    first = vjs.get_vlm_judge_service(config)
    vjs.reset_vlm_judge_service()
    second = vjs.get_vlm_judge_service(config)
    assert first is not None
    assert second is not None
    assert first is not second


def test_returns_none_when_evaluation_package_unimportable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force ``from evaluation.vlm_judge import ...`` to raise ImportError.
    monkeypatch.setitem(sys.modules, "evaluation.vlm_judge", None)
    assert vjs.get_vlm_judge_service(_config(monkeypatch, enabled=True)) is None
