---
sidebar_position: 6
title: MLflow Integration for SKRL Training
description: Metric logging integration for SKRL agent training during Isaac Lab runs using monkey-patching
author: Microsoft Robotics-AI Team
ms.date: 2026-02-23
ms.topic: reference
keywords:
  - mlflow
  - skrl
  - metrics
  - experiment tracking
  - isaac lab
---

The training pipeline uses monkey-patching to wrap the agent's `_update` method, intercepting training updates to extract and log metrics to MLflow. This approach provides comprehensive experiment tracking without modifying the underlying SKRL agent implementation or training code.

## Available Metrics

The MLflow integration automatically extracts metrics from SKRL agents across several categories:

### Episode Statistics

* `episode_reward` - Reward for the current episode
* `episode_reward_mean` - Mean reward across recent episodes
* `episode_length` - Length of the current episode
* `episode_length_mean` - Mean episode length across recent episodes
* `cumulative_rewards` - Cumulative rewards over time
* `mean_rewards` - Mean reward values
* `success_rate` - Success rate for task-specific metrics

### Training Losses

* `policy_loss` - Policy network loss
* `value_loss` - Value network loss (critic loss for some algorithms)
* `critic_loss` - Critic network loss (SAC, TD3, DDPG)
* `entropy` - Policy entropy for exploration

### Optimization Metrics

* `learning_rate` - Current learning rate
* `grad_norm` - Gradient norm for monitoring optimization
* `kl_divergence` - KL divergence between old and new policies (PPO)

### Timing Metrics

* `timesteps` - Total environment timesteps
* `iterations` - Training iteration count
* `fps` - Training frames per second
* `epoch_time` - Time per training epoch
* `rollout_time` - Time spent collecting experience
* `learning_time` - Time spent in optimization

### Multi-Element Metrics

For metrics with multiple values (tensors or arrays), the integration extracts statistical aggregates:

* `metric_name/mean` - Mean value
* `metric_name/std` - Standard deviation
* `metric_name/min` - Minimum value
* `metric_name/max` - Maximum value

### Custom Metrics

All entries in `agent.tracking_data` are automatically extracted, supporting algorithm-specific metrics from PPO, SAC, TD3, DDPG, A2C, and other SKRL implementations.

## Implementation Details

The integration uses the `create_mlflow_logging_wrapper` function from `skrl_mlflow_agent` module to create a closure that wraps the agent's `_update` method. The wrapper is applied after the SKRL Runner is instantiated but before training begins.

### Configuration Parameters

* `agent` - The SKRL agent instance to extract metrics from (required)
* `mlflow_module` - The mlflow module for logging metrics (required)
* `metric_filter` - Optional set of metric names to log (default: None)
  * When None, all available metrics are logged
  * Use a set of strings to only log specific metrics
  * Useful for reducing MLflow API load in production environments

### Logging Interval

The MLflow logging interval is controlled via the `--mlflow_log_interval` CLI argument:

* `step` - Log metrics after every training step (most frequent)
* `balanced` - Log metrics every 10 steps (default, recommended)
* `rollout` - Log metrics once per rollout cycle
* Integer value - Custom interval in steps

### Metric Filtering Examples

To customize which metrics are logged, modify the `create_mlflow_logging_wrapper` call in `skrl_training.py`:

```python
from training.rl.scripts.skrl_mlflow_agent import create_mlflow_logging_wrapper

basic_metrics = {
    "episode_reward_mean",
    "episode_length_mean",
    "policy_loss",
    "value_loss",
}

wrapper_func = create_mlflow_logging_wrapper(
    agent=runner.agent,
    mlflow_module=mlflow,
    metric_filter=basic_metrics,
)
runner.agent._update = wrapper_func
```

```python
optimization_metrics = {
    "learning_rate",
    "grad_norm",
    "kl_divergence",
    "policy_loss",
    "value_loss",
}

wrapper_func = create_mlflow_logging_wrapper(
    agent=runner.agent,
    mlflow_module=mlflow,
    metric_filter=optimization_metrics,
)
runner.agent._update = wrapper_func
```

## Usage Examples

### Integration with SKRL Training

The monkey-patching approach is applied after creating the SKRL Runner:

```python
import mlflow
from skrl.utils.runner.torch import Runner
from training.rl.scripts.skrl_mlflow_agent import create_mlflow_logging_wrapper

mlflow.set_tracking_uri("azureml://...")
mlflow.set_experiment("isaaclab-training")

with mlflow.start_run():
    runner = Runner(env, agent_cfg)

    wrapper_func = create_mlflow_logging_wrapper(
        agent=runner.agent,
        mlflow_module=mlflow,
        metric_filter=None,
    )

    runner.agent._update = wrapper_func
    runner.run()
```

