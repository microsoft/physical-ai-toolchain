"""CPU-only backend contracts with synthetic framework boundaries.

No framework runtime, checkpoint weights, or native normalization is exercised.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import pytest
from sil.vla import backends
from sil.vla.config import EvaluationConfig, VectorMapping, load_config

_FRAMEWORKS = {"rho", "openpi", "jax", "torch", "isaaclab", "isaacsim", "omni"}
_OFFLINE_KEYS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block real frameworks; individual tests install only explicit synthetic modules."""
    names = _FRAMEWORKS | {name for name in sys.modules if name.split(".")[0] in _FRAMEWORKS}
    for name in names:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", None)
    monkeypatch.setenv("XLA_PYTHON_CLIENT_PREALLOCATE", "fixture-original")


def _modules(monkeypatch: pytest.MonkeyPatch, *names: str) -> dict[str, ModuleType]:
    result = {}
    for name in sorted(names, key=lambda value: value.count(".")):
        module = ModuleType(name)
        module.__file__ = None
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
        parent, _, attribute = name.rpartition(".")
        if parent and isinstance(sys.modules.get(parent), ModuleType):
            monkeypatch.setattr(sys.modules[parent], attribute, module, raising=False)
        result[name] = module
    return result


@pytest.fixture
def config(tmp_path: Path) -> EvaluationConfig:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    source = {
        "schema_version": 1,
        "instruction": "Move the observed part into the tray.",
        "channels": ["shoulder", "wrist", "gripper"],
        "image_shapes": {"front": [2, 4, 3], "wrist": [4, 2, 3]},
        "policy": {
            "backend": "openpi",
            "checkpoint": str(checkpoint),
            "checkpoint_sha256": "a" * 64,
            "training_config": "synthetic_registered_config",
            "state_key": "observation.state",
            "prompt_key": "task",
            "image_keys": {"front": "observation.image.0", "wrist": "observation.image.1"},
            "state_mapping": {"indices": [2, 0, 1], "scale": [2, -1, 0.5], "offset": [1, 4, -2]},
            "action_mapping": {"indices": [2, 0, 1], "scale": [2, -1, 0.5], "offset": [1, 0.25, -0.125]},
            "delta_indices": [0, 2],
            "action_horizon": 3,
        },
        "task": {
            "adapter": "ur10e_gear",
            "producer_root": "owners",
            "expert_config": "expert.json",
            "stage": "assets/stage.usda",
            "reference_root": "assets",
            "runtime_assets": [],
        },
        "evaluation": {
            "control_hz": 30,
            "execution_horizon": 2,
            "seeds": {"test": [11, 23]},
            "max_steps": 4,
            "timeout_seconds": 5,
            "minimum_free_gib": 0,
            "joint_limit_behavior": "reject",
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    return load_config(path)


@pytest.fixture
def observation(config: EvaluationConfig) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    return np.array([10, 20, 30], dtype=np.float64), {
        name: np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)[:, ::-1]
        for name, shape in config.image_shapes.items()
    }


class TestBackendArrays:
    def test_observation_maps_measured_channels_and_copies_only_declared_inputs(
        self, config: EvaluationConfig, observation: tuple[np.ndarray, dict[str, np.ndarray]]
    ) -> None:
        state, images = observation

        result = backends._observation(config, state, images, seed=11, reset=True)

        assert set(result) == {"observation.state", "task", "observation.image.0", "observation.image.1"}
        assert result["task"] == config.instruction
        np.testing.assert_array_equal(result["observation.state"], [61, -6, 8])
        assert result["observation.state"].dtype == np.float32
        assert not np.shares_memory(result["observation.state"], state)
        for camera, key in config.policy.image_keys.items():
            np.testing.assert_array_equal(result[key], images[camera])
            assert result[key].dtype == np.uint8 and result[key].flags.c_contiguous
            assert not np.shares_memory(result[key], images[camera])

    @pytest.mark.parametrize(
        ("seed", "reset"),
        [(True, True), (-1, False), (2**32, False), (1.0, False), (np.int64(1), False), (1, 1), (1, None)],
    )
    def test_seed_and_reset_require_exact_protocol_types(
        self,
        config: EvaluationConfig,
        observation: tuple[np.ndarray, dict[str, np.ndarray]],
        seed: Any,
        reset: Any,
    ) -> None:
        with pytest.raises(ValueError, match=r"Policy seed|Policy reset"):
            backends._observation(config, *observation, seed=seed, reset=reset)

    @pytest.mark.parametrize("seed", [0, 2**32 - 1])
    def test_seed_boundaries_do_not_add_seed_or_reset_to_model_inputs(
        self, config: EvaluationConfig, observation: tuple[np.ndarray, dict[str, np.ndarray]], seed: int
    ) -> None:
        result = backends._observation(config, *observation, seed=seed, reset=False)

        assert "seed" not in result and "reset" not in result and "_reset_" not in result

    @pytest.mark.parametrize("state", [np.zeros(2), np.zeros((1, 3)), np.array([0, np.nan, 0])])
    def test_observation_checks_raw_shape_and_finiteness_before_mapping(
        self, config: EvaluationConfig, observation: tuple[np.ndarray, dict[str, np.ndarray]], state: np.ndarray
    ) -> None:
        with pytest.raises(ValueError, match="Measured state"):
            backends._observation(config, state, observation[1], seed=11, reset=False)

    def test_float32_mapping_overflow_is_rejected_even_when_float64_is_finite(
        self, config: EvaluationConfig, observation: tuple[np.ndarray, dict[str, np.ndarray]]
    ) -> None:
        mapping = VectorMapping((0, 1, 2), (1e38, 1, 1), (0, 0, 0))
        config = replace(config, policy=replace(config.policy, state_mapping=mapping))

        with pytest.raises(ValueError, match="Mapped model state exceeds finite float32 range"):
            backends._observation(config, *observation, seed=11, reset=False)

    def test_native_actions_are_owned_contiguous_float32_without_generic_decoding(self) -> None:
        value = np.arange(12, dtype=np.int32).reshape(3, 4)[:, ::-1]

        result = backends._actions(value, 3, 4)

        np.testing.assert_array_equal(result, value)
        assert result.dtype == np.float32 and result.flags.c_contiguous
        assert not np.shares_memory(value, result)

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(np.zeros(3), id="rank-one"),
            pytest.param(np.zeros((1, 3, 3)), id="rank-three"),
            pytest.param(np.zeros((2, 3)), id="short-horizon"),
            pytest.param(np.zeros((4, 3)), id="long-horizon"),
            pytest.param(np.zeros((3, 0)), id="empty-width"),
            pytest.param(np.ones((3, 3), dtype=bool), id="bool"),
            pytest.param(np.ones((3, 3), dtype=object), id="object"),
            pytest.param(np.full((3, 3), "1"), id="text"),
            pytest.param(np.ones((3, 3), dtype=complex), id="complex"),
            pytest.param(np.full((3, 3), np.nan), id="nan"),
            pytest.param(np.full((3, 3), np.inf), id="infinity"),
            pytest.param(np.full((3, 3), 1e39), id="float32-overflow"),
        ],
    )
    def test_native_action_arrays_reject_invalid_shapes_types_and_values(self, value: np.ndarray) -> None:
        with pytest.raises(ValueError, match="Native policy"):
            backends._actions(value, 3)

    def test_checkpoint_width_is_enforced_without_padding_or_truncation(self) -> None:
        with pytest.raises(ValueError, match="width differs from checkpoint features"):
            backends._actions(np.zeros((3, 4)), 3, 3)


