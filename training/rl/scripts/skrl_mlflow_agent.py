"""SKRL MLflow integration utilities.

This module provides utility functions for integrating SKRL agent training with
MLflow metric logging via monkey-patching agent._update methods.

Available Metrics
-----------------
Extracts the following metric categories from SKRL agents:

**Episode Statistics:**
- Reward metrics: episode_reward, episode_reward_mean, cumulative_rewards, mean_rewards
- Episode length metrics: episode_length, episode_length_mean, episode_lengths
- Success metrics: success_rate

**Training Losses:**
- Policy loss: policy_loss
- Value/Critic loss: value_loss, critic_loss
- Entropy: entropy

**Optimization Metrics:**
- Learning rate: learning_rate, lr
- Gradient norm: grad_norm, gradient_norm
- KL divergence: kl_divergence, kl

**Timing Metrics:**
- Timesteps: timesteps, timesteps_total, total_timesteps
- Iterations: iterations, iterations_total
- FPS: fps
- Timing: time_elapsed, epoch_time, rollout_time, learning_time

**Additional Metrics:**
All entries in agent.tracking_data dict are extracted, supporting custom metrics
from different SKRL algorithms (PPO, SAC, TD3, DDPG, A2C, etc.).

**System Metrics:**
Collected via psutil and pynvml with system/ prefix:
- CPU: system/cpu_utilization_percentage
- Memory: system/memory_used_megabytes, system/memory_percent, system/memory_available_megabytes
- GPU: system/gpu_{i}_utilization_percentage, system/gpu_{i}_memory_percent,
  system/gpu_{i}_memory_used_megabytes, system/gpu_{i}_power_watts
- Disk: system/disk_used_gigabytes, system/disk_percent, system/disk_available_gigabytes

Metric Logging
--------------
Metrics are logged to MLflow after each agent._update() call, which is when SKRL
agents populate their tracking_data dict. This occurs after collecting rollouts
(e.g., every 16 environment steps for default PPO config), ensuring metrics
reflect actual training updates rather than environment interactions.

Metric Filtering
----------------
Use the metric_filter parameter to control which metrics are logged:
- None (default): Log all available metrics
- set of str: Only log metrics whose names are in the set
- Useful for reducing MLflow API load in production environments

Usage Example
-------------
```python
from training.scripts.skrl_mlflow_agent import create_mlflow_logging_wrapper
import mlflow

# After creating SKRL runner with agent
wrapper_func = create_mlflow_logging_wrapper(
    agent=runner.agent,
    mlflow_module=mlflow,
    metric_filter=None,
    collect_gpu_metrics=True,
)

# Monkey-patch the agent's _update method
runner.agent._update = wrapper_func

# Now when runner.run() executes, metrics will be logged to MLflow
runner.run()
```
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from training.utils.metrics import (
    _STANDARD_METRIC_ATTRS,
    SystemMetricsCollector,
    _extract_from_tracking_data,
    _extract_from_value,
)

_LOGGER = logging.getLogger(__name__)


@runtime_checkable
class SkrlAgent(Protocol):
    """Protocol defining the interface expected from SKRL agents for metric extraction."""

    tracking_data: dict[str, Any]
    _update: Callable[[int, int], Any]


@runtime_checkable
class MLflowModule(Protocol):
    """Protocol defining the MLflow API used for logging metrics."""

    def log_metrics(
        self,
        metrics: dict[str, float],
        step: int | None = None,
        synchronous: bool = True,
    ) -> None: ...


def _has_tracking_data(agent: SkrlAgent) -> bool:
    """Check if agent has valid tracking_data dict."""
    return hasattr(agent, "tracking_data") and isinstance(agent.tracking_data, dict)


def _extract_metrics_from_agent(
    agent: SkrlAgent,
    metric_filter: set[str] | None = None,
) -> dict[str, float]:
    """Extract metrics from SKRL agent's internal state.

    Extracts from agent.tracking_data dict, direct attributes, and nested
    structures. Multi-element values produce mean/std/min/max statistics.

    Args:
        agent: SKRL agent instance with tracking_data dict.
        metric_filter: Optional set of metric names to include.

    Returns:
        Dictionary of metric names to float values.
    """
    metrics: dict[str, float] = {}

    if _has_tracking_data(agent):
        _extract_from_tracking_data(agent.tracking_data, metrics, prefix="")

    for attr_name in _STANDARD_METRIC_ATTRS:
        if hasattr(agent, attr_name):
            _extract_from_value(attr_name, getattr(agent, attr_name), metrics)

    if metric_filter:
        metrics = {k: v for k, v in metrics.items() if k in metric_filter}

    return metrics


def create_mlflow_logging_wrapper(
    agent: SkrlAgent,
    mlflow_module: MLflowModule,
    metric_filter: set[str] | None = None,
    collect_gpu_metrics: bool = True,
) -> Callable[[int, int], Any]:
    """Create closure that wraps agent._update with MLflow logging.

    Returns a function that calls the original agent._update method then
    extracts and logs metrics to MLflow.

    Args:
        agent: SKRL agent instance to extract metrics from.
        mlflow_module: MLflow module for logging metrics.
        metric_filter: Optional set of metric names to include.
        collect_gpu_metrics: Enable GPU metrics collection (default: True).

    Returns:
        Closure function suitable for monkey-patching agent._update.

    Raises:
        AttributeError: If agent lacks tracking_data attribute.

    Example:
        >>> wrapper = create_mlflow_logging_wrapper(runner.agent, mlflow)
        >>> runner.agent._update = wrapper
    """
    if not _has_tracking_data(agent):
        raise AttributeError(
            "Agent must have 'tracking_data' attribute for MLflow metric logging. "
            f"Agent type {type(agent).__name__} does not support metric tracking."
        )

    system_metrics_collector = SystemMetricsCollector(
        collect_gpu=collect_gpu_metrics,
        collect_disk=True,
    )
    _LOGGER.debug(
        "System metrics collector initialized (GPU: %s)",
        collect_gpu_metrics,
    )

    original_update = agent._update

    def mlflow_logging_update(timestep: int, timesteps: int) -> Any:
        """Call original _update and log metrics to MLflow."""
        result = original_update(timestep, timesteps)

        try:
            training_metrics = _extract_metrics_from_agent(agent, metric_filter)

            system_metrics = {}
            try:
                system_metrics = system_metrics_collector.collect_metrics()
                _LOGGER.debug(
                    "System metrics collected: %d metrics",
                    len(system_metrics),
                )
            except Exception as exc:
                _LOGGER.debug("System metrics collection failed: %s", exc)

            all_metrics = {**training_metrics, **system_metrics}

            if all_metrics and mlflow_module:
                mlflow_module.log_metrics(all_metrics, step=timestep, synchronous=False)
            elif not all_metrics:
                _LOGGER.debug("No metrics extracted at timestep %d", timestep)
        except Exception as exc:
            _LOGGER.warning("Failed to log metrics at timestep %d: %s", timestep, exc)

        return result

    return mlflow_logging_update