### Configuring Logging Intervals

Use CLI arguments to control logging frequency:

```bash
# Log after every training step
python training/rl/scripts/skrl_training.py --mlflow_log_interval step

# Log every 10 steps (default)
python training/rl/scripts/skrl_training.py --mlflow_log_interval balanced

# Log once per rollout
python training/rl/scripts/skrl_training.py --mlflow_log_interval rollout

# Log every 100 steps
python training/rl/scripts/skrl_training.py --mlflow_log_interval 100
```

### Filtering Metrics for Production

Modify the wrapper creation in `skrl_training.py`:

```python
production_metrics = {
    "episode_reward_mean",
    "episode_length_mean",
    "success_rate",
}

wrapper_func = create_mlflow_logging_wrapper(
    agent=runner.agent,
    mlflow_module=mlflow,
    metric_filter=production_metrics,
)
runner.agent._update = wrapper_func
```

## Integration with Isaac Lab

The MLflow integration is automatically applied in `skrl_training.py` when training with Isaac Lab tasks:

```bash
python training/rl/scripts/skrl_training.py \
    --task Isaac-Cartpole-v0 \
    --num_envs 512 \
    --headless
```

The training script handles MLflow setup and monkey-patching automatically. To customize the logging interval, use the `--mlflow_log_interval` argument. To customize metric filtering, modify the `create_mlflow_logging_wrapper` call in `skrl_training.py`.

## Troubleshooting

### No Metrics Logged to MLflow

Training runs complete but no metrics appear in MLflow.

1. **MLflow not configured** - Verify `mlflow.set_tracking_uri()` is called with the correct Azure ML workspace URI and authentication is valid.
2. **Monkey-patching not applied** - Ensure `create_mlflow_logging_wrapper` is called after Runner instantiation and `runner.agent._update` is replaced before `runner.run()`.
3. **Short training runs** - Training updates occur after rollouts complete. Very short runs may finish before metrics are captured.
4. **Empty tracking data** - Agent `tracking_data` may not populate until after the first rollout. If using `metric_filter`, verify the filter set contains matching metric names.

### Missing Specific Metrics

Some expected metrics are not logged while others are.

1. **Algorithm differences** - Different SKRL algorithms expose different metrics. Check `agent.tracking_data` for available entries.
2. **Metric filtered** - If using `metric_filter`, ensure metric names match exactly (case-sensitive).
3. **Extraction failure** - Check logs for metric extraction warnings. Some metrics may have incompatible types.

### AttributeError on Agent

`AttributeError: Agent must have 'tracking_data' attribute`

1. **Incompatible agent** - Ensure the agent is a SKRL agent with `tracking_data`. Verify SKRL version compatibility.
2. **Timing** - Apply monkey-patch after Runner instantiation. Verify `runner.agent` and `runner.agent._update` exist before replacement.

### High MLflow API Load

Training slows down due to excessive MLflow API calls.

1. Increase logging interval with `--mlflow_log_interval 100` or higher.
2. Use `metric_filter` to log only essential metrics.
3. The integration already batches metrics per training update. Enable asynchronous MLflow logging if available.

### Metric Extraction Warnings

Log messages like `"Failed to extract or log metrics at step X"` indicate transient data structure changes or incompatible metric types. Occasional warnings are harmless. For persistent warnings, check the exception details and modify `_extract_from_value()` in `skrl_mlflow_agent.py` for specific metric types.

**Possible Causes:**

1. Transient data structure changes
   * Some algorithms modify `tracking_data` structure during training
   * Usually harmless if only occasional warnings appear

2. Incompatible metric types
   * The integration attempts to convert all metrics to float
   * Some complex objects cannot be converted and are skipped

**Solutions:**

1. Check warning details in logs
   * Warnings include the exception message for debugging
   * Determine if the failed metric is critical

2. Add custom extraction logic
   * Modify `_extract_from_value()` in `skrl_mlflow_agent.py` for specific metric types
   * Contribute improvements back to the integration module

### Empty Metrics Dictionary

**Symptom:** Integration runs but extracts zero metrics.

**Possible Causes:**

1. Agent `tracking_data` is empty
   * Agent may not have started tracking yet
   * Training updates occur after rollouts, not after every environment step
   * Check agent initialization and training state

2. All metrics filtered out
   * If using `metric_filter` with no matching metric names
   * Verify filter set contains correct metric names

3. Metric extraction depth exceeded
   * Nested metrics beyond `max_depth=2` are not extracted
   * Increase `max_depth` in `_extract_from_tracking_data()` if needed

## Related Documentation

* [Training Guide](README.md)
* [Workflow Templates](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/README.md)

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
