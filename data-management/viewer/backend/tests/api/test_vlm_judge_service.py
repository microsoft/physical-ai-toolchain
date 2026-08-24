"""Unit tests for the dataviewer VLM-judge service factory.

These exercise ``get_vlm_judge_service`` without touching a dataset, the
network, or model weights, so they always run in CI regardless of which
datasets are present.
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pytest

import src.api.services.vlm_judge_service as vjs
from src.api.config import AppConfig, load_config


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure each test starts and ends with a clean service singleton."""
    vjs.reset_vlm_judge_service()
    yield
    vjs.reset_vlm_judge_service()


def _config(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> AppConfig:
    monkeypatch.setenv("VLM_JUDGE_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("VLM_JUDGE_BACKEND", "echo")
    return load_config()


def _config_with_process_method(monkeypatch: pytest.MonkeyPatch, process_method: str) -> AppConfig:
    monkeypatch.setenv("VLM_JUDGE_ENABLED", "true")
    monkeypatch.setenv("VLM_JUDGE_BACKEND", "echo")
    monkeypatch.setenv("VLM_JUDGE_PROCESS_METHOD", process_method)
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


def test_warns_when_process_method_env_value_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _config_with_process_method(monkeypatch, "backwards")

    service = vjs.get_vlm_judge_service(config)

    assert service is not None
    assert service.config.agent.process_method == "gvl"
    assert "Invalid VLM_JUDGE_PROCESS_METHOD='backwards'; falling back to 'gvl'" in caplog.text


def test_builds_singleton_once_for_concurrent_first_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import evaluation.vlm_judge as eval_vlm

    original = eval_vlm.JudgeService
    calls = 0
    guard = Lock()

    class SlowJudgeService(original):
        def __init__(self, *args, **kwargs) -> None:
            nonlocal calls
            with guard:
                calls += 1
            time.sleep(0.01)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(eval_vlm, "JudgeService", SlowJudgeService)
    config = _config(monkeypatch, enabled=True)

    with ThreadPoolExecutor(max_workers=8) as executor:
        services = list(executor.map(lambda _idx: vjs.get_vlm_judge_service(config), range(8)))

    assert calls == 1
    assert all(service is services[0] for service in services)
