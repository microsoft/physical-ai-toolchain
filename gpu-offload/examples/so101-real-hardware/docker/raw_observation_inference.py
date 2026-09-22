from __future__ import annotations

import functools
import logging
import os
import time
from collections import defaultdict
from contextlib import nullcontext
from copy import copy
from pathlib import Path
from typing import Any

import numpy as np
import torch
from lerobot.policies import make_pre_post_processors
from lerobot.policies.pretrained import PreTrainedPolicy
from lerobot.policies.utils import make_robot_action
from lerobot.rollout.inference.factory import InferenceEngineConfig, SyncInferenceConfig
from lerobot.rollout.inference.sync import SyncInferenceEngine
from lerobot.rollout.robot_wrapper import ThreadSafeRobot

logger = logging.getLogger(__name__)

_ENABLED_VALUE = "true"
_INSTALLED = False


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    index = (len(sorted_values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = index - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


class RawObservationSyncInferenceEngine(SyncInferenceEngine):
    """Run the synchronous policy pipeline remotely from compact observation tensors."""

    def __init__(
        self,
        policy: PreTrainedPolicy,
        dataset_features: dict[str, dict[str, Any]],
        ordered_action_keys: list[str],
        task: str,
        device: str | None,
        robot_type: str,
    ) -> None:
        self._policy = policy
        self._dataset_features = dataset_features
        self._ordered_action_keys = ordered_action_keys
        self._task = task
        self._device = torch.device(device or "cpu")
        self._robot_type = robot_type
        policy_config = policy.config
        self._preprocessor, self._postprocessor = make_pre_post_processors(
            policy_cfg=policy_config,
            pretrained_path=policy_config.pretrained_path,
            pretrained_revision=policy_config.pretrained_revision,
            dataset_stats=None,
            preprocessor_overrides={
                "device_processor": {"device": device},
                "rename_observations_processor": {"rename_map": {}},
            },
        )
        self._server_timings: dict[str, list[float]] = defaultdict(list)
        logger.info(
            "RawObservationSyncInferenceEngine initialized (device=%s, action_keys=%d)",
            self._device,
            len(ordered_action_keys),
        )

    def reset(self) -> None:
        super().reset()
        self._server_timings.clear()

    def stop(self) -> None:
        for stage in sorted(self._server_timings):
            values_ms = sorted(duration * 1000 for duration in self._server_timings[stage])
            logger.warning(
                "RAW_OBSERVATION_SERVER_TIMING stage=%s calls=%d mean_ms=%.3f p50_ms=%.3f p95_ms=%.3f max_ms=%.3f",
                stage,
                len(values_ms),
                sum(values_ms) / len(values_ms),
                _percentile(values_ms, 0.50),
                _percentile(values_ms, 0.95),
                values_ms[-1],
            )

    def get_action(self, obs_frame: dict[str, torch.Tensor] | None) -> torch.Tensor | None:
        if obs_frame is None:
            return None

        total_started_at = time.perf_counter()
        observation = copy(obs_frame)

        autocast_ctx = (
            torch.autocast(device_type=self._device.type)
            if self._device.type == "cuda" and self._policy.config.use_amp
            else nullcontext()
        )
        with torch.inference_mode(), autocast_ctx:
            started_at = time.perf_counter()
            for name, value in observation.items():
                if not isinstance(value, torch.Tensor):
                    raise TypeError(f"Raw observation value {name!r} must be a torch.Tensor")
                if "image" in name:
                    if value.dtype == torch.uint8:
                        value = value.to(device=self._device, dtype=torch.float32).div_(255)
                    else:
                        value = value.to(self._device)
                    value = value.permute(2, 0, 1).contiguous()
                else:
                    value = value.to(self._device)
                observation[name] = value.unsqueeze(0)
            observation["task"] = self._task or ""
            observation["robot_type"] = self._robot_type or ""
            self._server_timings["prepare_observation"].append(time.perf_counter() - started_at)

            started_at = time.perf_counter()
            observation = self._preprocessor(observation)
            self._server_timings["preprocessor"].append(time.perf_counter() - started_at)

            started_at = time.perf_counter()
            action = self._policy.select_action(observation)
            self._server_timings["policy_select_action"].append(time.perf_counter() - started_at)

            started_at = time.perf_counter()
            action = self._postprocessor(action)
            self._server_timings["postprocessor"].append(time.perf_counter() - started_at)

        action_tensor = action.squeeze(0).cpu()
        action_dict = make_robot_action(action_tensor, self._dataset_features)
        result = torch.tensor([action_dict[key] for key in self._ordered_action_keys])
        self._server_timings["get_action_total"].append(time.perf_counter() - total_started_at)
        return result


def _tensorize_dataset_frame(function: Any) -> Any:
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        frame = function(*args, **kwargs)
        return {
            name: torch.from_numpy(value) if isinstance(value, np.ndarray) else value for name, value in frame.items()
        }

    return wrapper


def _create_inference_engine(
    original: Any,
    config: InferenceEngineConfig,
    **kwargs: Any,
) -> Any:
    if not isinstance(config, SyncInferenceConfig):
        return original(config, **kwargs)

    robot_wrapper = kwargs["robot_wrapper"]
    if not isinstance(robot_wrapper, ThreadSafeRobot):
        raise TypeError("robot_wrapper must be a ThreadSafeRobot")
    return RawObservationSyncInferenceEngine(
        policy=kwargs["policy"],
        dataset_features=kwargs["dataset_features"],
        ordered_action_keys=kwargs["ordered_action_keys"],
        task=kwargs["task"],
        device=kwargs["device"],
        robot_type=robot_wrapper.robot_type,
    )


def install_raw_observation_offload() -> None:
    global _INSTALLED
    if _INSTALLED or os.environ.get("ROLLOUT_RAW_OBSERVATION_OFFLOAD", "false").lower() != _ENABLED_VALUE:
        return

    from lerobot.rollout import context as rollout_context
    from lerobot.rollout.inference import factory as inference_factory
    from lerobot.rollout.strategies import core as strategy_core

    original_create_inference_engine = inference_factory.create_inference_engine
    create_inference_engine = functools.partial(_create_inference_engine, original_create_inference_engine)
    inference_factory.create_inference_engine = create_inference_engine
    rollout_context.create_inference_engine = create_inference_engine
    strategy_core.build_dataset_frame = _tensorize_dataset_frame(strategy_core.build_dataset_frame)
    _INSTALLED = True


def validate_raw_observation_offload(policy_path: str, robot_type: str) -> None:
    from lerobot.configs import FeatureType
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.rollout.context import _load_pretrained_policy

    config = ACTConfig.from_pretrained(policy_path)
    config.pretrained_path = Path(policy_path)
    policy = _load_pretrained_policy(config).to(config.device)
    policy.eval()

    observation = {}
    for name, feature in config.input_features.items():
        shape = tuple(feature.shape)
        if feature.type == FeatureType.VISUAL:
            channels, height, width = shape
            observation[name] = torch.zeros((height, width, channels), dtype=torch.uint8)
        else:
            observation[name] = torch.zeros(shape, dtype=torch.float32)

    action_dimension = config.output_features["action"].shape[0]
    action_keys = getattr(config, "action_feature_names", None) or [
        f"action_{index}" for index in range(action_dimension)
    ]
    dataset_features = {"action": {"names": action_keys}}
    engine = RawObservationSyncInferenceEngine(
        policy=policy,
        dataset_features=dataset_features,
        ordered_action_keys=action_keys,
        task="raw observation validation",
        device=config.device,
        robot_type=robot_type,
    )
    engine.notify_observation({name: value.numpy() for name, value in observation.items()})
    engine.reset()
    action = engine.get_action(observation)
    engine.stop()
    if action is None or tuple(action.shape) != (action_dimension,) or not torch.isfinite(action).all():
        raise RuntimeError("Raw observation validation returned an invalid action")
    print(f"Raw ACT action: shape={tuple(action.shape)}, device={action.device}")


def main() -> None:
    install_raw_observation_offload()

    from lerobot.scripts.lerobot_rollout import main as rollout_main

    rollout_main()


if __name__ == "__main__":
    main()