class TestBackendDispatch:
    def test_module_import_does_not_load_any_framework(self) -> None:
        spec = importlib.util.spec_from_file_location("sil.vla._backend_import_fixture", backends.__file__)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)

        spec.loader.exec_module(module)

        assert callable(module.load_backend)
        assert all(sys.modules[name] is None for name in _FRAMEWORKS)

    @pytest.mark.parametrize("backend", ["rho", "openpi"])
    def test_only_selected_factory_receives_exact_configuration_and_device(
        self, config: EvaluationConfig, monkeypatch: pytest.MonkeyPatch, backend: str
    ) -> None:
        config = replace(config, policy=replace(config.policy, backend=backend))
        calls = []
        result = object()

        def factory(selected: EvaluationConfig, device: str) -> object:
            assert selected is config and device == "cpu"
            assert all(os.environ[key] == "1" for key in _OFFLINE_KEYS)
            calls.append(backend)
            return result

        def forbidden(*args: Any, **kwargs: Any) -> None:
            pytest.fail("Unselected backend was initialized")

        monkeypatch.setattr(backends, "_RhoBackend", factory if backend == "rho" else forbidden)
        monkeypatch.setattr(backends, "_OpenPIBackend", factory if backend == "openpi" else forbidden)

        assert backends.load_backend(config, "cpu") is result
        assert calls == [backend]

    @pytest.mark.parametrize(
        ("case", "message"),
        [
            ("backend", "Supported policy backends"),
            ("device", "Specify a native policy device"),
            ("directory", "existing local directory"),
            ("training-config", "registered training_config"),
            ("camera", "Map every declared camera"),
            ("alias", "must not alias"),
        ],
    )
    def test_invalid_dispatch_inputs_fail_before_a_factory_is_called(
        self, config: EvaluationConfig, monkeypatch: pytest.MonkeyPatch, case: str, message: str
    ) -> None:
        changes = {
            "backend": {"backend": "arbitrary:factory"},
            "directory": {"checkpoint": config.policy.checkpoint / "missing"},
            "training-config": {"training_config": " "},
            "camera": {"image_keys": {"front": "observation.image.0"}},
            "alias": {"prompt_key": config.policy.state_key},
        }
        config = replace(config, policy=replace(config.policy, **changes.get(case, {})))

        def forbidden(*args: Any, **kwargs: Any) -> None:
            pytest.fail("Invalid dispatch reached a backend factory")

        monkeypatch.setattr(backends, "_RhoBackend", forbidden)
        monkeypatch.setattr(backends, "_OpenPIBackend", forbidden)

        with pytest.raises(ValueError, match=message):
            backends.load_backend(config, " " if case == "device" else "cpu")


