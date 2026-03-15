"""Tests for training metrics utility helpers."""

import builtins
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
METRICS_PATH = SRC / "training" / "utils" / "metrics.py"
SPEC = importlib.util.spec_from_file_location("metrics_under_test", METRICS_PATH)
assert SPEC is not None and SPEC.loader is not None
metrics_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics_module)
SystemMetricsCollector = metrics_module.SystemMetricsCollector


def test_collect_metrics_merges_sections_as_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Merge metrics from enabled collectors into a single payload."""
    collector = SystemMetricsCollector(collect_gpu=False, collect_disk=True)

    monkeypatch.setattr(
        collector,
        "_collect_cpu_metrics",
        lambda: {
            "process/step": 42.0,
            "system/cpu_utilization_percentage": 18.0,
        },
    )
    monkeypatch.setattr(collector, "_collect_gpu_metrics", lambda: {"system/gpu_0_power_watts": 95.0})
    monkeypatch.setattr(collector, "_collect_disk_metrics", lambda: {"system/disk_percent": 33.0})

    payload = collector.collect_metrics()

    assert payload["process/step"] == 42.0
    assert payload["system/cpu_utilization_percentage"] == 18.0
    assert payload["system/gpu_0_power_watts"] == 95.0
    assert payload["system/disk_percent"] == 33.0


def test_initialize_gpu_degrades_when_pynvml_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable GPU collection when optional pynvml dependency is unavailable."""
    original_import = builtins.__import__

    def _import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pynvml":
            raise ModuleNotFoundError("No module named 'pynvml'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)

    collector = SystemMetricsCollector(collect_gpu=True, collect_disk=False)

    assert collector._gpu_available is False
    assert collector._gpu_handles == []
    assert collector._collect_gpu_metrics() == {}


def test_cpu_and_disk_collection_failures_are_non_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return empty metrics when CPU or disk collection raises runtime errors."""

    class FailingPsutil:
        @staticmethod
        def cpu_percent(interval: object = None) -> float:
            raise RuntimeError("cpu error")

        @staticmethod
        def virtual_memory() -> SimpleNamespace:
            return SimpleNamespace(used=0, available=0, percent=0)

        @staticmethod
        def disk_usage(path: str) -> SimpleNamespace:
            raise RuntimeError("disk error")

    monkeypatch.setitem(sys.modules, "psutil", FailingPsutil)

    collector = SystemMetricsCollector(collect_gpu=False, collect_disk=True)

    assert collector._collect_cpu_metrics() == {}
    assert collector._collect_disk_metrics() == {}


def test_gpu_collection_skips_failed_device_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep collecting GPU metrics when one device fails."""

    class FakeNvml:
        @staticmethod
        def nvmlDeviceGetUtilizationRates(handle: str) -> SimpleNamespace:
            if handle == "bad":
                raise RuntimeError("gpu read failed")
            return SimpleNamespace(gpu=64)

        @staticmethod
        def nvmlDeviceGetMemoryInfo(handle: str) -> SimpleNamespace:
            return SimpleNamespace(used=512 * 1024 * 1024, total=1024 * 1024 * 1024)

        @staticmethod
        def nvmlDeviceGetPowerUsage(handle: str) -> float:
            return 120000

    monkeypatch.setitem(sys.modules, "pynvml", FakeNvml)

    collector = SystemMetricsCollector(collect_gpu=False, collect_disk=False)
    collector._gpu_available = True
    collector._gpu_handles = ["bad", "good"]

    payload = collector._collect_gpu_metrics()

    assert "system/gpu_0_utilization_percentage" not in payload
    assert payload["system/gpu_1_utilization_percentage"] == 64.0
    assert payload["system/gpu_1_memory_used_megabytes"] == 512.0
    assert payload["system/gpu_1_memory_percent"] == 50.0
    assert payload["system/gpu_1_power_watts"] == 120.0


def test_extract_from_value_covers_scalar_array_and_failure_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extract values for supported types and ignore non-convertible inputs."""
    metrics: dict[str, float] = {}

    class Scalar:
        def __init__(self, value: float) -> None:
            self._value = value

        def item(self) -> float:
            return self._value

    class TensorScalar:
        def item(self) -> int:
            return 7

        def numel(self) -> int:
            return 1

    class TensorArray:
        def item(self) -> int:
            return 0

        def numel(self) -> int:
            return 3

        def mean(self) -> Scalar:
            return Scalar(2.0)

        def std(self) -> Scalar:
            return Scalar(0.5)

        def min(self) -> Scalar:
            return Scalar(1.0)

        def max(self) -> Scalar:
            return Scalar(3.0)

    class ItemOnly:
        def item(self) -> int:
            return 9

    class NumpyLike:
        def mean(self) -> float:
            return 0.0

        def __len__(self) -> int:
            return 2

    class BadFloat:
        pass

    def _fake_numpy_extract(name: str, value: object, output: dict[str, float]) -> None:
        output[f"{name}/mean"] = 5.0
        output[f"{name}/std"] = 1.0
        output[f"{name}/min"] = 4.0
        output[f"{name}/max"] = 6.0

    monkeypatch.setattr(metrics_module, "_extract_numpy_statistics", _fake_numpy_extract)

    metrics_module._extract_from_value("none", None, metrics)
    metrics_module._extract_from_value("tensor_scalar", TensorScalar(), metrics)
    metrics_module._extract_from_value("tensor_array", TensorArray(), metrics)
    metrics_module._extract_from_value("item_only", ItemOnly(), metrics)
    metrics_module._extract_from_value("numpy_like", NumpyLike(), metrics)
    metrics_module._extract_from_value("single_seq", [11], metrics)
    metrics_module._extract_from_value("plain", 12.5, metrics)
    metrics_module._extract_from_value("bad", BadFloat(), metrics)

    assert "none" not in metrics
    assert metrics["tensor_scalar"] == 7.0
    assert metrics["tensor_array/mean"] == 2.0
    assert metrics["tensor_array/std"] == 0.5
    assert metrics["tensor_array/min"] == 1.0
    assert metrics["tensor_array/max"] == 3.0
    assert metrics["item_only"] == 9.0
    assert metrics["numpy_like/mean"] == 5.0
    assert metrics["single_seq"] == 11.0
    assert metrics["plain"] == 12.5
    assert "bad" not in metrics


def test_extract_from_tracking_data_respects_prefix_and_depth() -> None:
    """Extract nested metrics recursively until depth limit is reached."""
    metrics: dict[str, float] = {}

    metrics_module._extract_from_tracking_data(
        {
            "loss": 1.5,
            "nested": {
                "reward": [3],
                "deeper": {"ignored": 9},
            },
        },
        metrics,
        prefix="process/",
        max_depth=2,
    )

    assert metrics["process/loss"] == 1.5
    assert metrics["process/nested/reward"] == 3.0
    assert "process/nested/deeper/ignored" not in metrics
