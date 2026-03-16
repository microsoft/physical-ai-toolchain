"""System and training metrics collection utilities.

Provides reusable metrics collectors for training integrations.
CPU and memory metrics require ``psutil``. GPU metrics require
``pynvml``; collection degrades gracefully when pynvml is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


def _is_tensor_scalar(value: Any) -> bool:
    """Check whether *value* is a single-element tensor.

    Args:
        value: Object to inspect.

    Returns:
        ``True`` when *value* has ``item`` and ``numel`` attributes
        and contains exactly one element.
    """
    return hasattr(value, "item") and hasattr(value, "numel") and value.numel() == 1


def _is_tensor_array(value: Any) -> bool:
    """Check whether *value* is a multi-element tensor.

    Args:
        value: Object to inspect.

    Returns:
        ``True`` when *value* has ``item`` and ``numel`` attributes
        and contains more than one element.
    """
    return hasattr(value, "item") and hasattr(value, "numel") and value.numel() > 1


def _is_numpy_array(value: Any) -> bool:
    """Check whether *value* is a multi-element numpy array.

    Args:
        value: Object to inspect.

    Returns:
        ``True`` when *value* has a ``mean`` attribute, supports
        ``len()``, and contains more than one element.
    """
    return hasattr(value, "mean") and hasattr(value, "__len__") and len(value) > 1


def _is_single_element_sequence(value: Any) -> bool:
    """Check whether *value* is a sequence with exactly one element.

    Args:
        value: Object to inspect.

    Returns:
        ``True`` when *value* supports ``len()`` and contains
        exactly one element.
    """
    return hasattr(value, "__len__") and len(value) == 1


def _extract_tensor_scalar(name: str, value: Any, metrics: dict[str, float]) -> None:
    """Extract scalar value from a single-element tensor into *metrics*.

    Args:
        name: Metric key name.
        value: Single-element tensor with an ``item`` method.
        metrics: Output dictionary to populate.
    """
    metrics[name] = float(value.item())


def _extract_tensor_statistics(name: str, value: Any, metrics: dict[str, float]) -> None:
    """Extract mean, std, min, and max statistics from a tensor array.

    Stores four keys: ``{name}/mean``, ``{name}/std``, ``{name}/min``,
    ``{name}/max``.

    Args:
        name: Base metric key name.
        value: Multi-element tensor with ``mean``, ``std``, ``min``,
            and ``max`` methods.
        metrics: Output dictionary to populate.
    """
    if hasattr(value, "mean"):
        metrics[f"{name}/mean"] = float(value.mean().item())
    if hasattr(value, "std"):
        metrics[f"{name}/std"] = float(value.std().item())
    if hasattr(value, "min"):
        metrics[f"{name}/min"] = float(value.min().item())
    if hasattr(value, "max"):
        metrics[f"{name}/max"] = float(value.max().item())


def _extract_numpy_statistics(name: str, value: Any, metrics: dict[str, float]) -> None:
    """Extract mean, std, min, and max statistics from a numpy array.

    Imports ``numpy`` at call time. Stores four keys:
    ``{name}/mean``, ``{name}/std``, ``{name}/min``, ``{name}/max``.

    Args:
        name: Base metric key name.
        value: Array-like object convertible via ``numpy.asarray``.
        metrics: Output dictionary to populate.
    """
    import numpy as np

    arr = np.asarray(value)
    metrics[f"{name}/mean"] = float(np.mean(arr))
    metrics[f"{name}/std"] = float(np.std(arr))
    metrics[f"{name}/min"] = float(np.min(arr))
    metrics[f"{name}/max"] = float(np.max(arr))


def _extract_from_value(name: str, value: int | float | Any, metrics: dict[str, float]) -> None:
    """Extract numeric value and add to metrics dict.

    Handles tensors, numpy arrays, and scalar types. Multi-element arrays
    are converted to mean/std/min/max statistics.

    Args:
        name: Metric name.
        value: Value to extract (tensor, array, or scalar).
        metrics: Output dictionary to populate.
    """
    if value is None:
        return

    try:
        if _is_tensor_scalar(value):
            _extract_tensor_scalar(name, value, metrics)
        elif _is_tensor_array(value):
            _extract_tensor_statistics(name, value, metrics)
        elif hasattr(value, "item"):
            metrics[name] = float(value.item())
        elif _is_numpy_array(value):
            _extract_numpy_statistics(name, value, metrics)
        elif _is_single_element_sequence(value):
            metrics[name] = float(value[0])
        else:
            metrics[name] = float(value)
    except (ValueError, TypeError, AttributeError, IndexError) as exc:
        _LOGGER.debug("Could not convert %s to float: %s", name, exc)


def _extract_from_tracking_data(
    data: dict[str, Any],
    metrics: dict[str, float],
    prefix: str,
    max_depth: int = 2,
) -> None:
    """Recursively extract metrics from tracking_data dict.

    Args:
        data: Dictionary to extract from.
        metrics: Output dictionary to populate.
        prefix: Metric name prefix for nested structures.
        max_depth: Maximum recursion depth.
    """
    if max_depth <= 0:
        return

    for key, value in data.items():
        metric_name = f"{prefix}{key}" if prefix else key

        if isinstance(value, dict):
            _extract_from_tracking_data(value, metrics, f"{metric_name}/", max_depth - 1)
        else:
            _extract_from_value(metric_name, value, metrics)


_STANDARD_METRIC_ATTRS = [
    "episode_reward",
    "episode_reward_mean",
    "episode_length",
    "episode_length_mean",
    "cumulative_rewards",
    "mean_rewards",
    "episode_lengths",
    "success_rate",
    "policy_loss",
    "value_loss",
    "critic_loss",
    "entropy",
    "learning_rate",
    "lr",
    "grad_norm",
    "gradient_norm",
    "kl_divergence",
    "kl",
    "timesteps",
    "timesteps_total",
    "total_timesteps",
    "iterations",
    "iterations_total",
    "fps",
    "time_elapsed",
    "epoch_time",
    "rollout_time",
    "learning_time",
]


class SystemMetricsCollector:
    """Collect system-level CPU, memory, GPU, and disk metrics.

    All returned metric keys use the ``system/`` prefix. GPU metrics
    are optional and omitted when ``pynvml`` is unavailable or
    initialization fails; a warning is logged in that case.

    Attributes:
        _collect_disk: Whether disk metrics are included in collection.
        _gpu_available: Whether GPU monitoring initialized successfully.
        _gpu_handles: List of pynvml device handles, empty when GPU
            is unavailable.
    """

    def __init__(self, collect_gpu: bool = True, collect_disk: bool = True) -> None:
        """Initialize system metrics collector.

        When *collect_gpu* is enabled, GPU monitoring is initialized via
        ``pynvml``. If ``pynvml`` is unavailable or initialization raises
        an exception, GPU metrics are disabled and a warning is logged.

        Args:
            collect_gpu: Enable GPU metrics collection. Requires ``pynvml``.
            collect_disk: Enable disk metrics collection.
        """
        self._collect_disk = collect_disk
        self._gpu_available = False
        self._gpu_handles: list[Any] = []

        if collect_gpu:
            self._initialize_gpu()

    def _initialize_gpu(self) -> None:
        """Initialize GPU monitoring via pynvml.

        Discovers NVIDIA devices and stores their handles for subsequent
        metric collection. Logs device count on success. On failure
        (missing ``pynvml`` or driver issues), sets
        ``_gpu_available`` to ``False`` and logs a warning.
        """
        try:
            import pynvml

            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
            self._gpu_handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(device_count)]
            self._gpu_available = True
            _LOGGER.info("GPU metrics collection initialized (%d devices)", device_count)
        except Exception as exc:
            _LOGGER.warning("GPU metrics unavailable (will only log CPU/memory/disk): %s", exc)
            self._gpu_available = False

    def collect_metrics(self) -> dict[str, float]:
        """Collect all enabled system metrics.

        Aggregates CPU, memory, GPU, and disk metrics into a single
        dictionary. GPU entries are omitted when GPU monitoring is
        unavailable. Disk entries are omitted when disk collection is
        disabled via the ``collect_disk`` initialization flag.

        Returns:
            Dictionary of metric names to float values. All keys use
            the ``system/`` prefix.
        """
        metrics: dict[str, float] = {}

        metrics.update(self._collect_cpu_metrics())
        metrics.update(self._collect_gpu_metrics())

        if self._collect_disk:
            metrics.update(self._collect_disk_metrics())

        return metrics

    def _collect_cpu_metrics(self) -> dict[str, float]:
        """Collect CPU and memory metrics via ``psutil``.

        Imports ``psutil`` at call time. Collects CPU utilization
        percentage and virtual memory used, available, and percent
        metrics.

        Returns:
            Dictionary with ``system/cpu_utilization_percentage``,
            ``system/memory_used_megabytes``,
            ``system/memory_available_megabytes``, and
            ``system/memory_percent`` keys. Empty when runtime
            collection encounters an error.
        """
        import psutil

        metrics: dict[str, float] = {}

        try:
            metrics["system/cpu_utilization_percentage"] = psutil.cpu_percent(interval=None)

            mem = psutil.virtual_memory()
            metrics["system/memory_used_megabytes"] = mem.used / (1024 * 1024)
            metrics["system/memory_available_megabytes"] = mem.available / (1024 * 1024)
            metrics["system/memory_percent"] = mem.percent
        except Exception as exc:
            _LOGGER.debug("CPU/memory metrics collection failed: %s", exc)

        return metrics

    def _collect_gpu_metrics(self) -> dict[str, float]:
        """Collect GPU metrics via ``pynvml`` for each NVIDIA device.

        Imports ``pynvml`` at call time. Collects utilization, memory
        usage, and power draw per device. Individual device failures
        are logged at debug level and skipped.

        Returns:
            Dictionary with ``system/gpu_{i}_utilization_percentage``,
            ``system/gpu_{i}_memory_used_megabytes``,
            ``system/gpu_{i}_memory_percent``, and
            ``system/gpu_{i}_power_watts`` keys per device. Empty when
            GPU monitoring is unavailable.
        """
        if not self._gpu_available:
            return {}

        import pynvml

        metrics: dict[str, float] = {}

        for i, handle in enumerate(self._gpu_handles):
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                metrics[f"system/gpu_{i}_utilization_percentage"] = float(util.gpu)

                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                metrics[f"system/gpu_{i}_memory_used_megabytes"] = mem_info.used / (1024 * 1024)
                metrics[f"system/gpu_{i}_memory_percent"] = (mem_info.used / mem_info.total) * 100

                power = pynvml.nvmlDeviceGetPowerUsage(handle)
                metrics[f"system/gpu_{i}_power_watts"] = power / 1000
            except Exception as exc:
                _LOGGER.debug("GPU %d metrics collection failed: %s", i, exc)

        return metrics

    def _collect_disk_metrics(self) -> dict[str, float]:
        """Collect disk usage metrics via ``psutil`` for the root filesystem.

        Imports ``psutil`` at call time. Reports used and available
        space in gigabytes and overall utilization percentage.

        Returns:
            Dictionary with ``system/disk_used_gigabytes``,
            ``system/disk_available_gigabytes``, and
            ``system/disk_percent`` keys. Empty when runtime
            collection encounters an error.
        """
        import psutil

        metrics: dict[str, float] = {}

        try:
            disk = psutil.disk_usage("/")
            metrics["system/disk_used_gigabytes"] = disk.used / (1024 * 1024 * 1024)
            metrics["system/disk_available_gigabytes"] = disk.free / (1024 * 1024 * 1024)
            metrics["system/disk_percent"] = disk.percent
        except Exception as exc:
            _LOGGER.debug("Disk metrics collection failed: %s", exc)

        return metrics