class TestOfflineScope:
    @pytest.mark.parametrize("previous", [None, "", "0", "operator-value"])
    @pytest.mark.parametrize("fail", [False, True])
    def test_nested_scope_restores_absent_and_existing_environment_on_every_exit(
        self, monkeypatch: pytest.MonkeyPatch, previous: str | None, fail: bool
    ) -> None:
        for key in _OFFLINE_KEYS:
            if previous is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, previous)

        def invoke() -> None:
            with backends._offline_runtime():
                assert all(os.environ[key] == "1" for key in _OFFLINE_KEYS)
                with backends._offline_runtime():
                    assert all(os.environ[key] == "1" for key in _OFFLINE_KEYS)
                assert all(os.environ[key] == "1" for key in _OFFLINE_KEYS)
                if fail:
                    raise RuntimeError("synthetic callback failure")

        if fail:
            with pytest.raises(RuntimeError, match="synthetic callback failure"):
                invoke()
        else:
            invoke()

        assert {key: os.environ.get(key) for key in _OFFLINE_KEYS} == dict.fromkeys(_OFFLINE_KEYS, previous)

    def test_cached_online_hub_constant_cannot_be_overridden_by_offline_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hub = _modules(monkeypatch, "huggingface_hub", "huggingface_hub.constants")
        hub["huggingface_hub.constants"].HF_HUB_OFFLINE = False
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

        with pytest.raises(ValueError, match="Hugging Face was imported online"), backends._offline_runtime():
            pytest.fail("Cached online settings reached the callback")

        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert "TRANSFORMERS_OFFLINE" not in os.environ
        assert hub["huggingface_hub.constants"].HF_HUB_OFFLINE is False

    def test_cached_offline_hub_constant_allows_the_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        hub = _modules(monkeypatch, "huggingface_hub", "huggingface_hub.constants")
        hub["huggingface_hub.constants"].HF_HUB_OFFLINE = True

        with backends._offline_runtime():
            assert all(os.environ[key] == "1" for key in _OFFLINE_KEYS)


class TestCachedOpenPIAssets:
    def test_local_and_precached_assets_are_resolved_without_creation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = tmp_path / "cache"
        asset = cache / "bucket" / "assets" / "stats.json"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"synthetic asset")
        monkeypatch.setenv("OPENPI_DATA_HOME", str(cache))

        assert backends._cached_openpi_asset(str(asset)) == asset
        assert backends._cached_openpi_asset("gs://bucket/assets/stats.json") == asset
        assert backends._cached_openpi_asset("https://bucket/assets/stats.json") == asset
        assert asset.read_bytes() == b"synthetic asset"

    @pytest.mark.parametrize(
        "reference",
        ["s3://bucket/asset", "https://bucket/asset?token=fixture", "gs://bucket/asset#fragment", "gs:///asset"],
    )
    def test_unsupported_remote_references_are_rejected(self, reference: str) -> None:
        with pytest.raises(ValueError, match="Unsupported OpenPI asset reference"):
            backends._cached_openpi_asset(reference)

    def test_missing_cache_and_cache_escape_do_not_download_or_create_directories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = tmp_path / "missing-cache"
        monkeypatch.setenv("OPENPI_DATA_HOME", str(cache))

        with pytest.raises(FileNotFoundError, match="downloads are disabled"):
            backends._cached_openpi_asset("gs://bucket/missing")
        with pytest.raises(ValueError, match="escapes its cache"):
            backends._cached_openpi_asset("gs://bucket/../../outside")
        with pytest.raises(ValueError, match="forced downloads are disabled"):
            backends._cached_openpi_asset(str(tmp_path), force_download=True)

        assert not cache.exists()


@pytest.fixture
def openpi_runtime(config: EvaluationConfig, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    modules = _modules(
        monkeypatch,
        "openpi",
        "openpi.shared",
        "openpi.shared.download",
        "openpi.policies",
        "openpi.policies.policy_config",
        "openpi.training",
        "openpi.training.config",
        "jax",
        "torch",
    )
    runtime = SimpleNamespace(
        native_config=SimpleNamespace(model=SimpleNamespace(action_horizon=3, action_dim=4)),
        training_calls=[],
        create_calls=[],
        device_calls=[],
        visible=[SimpleNamespace(platform="cpu")],
        observations=[],
        resets=0,
        callback=None,
        load_error=None,
        result={"actions": np.arange(12, dtype=np.float64).reshape(3, 4)},
        download=modules["openpi.shared.download"],
    )

    class Policy:
        def reset(self) -> None:
            runtime.resets += 1

        def infer(self, observation: dict[str, Any]) -> Any:
            assert all(os.environ[key] == "1" for key in _OFFLINE_KEYS)
            assert runtime.download.maybe_download is backends._cached_openpi_asset
            runtime.observations.append(observation)
            if runtime.callback is not None:
                runtime.callback(observation)
            return runtime.result

    def download_forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("An unscoped native downloader was called")

    def get_config(name: str) -> SimpleNamespace:
        runtime.training_calls.append(name)
        return runtime.native_config

    def create_policy(*args: Any, **kwargs: Any) -> Policy:
        assert all(os.environ[key] == "1" for key in _OFFLINE_KEYS)
        assert runtime.download.maybe_download is backends._cached_openpi_asset
        assert runtime.download.maybe_download(str(args[1])) == config.policy.checkpoint
        runtime.create_calls.append((args, kwargs))
        if runtime.load_error is not None:
            raise runtime.load_error
        return runtime.policy

    def devices() -> list[Any]:
        runtime.device_calls.append(True)
        return runtime.visible

    runtime.policy = Policy()
    runtime.original_download = download_forbidden
    runtime.download.maybe_download = download_forbidden
    modules["openpi.training.config"].get_config = get_config
    modules["openpi.policies.policy_config"].create_trained_policy = create_policy
    modules["jax"].devices = devices
    (config.policy.checkpoint / "params").mkdir()
    return runtime


class TestOpenPINativeBoundary:
    @pytest.mark.parametrize("checkpoint_format", ["jax", "pytorch"])
    def test_registered_training_config_and_exact_local_checkpoint_reach_native_loader(
        self, config: EvaluationConfig, openpi_runtime: SimpleNamespace, checkpoint_format: str
    ) -> None:
        if checkpoint_format == "pytorch":
            (config.policy.checkpoint / "model.safetensors").write_bytes(b"synthetic format marker, not weights")

        backend = backends.load_backend(config, "cpu")

        expected_kwargs = {"pytorch_device": "cpu"} if checkpoint_format == "pytorch" else {}
        assert openpi_runtime.training_calls == [config.policy.training_config]
        assert openpi_runtime.create_calls == [
            ((openpi_runtime.native_config, config.policy.checkpoint), expected_kwargs)
        ]
        assert openpi_runtime.device_calls == ([] if checkpoint_format == "pytorch" else [True])
        assert openpi_runtime.download.maybe_download is openpi_runtime.original_download
        assert backend.provenance["checkpoint_format"] == checkpoint_format
        assert backend.provenance["normalization_source"] == "native_checkpoint_assets"
        assert backend.provenance["generic_action_decoding"] == "server_only"
        assert sys.modules["rho"] is None

    def test_action_horizon_mismatch_fails_before_device_query_or_weights_callback(
        self, config: EvaluationConfig, openpi_runtime: SimpleNamespace
    ) -> None:
        openpi_runtime.native_config.model.action_horizon = 4

        with pytest.raises(ValueError, match="native model action horizon differs"):
            backends.load_backend(config, "cpu")

        assert not openpi_runtime.create_calls and not openpi_runtime.device_calls
        assert openpi_runtime.download.maybe_download is openpi_runtime.original_download

    def test_missing_native_params_fails_before_device_query_or_weights_callback(
        self, config: EvaluationConfig, openpi_runtime: SimpleNamespace
    ) -> None:
        (config.policy.checkpoint / "params").rmdir()

        with pytest.raises(ValueError, match="no native params directory"):
            backends.load_backend(config, "cpu")

        assert not openpi_runtime.create_calls and not openpi_runtime.device_calls

    @pytest.mark.parametrize(
        ("platforms", "device", "message"),
        [
            ([], "cpu", "expose exactly one device"),
            (["cpu", "cpu"], "cpu", "expose exactly one device"),
            (["gpu", "gpu"], "cuda", "expose exactly one device"),
            (["gpu"], "cpu", "device differs from the request"),
            (["cpu"], "cuda:0", "device differs from the request"),
            (["gpu"], "cuda:1", "device differs from the request"),
            (["cpu"], "invalid", "device differs from the request"),
        ],
    )
    def test_fake_jax_visibility_is_checked_before_the_native_loader(
        self,
        config: EvaluationConfig,
        openpi_runtime: SimpleNamespace,
        platforms: list[str],
        device: str,
        message: str,
    ) -> None:
        openpi_runtime.visible = [SimpleNamespace(platform=platform) for platform in platforms]

        with pytest.raises(ValueError, match=message):
            backends.load_backend(config, device)

        assert openpi_runtime.device_calls == [True] and not openpi_runtime.create_calls
        assert openpi_runtime.download.maybe_download is openpi_runtime.original_download

    @pytest.mark.parametrize(("platform", "device"), [("cpu", "cpu:0"), ("gpu", "cuda"), ("gpu", "cuda:0")])
    def test_single_matching_fake_device_is_recorded_without_a_loader_device_override(
        self, config: EvaluationConfig, openpi_runtime: SimpleNamespace, platform: str, device: str
    ) -> None:
        openpi_runtime.visible = [SimpleNamespace(platform=platform)]

        backend = backends.load_backend(config, device)

        assert openpi_runtime.create_calls == [((openpi_runtime.native_config, config.policy.checkpoint), {})]
        assert backend.provenance["jax_devices"] == [str(openpi_runtime.visible[0])]
        assert backend.provenance["jax_preimported"] is True

    def test_native_actions_and_resets_do_not_apply_rho_normalization_or_generic_deltas(
        self,
        config: EvaluationConfig,
        observation: tuple[np.ndarray, dict[str, np.ndarray]],
        openpi_runtime: SimpleNamespace,
    ) -> None:
        backend = backends.load_backend(config, "cpu")
        native = openpi_runtime.result["actions"]

        results = [
            backend.infer(*observation, seed=11, reset=False),
            backend.infer(*observation, seed=11, reset=False),
            backend.infer(*observation, seed=23, reset=True),
        ]

        assert openpi_runtime.resets == 2 and len(openpi_runtime.observations) == 3
        for result in results:
            np.testing.assert_array_equal(result, native)
            assert result.shape == (3, 4) and result.dtype == np.float32
            assert not np.shares_memory(result, native)
        assert sys.modules["rho"] is None
        assert openpi_runtime.download.maybe_download is openpi_runtime.original_download

    def test_mutating_native_callback_cannot_change_caller_measurements_or_configuration(
        self,
        config: EvaluationConfig,
        observation: tuple[np.ndarray, dict[str, np.ndarray]],
        openpi_runtime: SimpleNamespace,
    ) -> None:
        state, images = observation
        original_images = {key: value.copy() for key, value in images.items()}
        source = deepcopy(config.source)

        def mutate(value: dict[str, Any]) -> None:
            np.testing.assert_array_equal(value[config.policy.state_key], [61, -6, 8])
            value[config.policy.state_key].fill(-999)
            for key in config.policy.image_keys.values():
                value[key].fill(0)
            value[config.policy.prompt_key] = "changed synthetic prompt"

        openpi_runtime.callback = mutate
        backend = backends.load_backend(config, "cpu")

        backend.infer(state, images, seed=11, reset=True)

        np.testing.assert_array_equal(state, [10, 20, 30])
        for key, value in images.items():
            np.testing.assert_array_equal(value, original_images[key])
        assert config.source == source and config.instruction == source["instruction"]

    @pytest.mark.parametrize(
        "result",
        [None, np.zeros((3, 4)), {}, {"actions": np.zeros((2, 4))}, {"actions": np.full((3, 4), np.nan)}],
    )
    def test_invalid_native_results_do_not_consume_the_first_observation_reset(
        self,
        config: EvaluationConfig,
        observation: tuple[np.ndarray, dict[str, np.ndarray]],
        openpi_runtime: SimpleNamespace,
        result: Any,
    ) -> None:
        backend = backends.load_backend(config, "cpu")
        openpi_runtime.result = result

        with pytest.raises(ValueError):
            backend.infer(*observation, seed=11, reset=False)

        assert backend._first_observation is True
        assert openpi_runtime.download.maybe_download is openpi_runtime.original_download
        openpi_runtime.result = {"actions": np.zeros((3, 4))}
        backend.infer(*observation, seed=11, reset=False)
        assert openpi_runtime.resets == 2 and backend._first_observation is False

    @pytest.mark.parametrize("phase", ["load", "infer"])
    def test_native_callback_failure_restores_downloader_and_offline_environment(
        self,
        config: EvaluationConfig,
        observation: tuple[np.ndarray, dict[str, np.ndarray]],
        openpi_runtime: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        phase: str,
    ) -> None:
        monkeypatch.setenv("HF_HUB_OFFLINE", "operator-value")
        monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

        def fail(value: dict[str, Any]) -> None:
            raise RuntimeError("synthetic native callback failure")

        if phase == "load":
            openpi_runtime.load_error = RuntimeError("synthetic native callback failure")
            with pytest.raises(RuntimeError, match="synthetic native callback failure"):
                backends.load_backend(config, "cpu")
        else:
            backend = backends.load_backend(config, "cpu")
            openpi_runtime.callback = fail
            with pytest.raises(RuntimeError, match="synthetic native callback failure"):
                backend.infer(*observation, seed=11, reset=False)

        assert openpi_runtime.download.maybe_download is openpi_runtime.original_download
        assert os.environ["HF_HUB_OFFLINE"] == "operator-value"
        assert "TRANSFORMERS_OFFLINE" not in os.environ


class _FeatureType(Enum):
    STATE = "STATE"
    ACTION = "ACTION"
    VISUAL = "VISUAL"


class _NormalizationMode(Enum):
    IDENTITY = "IDENTITY"
    MEAN_STD = "MEAN_STD"
    MIN_MAX = "MIN_MAX"
    QUANTILE = "QUANTILE"
    UNSUPPORTED = "UNSUPPORTED"


class _ActionType(Enum):
    POSITION = "POSITION"
    VELOCITY = "VELOCITY"


@pytest.fixture
def rho_contract(config: EvaluationConfig, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    config = replace(config, policy=replace(config.policy, backend="rho", training_config=None, delta_indices=()))
    modules = _modules(
        monkeypatch, "rho", "rho.common", "rho.common.constants", "rho.common.types", "rho.common.transforms"
    )
    constants = modules["rho.common.constants"]
    constants.ACTION, constants.OBSERVATION_STATE = "action", "observation.state"
    constants.OBSERVATION_LANG, constants.OBSERVATION_IMAGE = "task", "observation.image"
    types = modules["rho.common.types"]
    types.FeatureType, types.NormalizationMode, types.ActionType = _FeatureType, _NormalizationMode, _ActionType
    transforms = modules["rho.common.transforms"]
    for name in (
        "DeltaActions",
        "CenterCrop",
        "ResizeWithPadding",
        "RandomResizedCrop",
        "ColorJitter",
        "RandomFlipLeftRight",
        "RandomFlipUpDown",
        "RandomRot90",
        "SynchronizedRandomCrop",
    ):
        setattr(transforms, name, type(name, (SimpleNamespace,), {}))
    features = {
        "action": SimpleNamespace(type=_FeatureType.ACTION, shape=(3,)),
        "observation.state": SimpleNamespace(type=_FeatureType.STATE, shape=(3,)),
        **{
            key: SimpleNamespace(type=_FeatureType.VISUAL, shape=(3, 2, 4)) for key in config.policy.image_keys.values()
        },
    }
    dataset = SimpleNamespace(
        action_type=_ActionType.POSITION,
        transformed_features=deepcopy(features),
        normalization_mapping={_FeatureType.STATE: _NormalizationMode.IDENTITY},
        transform_mapping=None,
        stats=None,
        transformed_stats=None,
    )
    policy = SimpleNamespace(
        n_obs_steps=1,
        chunk_size=3,
        feature_dict=features,
        image_features={key: features[key] for key in config.policy.image_keys.values()},
        action_feature=features["action"],
        delta_indices_dict={key: list(range(3)) if key == "action" else [0] for key in features},
    )
    delta = transforms.DeltaActions(
        action_type=_ActionType.POSITION,
        relative_to_state=True,
        state_key="observation.state",
        action_key="action",
        absolute_idx=[2],
        use_absolute_grippers=True,
        post_norm=False,
    )
    return SimpleNamespace(
        config=config,
        cfg=SimpleNamespace(policy=policy, dataset=dataset, device="cpu"),
        delta=delta,
        transforms=transforms,
    )


class TestRhoCheckpointContract:
    @pytest.mark.parametrize("relative", [False, True])
    def test_absolute_and_native_position_delta_contracts_are_supported(
        self, rho_contract: SimpleNamespace, relative: bool
    ) -> None:
        if relative:
            rho_contract.cfg.dataset.transform_mapping = {"action": [rho_contract.delta]}

        width, delta = backends._rho_contract(rho_contract.cfg, rho_contract.config)

        assert width == 3 and delta is (rho_contract.delta if relative else None)
        assert sys.modules["torch"] is None

    @pytest.mark.parametrize(
        ("case", "message"),
        [
            ("state-key", "native policies require"),
            ("prompt-key", "native policies require"),
            ("history", "observation history"),
            ("horizon", "action horizon differs"),
            ("action-type", "POSITION action semantics"),
            ("normalization", "Missing Rho normalization mapping"),
            ("policy-key", "features differ"),
            ("dataset-key", "features differ"),
            ("oracle-key", "features differ"),
            ("camera-keys", "native image keys differ"),
            ("state-shape", "STATE differs"),
            ("state-type", "STATE differs"),
            ("action-shape", "ACTION feature definitions disagree"),
            ("empty-action", "ACTION must be a nonempty vector"),
            ("boolean-width", "ACTION must be a nonempty vector"),
            ("camera-shape", "channel-first RGB"),
            ("camera-type", "channel-first RGB"),
            ("missing-temporal", "Missing Rho observation temporal contract"),
            ("state-history", "only the current observation"),
            ("camera-history", "only the current observation"),
        ],
    )
    def test_unsupported_checkpoint_metadata_is_rejected(
        self, rho_contract: SimpleNamespace, case: str, message: str
    ) -> None:
        cfg, config = rho_contract.cfg, rho_contract.config
        if case in {"state-key", "prompt-key"}:
            field = case.replace("-", "_")
            config = replace(config, policy=replace(config.policy, **{field: "other"}))
        elif case == "history":
            cfg.policy.n_obs_steps = 2
        elif case == "horizon":
            cfg.policy.chunk_size = 4
        elif case == "action-type":
            cfg.dataset.action_type = _ActionType.VELOCITY
        elif case == "normalization":
            cfg.dataset.normalization_mapping = None
        elif case == "policy-key":
            cfg.policy.feature_dict.pop("observation.image.0")
        elif case == "dataset-key":
            cfg.dataset.transformed_features.pop("observation.image.0")
        elif case == "oracle-key":
            cfg.policy.feature_dict["oracle.pose"] = cfg.policy.action_feature
        elif case == "camera-keys":
            cfg.policy.image_features = {}
        elif case == "state-shape":
            cfg.dataset.transformed_features["observation.state"].shape = (1, 3)
        elif case == "state-type":
            cfg.policy.feature_dict["observation.state"].type = _FeatureType.ACTION
        elif case == "action-shape":
            cfg.dataset.transformed_features["action"].shape = (4,)
        elif case in {"empty-action", "boolean-width"}:
            cfg.policy.action_feature.shape = () if case == "empty-action" else (True,)
        elif case == "camera-shape":
            cfg.policy.feature_dict["observation.image.0"].shape = (2, 4, 3)
        elif case == "camera-type":
            cfg.dataset.transformed_features["observation.image.0"].type = _FeatureType.STATE
        elif case == "missing-temporal":
            cfg.policy.delta_indices_dict = None
        elif case == "state-history":
            cfg.policy.delta_indices_dict["observation.state"] = [-1, 0]
        else:
            cfg.policy.delta_indices_dict.pop("observation.image.0")

        with pytest.raises(ValueError, match=message):
            backends._rho_contract(cfg, config)

    @pytest.mark.parametrize("field", ["stats", "transformed_stats"])
    @pytest.mark.parametrize("value", [0.0, [0.0], [[0.0, 0.0, 0.0]], [0.0, np.nan, 0.0]])
    def test_state_statistics_must_be_finite_per_channel_not_broadcast_scalars(
        self, rho_contract: SimpleNamespace, field: str, value: Any
    ) -> None:
        setattr(rho_contract.cfg.dataset, field, {"observation.state": {"mean": value}})

        with pytest.raises(ValueError, match="per-channel STATE statistics required"):
            backends._rho_contract(rho_contract.cfg, rho_contract.config)

    @pytest.mark.parametrize(
        "mode", [_NormalizationMode.MEAN_STD, _NormalizationMode.MIN_MAX, _NormalizationMode.QUANTILE]
    )
    def test_supported_state_normalization_requires_both_checkpoint_statistic_sets(
        self, rho_contract: SimpleNamespace, mode: _NormalizationMode
    ) -> None:
        data = rho_contract.cfg.dataset
        data.normalization_mapping[_FeatureType.STATE] = mode
        values = {
            "mean": [0, 0, 0],
            "std": [1, 2, 3],
            "min": [-1, -2, -3],
            "max": [1, 2, 3],
            "q01": [-2, -3, -4],
            "q99": [2, 3, 4],
            "count": 7,
        }
        data.stats = {"observation.state": deepcopy(values)}
        data.transformed_stats = {"observation.state": deepcopy(values)}

        assert backends._rho_contract(rho_contract.cfg, rho_contract.config) == (3, None)
        data.transformed_stats = None
        with pytest.raises(ValueError, match="Missing Rho transformed_stats for STATE"):
            backends._rho_contract(rho_contract.cfg, rho_contract.config)

    @pytest.mark.parametrize(
        ("mode", "stats", "message"),
        [
            (_NormalizationMode.UNSUPPORTED, None, "STATE normalization must be"),
            (_NormalizationMode.MEAN_STD, None, "Missing Rho stats for STATE"),
            (_NormalizationMode.MEAN_STD, {"mean": [0, 0, 0]}, "Missing Rho MEAN_STD"),
            (_NormalizationMode.MEAN_STD, {"mean": [0, 0, 0], "std": [1, 0, 1]}, "Degenerate"),
            (_NormalizationMode.MIN_MAX, {"mean": [0, 0, 0], "min": [1, 1, 1], "max": [0, 2, 2]}, "Degenerate"),
            (_NormalizationMode.QUANTILE, {"mean": [0, 0, 0], "q01": [0, 0, 0], "q99": [0, 1, 1]}, "Degenerate"),
        ],
    )
    def test_missing_unsupported_and_degenerate_normalization_is_rejected(
        self, rho_contract: SimpleNamespace, mode: _NormalizationMode, stats: Any, message: str
    ) -> None:
        data = rho_contract.cfg.dataset
        data.normalization_mapping[_FeatureType.STATE] = mode
        data.stats = None if stats is None else {"observation.state": stats}
        data.transformed_stats = deepcopy(data.stats)

        with pytest.raises(ValueError, match=message):
            backends._rho_contract(rho_contract.cfg, rho_contract.config)


class TestRhoTransforms:
    @pytest.mark.parametrize(
        "name",
        [
            "CenterCrop",
            "ResizeWithPadding",
            "RandomResizedCrop",
            "ColorJitter",
            "RandomFlipLeftRight",
            "RandomFlipUpDown",
            "RandomRot90",
        ],
    )
    def test_known_image_transforms_use_only_declared_camera_keys(
        self, rho_contract: SimpleNamespace, name: str
    ) -> None:
        transform = getattr(rho_contract.transforms, name)()
        rho_contract.cfg.dataset.transform_mapping = {"observation.image.0": [transform]}

        assert backends._rho_contract(rho_contract.cfg, rho_contract.config) == (3, None)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("action_type", _ActionType.VELOCITY),
            ("relative_to_state", False),
            ("state_key", "normalized.state"),
            ("action_key", "other.action"),
            ("absolute_idx", [-1]),
            ("absolute_idx", [3]),
            ("absolute_idx", [True]),
            ("absolute_idx", (2,)),
            ("use_absolute_grippers", 1),
            ("post_norm", True),
        ],
    )
    def test_delta_settings_outside_the_supported_position_contract_are_rejected(
        self, rho_contract: SimpleNamespace, field: str, value: Any
    ) -> None:
        setattr(rho_contract.delta, field, value)
        rho_contract.cfg.dataset.transform_mapping = {"action": [rho_contract.delta]}

        with pytest.raises(ValueError, match="Rho"):
            backends._rho_contract(rho_contract.cfg, rho_contract.config)

    @pytest.mark.parametrize("case", ["mapping", "sequence", "custom", "state", "duplicate", "width"])
    def test_custom_or_ambiguous_action_pipelines_are_rejected(self, rho_contract: SimpleNamespace, case: str) -> None:
        cfg = rho_contract.cfg
        mappings = {
            "mapping": [],
            "sequence": {"action": rho_contract.delta},
            "custom": {"action": [SimpleNamespace()]},
            "state": {"observation.state": [rho_contract.delta]},
            "duplicate": {"action": [rho_contract.delta, rho_contract.delta]},
            "width": {"action": [rho_contract.delta]},
        }
        cfg.dataset.transform_mapping = mappings[case]
        if case == "width":
            cfg.policy.action_feature.shape = (4,)
            cfg.dataset.transformed_features["action"].shape = (4,)

        with pytest.raises(ValueError, match="Rho"):
            backends._rho_contract(cfg, rho_contract.config)

    @pytest.mark.parametrize("case", ["valid", "undeclared-camera", "different-shapes"])
    def test_synchronized_crops_require_declared_equal_shape_source_cameras(
        self, rho_contract: SimpleNamespace, case: str
    ) -> None:
        config = rho_contract.config
        keys = list(config.policy.image_keys.values())
        if case != "different-shapes":
            config = replace(config, image_shapes={key: (2, 4, 3) for key in config.image_shapes})
        if case == "undeclared-camera":
            keys.append("oracle.camera")
        crop = rho_contract.transforms.SynchronizedRandomCrop(keys=keys)
        rho_contract.cfg.dataset.transform_mapping = {"observation.image": [crop]}

        if case == "valid":
            assert backends._rho_contract(rho_contract.cfg, config) == (3, None)
        else:
            with pytest.raises(ValueError, match=r"undeclared cameras|equal source camera shapes"):
                backends._rho_contract(rho_contract.cfg, config)


class _ArrayTensor:
    """NumPy-backed detach/clone boundary, not an implementation of native Torch transforms."""

    def __init__(self, values: Any) -> None:
        self.values = np.asarray(values)

    @property
    def shape(self) -> tuple[int, ...]:
        return self.values.shape

    def detach(self) -> _ArrayTensor:
        return self

    def clone(self) -> _ArrayTensor:
        return _ArrayTensor(self.values.copy())


@pytest.fixture
def rho_interface(rho_contract: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> Callable[[bool], SimpleNamespace]:
    modules = _modules(monkeypatch, "rho.common.normalize", "rho.eval", "rho.eval.policy_interface")

    def build(relative: bool) -> SimpleNamespace:
        calls = SimpleNamespace(unnormalize=[], inverse=[], parameters=None, resets=0, process_flags=[])
        decoded = _ArrayTensor(np.full((1, 3, 3), 17.0))
        absolute = _ArrayTensor(np.full((1, 3, 3), 29.0))

        class Unnormalize:
            def __init__(self, *args: Any) -> None:
                calls.constructor = args

            def to(self, device: str) -> Unnormalize:
                calls.device = device
                return self

            def __call__(self, values: dict[str, Any]) -> dict[str, _ArrayTensor]:
                calls.unnormalize.append(values)
                return {"action": decoded}

        class AbsoluteActions:
            def __init__(self, **kwargs: Any) -> None:
                calls.parameters = kwargs

            def __call__(self, values: dict[str, Any]) -> dict[str, _ArrayTensor]:
                calls.inverse.append(values)
                return {"action": absolute}

        class PolicyInterface:
            def __init__(self, interface_config: Any) -> None:
                calls.config = interface_config

            def reset(self) -> None:
                calls.resets += 1

            def process_observation(self, obs: dict[str, Any], process_action: bool = False) -> dict[str, Any]:
                calls.process_flags.append(process_action)
                obs["observation.state"].values.fill(-99)
                return obs

        monkeypatch.setattr(modules["rho.common.normalize"], "Unnormalize", Unnormalize, raising=False)
        monkeypatch.setattr(rho_contract.transforms, "AbsoluteActions", AbsoluteActions, raising=False)
        module = modules["rho.eval.policy_interface"]
        monkeypatch.setattr(module, "PolicyInterface", PolicyInterface, raising=False)
        monkeypatch.setattr(module, "PolicyInterfaceConfig", SimpleNamespace, raising=False)
        policy = object()
        interface = backends._make_rho_interface(
            rho_contract.cfg, policy, rho_contract.delta if relative else None, "observation.state", 3
        )
        return SimpleNamespace(interface=interface, calls=calls, policy=policy, decoded=decoded, absolute=absolute)

    return build


class TestRhoMeasuredStateCallbacks:
    @pytest.mark.parametrize("relative", [False, True])
    def test_native_callback_parameters_and_measured_anchor_are_preserved_before_preprocessing(
        self,
        rho_contract: SimpleNamespace,
        rho_interface: Callable[[bool], SimpleNamespace],
        relative: bool,
    ) -> None:
        runtime = rho_interface(relative)
        interface, calls = runtime.interface, runtime.calls
        state = _ArrayTensor([[[61.0, -6.0, 8.0]]])
        action = _ArrayTensor(np.zeros((1, 3, 3)))
        observed = {"observation.state": state}

        assert interface.process_observation(observed, process_action=True) is observed
        result = interface.process_action(observed, action)

        cfg = rho_contract.cfg
        assert calls.constructor == (
            {"action": cfg.dataset.transformed_features["action"]},
            cfg.dataset.normalization_mapping,
            cfg.dataset.transformed_stats,
            3,
        )
        assert calls.device == "cpu" and calls.process_flags == [True]
        assert calls.config.policy is runtime.policy and calls.config.data_config is cfg.dataset
        assert calls.config.eval_mode == "standard" and calls.config.execution_horizon == 3
        assert calls.config.observation_mapping == {key: key for key in cfg.policy.feature_dict}
        assert calls.unnormalize == [{"action": action}]
        assert result is (runtime.absolute if relative else runtime.decoded)
        if relative:
            assert calls.parameters == {
                "state_key": "observation.state",
                "action_key": "action",
                "action_type": _ActionType.POSITION,
                "relative_to_state": True,
                "use_absolute_grippers": True,
                "absolute_idx": [2],
                "post_norm": False,
            }
            assert calls.parameters["absolute_idx"] is not rho_contract.delta.absolute_idx
            assert calls.inverse[0]["action"] is runtime.decoded
            np.testing.assert_array_equal(calls.inverse[0]["observation.state"].values, [[[61, -6, 8]]])
            assert not np.shares_memory(calls.inverse[0]["observation.state"].values, state.values)
        else:
            assert not calls.inverse and calls.parameters is None
        assert sys.modules["torch"] is None

    def test_reset_clears_the_measured_anchor_and_calls_the_native_base_reset(
        self, rho_interface: Callable[[bool], SimpleNamespace]
    ) -> None:
        runtime = rho_interface(True)
        runtime.interface.process_observation({"observation.state": _ArrayTensor([[[1, 2, 3]]])})

        runtime.interface.reset()

        assert runtime.calls.resets == 1 and runtime.interface._raw_state is None
        with pytest.raises(ValueError, match="Missing measured model state"):
            runtime.interface.process_action({}, _ArrayTensor(np.zeros((1, 3, 3))))

    @pytest.mark.parametrize("shape", [(3, 3), (1, 2, 3), (1, 3, 2), (2, 3, 3)])
    def test_unexpected_model_action_shape_is_rejected_before_output_callbacks(
        self, rho_interface: Callable[[bool], SimpleNamespace], shape: tuple[int, ...]
    ) -> None:
        runtime = rho_interface(False)

        with pytest.raises(ValueError, match="Unexpected Rho model action shape"):
            runtime.interface.process_action({}, _ArrayTensor(np.zeros(shape)))

        assert not runtime.calls.unnormalize and not runtime.calls.inverse
