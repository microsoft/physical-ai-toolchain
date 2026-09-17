"""Lazy native VLA inference; simulator action decoding belongs to the server."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import metadata
from pathlib import Path
from threading import RLock
from types import ModuleType
from typing import Any, Protocol
from urllib.parse import urlparse

import numpy as np

from .artifacts import file_identity
from .config import EvaluationConfig, finite_array, require

_RUNTIME_LOCK = RLock()


class Backend(Protocol):
    """Return native, output-transformed chunks before generic action mapping."""

    provenance: dict[str, Any]

    def infer(self, state: np.ndarray, images: dict[str, np.ndarray], *, seed: int, reset: bool) -> np.ndarray:
        """Infer a finite rank-two [action_horizon, output_width] chunk."""
        ...


def _package_identity(distribution: str, modules: tuple[ModuleType, ...]) -> dict[str, Any]:
    try:
        version = metadata.version(distribution)
    except metadata.PackageNotFoundError:
        version = "unpackaged"
    return {
        "distribution": distribution,
        "version": version,
        "modules": {
            module.__name__: file_identity(Path(module.__file__)) for module in modules if module.__file__ is not None
        },
    }


def _provenance(config: EvaluationConfig, device: str) -> dict[str, Any]:
    return {
        "backend": config.policy.backend,
        "checkpoint": str(config.policy.checkpoint),
        "checkpoint_sha256_expected": config.policy.checkpoint_sha256,
        "checkpoint_fingerprint_owner": "server",
        "device_requested": device,
        "python": {"version": sys.version, "executable": sys.executable},
        "numpy": _package_identity("numpy", (np,)),
        "adapter": file_identity(Path(__file__)),
        "state_preprocessing": "declared_state_mapping_then_native_checkpoint_transforms",
        "generic_action_decoding": "server_only",
        "eval_mode": "standard",
        "action_queue": "none",
        "deterministic": False,
        "huggingface_offline": "scoped_environment; imported_libraries_may_cache_settings",
    }


@contextmanager
def _offline_runtime() -> Iterator[None]:
    """Serialize backend calls while temporarily disabling Hugging Face network access."""
    with _RUNTIME_LOCK:
        previous = {key: os.environ.get(key) for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
        try:
            for key in previous:
                os.environ[key] = "1"
            hub_constants = sys.modules.get("huggingface_hub.constants")
            require(
                hub_constants is None or getattr(hub_constants, "HF_HUB_OFFLINE", True),
                "Hugging Face was imported online; start a fresh policy process with HF_HUB_OFFLINE=1",
            )
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _cached_openpi_asset(url: str, *, force_download: bool = False, **kwargs: Any) -> Path:
    """Resolve native asset references without downloading or changing the cache."""
    require(not force_download, "OpenPI forced downloads are disabled")
    parsed = urlparse(url)
    if not parsed.scheme:
        return Path(url).expanduser().resolve(strict=True)
    require(
        parsed.scheme in {"gs", "https", "http"} and bool(parsed.netloc) and not parsed.query and not parsed.fragment,
        "Unsupported OpenPI asset reference; supply local or pre-cached native assets",
    )
    cache = Path(os.environ.get("OPENPI_DATA_HOME", "~/.cache/openpi")).expanduser().resolve()
    path = (cache / parsed.netloc / parsed.path.strip("/")).resolve()
    require(path.is_relative_to(cache), "OpenPI asset reference escapes its cache")
    if not path.exists():
        raise FileNotFoundError(f"OpenPI native asset is not cached: {path}; downloads are disabled")
    return path


@contextmanager
def _cached_openpi_assets(download: Any) -> Iterator[None]:
    original = download.maybe_download
    download.maybe_download = _cached_openpi_asset
    try:
        yield
    finally:
        download.maybe_download = original


def _observation(
    config: EvaluationConfig, state: np.ndarray, images: dict[str, np.ndarray], *, seed: int, reset: bool
) -> dict[str, Any]:
    require(type(seed) is int and 0 <= seed < 2**32, "Policy seed must be an unsigned 32-bit integer")
    require(type(reset) is bool, "Policy reset must be boolean")
    state, images = config.validate_observation(state, images)
    with np.errstate(over="ignore", invalid="ignore"):
        model_state = config.policy.state_mapping.apply(state).astype(np.float32)
    require(bool(np.isfinite(model_state).all()), "Mapped model state exceeds finite float32 range")
    return {
        config.policy.state_key: model_state,
        config.policy.prompt_key: config.instruction,
        **{model_key: images[camera] for camera, model_key in config.policy.image_keys.items()},
    }


def _actions(value: Any, horizon: int, width: int | None = None) -> np.ndarray:
    array = np.asarray(value)
    require(
        array.ndim == 2 and array.shape[0] == horizon and array.shape[1] > 0 and array.dtype.kind in "fiu",
        "Native policy must return a numeric [action_horizon, output_width] array",
    )
    require(width is None or array.shape[1] == width, "Native action width differs from checkpoint features")
    with np.errstate(over="ignore", invalid="ignore"):
        result = np.array(array, dtype=np.float32, order="C", copy=True)
    require(bool(np.isfinite(result).all()), "Native policy actions are not finite float32 values")
    return result


def _rho_state_stats(data: Any, state_key: str, width: int) -> None:
    from rho.common.types import FeatureType, NormalizationMode

    mode = data.normalization_mapping.get(FeatureType.STATE, NormalizationMode.IDENTITY)
    pairs = {
        NormalizationMode.IDENTITY: (),
        NormalizationMode.MEAN_STD: ("mean", "std"),
        NormalizationMode.MIN_MAX: ("min", "max"),
        NormalizationMode.QUANTILE: ("q01", "q99"),
    }
    require(mode in pairs, "Rho STATE normalization must be IDENTITY, MEAN_STD, MIN_MAX, or QUANTILE")
    for label, stats in (("stats", data.stats), ("transformed_stats", data.transformed_stats)):
        require(stats is None or isinstance(stats, dict), f"Rho {label} must contain checkpoint statistics")
        values = stats.get(state_key) if stats is not None else None
        if values is None:
            require(mode == NormalizationMode.IDENTITY, f"Missing Rho {label} for STATE")
            continue
        require(isinstance(values, dict), f"Rho {label} for STATE must be a statistics mapping")
        for name, value in values.items():
            if name != "count":
                finite_array(value, (width,), f"Rho {label}.{state_key}.{name}; per-channel STATE statistics required")
        if mode != NormalizationMode.IDENTITY:
            require({"mean", *pairs[mode]} <= values.keys(), f"Missing Rho {mode.value} STATE statistics in {label}")
            low, high = (np.asarray(values[name], dtype=np.float64) for name in pairs[mode])
            valid = high > 0 if mode == NormalizationMode.MEAN_STD else high > low
            require(bool(valid.all()), f"Degenerate Rho STATE normalization statistics in {label}")


def _rho_transforms(data: Any, config: EvaluationConfig, action_width: int) -> Any:
    from rho.common import transforms
    from rho.common.constants import ACTION, OBSERVATION_IMAGE
    from rho.common.types import ActionType

    mapping = data.transform_mapping
    require(mapping is None or isinstance(mapping, dict), "Invalid Rho checkpoint transform mapping")
    image_keys = set(config.policy.image_keys.values())
    image_transforms = (
        transforms.CenterCrop,
        transforms.ResizeWithPadding,
        transforms.RandomResizedCrop,
        transforms.ColorJitter,
        transforms.RandomFlipLeftRight,
        transforms.RandomFlipUpDown,
        transforms.RandomRot90,
    )
    delta = None
    for key, items in (mapping or {}).items():
        require(isinstance(items, (list, tuple)), f"Rho transforms for {key} must be a sequence")
        for transform in items:
            require(not getattr(transform, "post_norm", False), f"Unsupported Rho post-normalization transform: {key}")
            if key == ACTION:
                require(
                    type(transform) is transforms.DeltaActions and delta is None,
                    "Rho supports no action transform or one native DeltaActions transform",
                )
                delta = transform
            elif key in image_keys and type(transform) in image_transforms:
                continue
            elif key in image_keys | {OBSERVATION_IMAGE} and type(transform) is transforms.SynchronizedRandomCrop:
                require(set(transform.keys) <= image_keys, "Rho synchronized crop refers to undeclared cameras")
                require(
                    len(
                        {
                            config.image_shapes[camera]
                            for camera, model_key in config.policy.image_keys.items()
                            if model_key in transform.keys
                        }
                    )
                    == 1,
                    "Rho synchronized crop requires equal source camera shapes",
                )
            else:
                raise ValueError(f"Unsupported Rho checkpoint transform for {key}: {type(transform).__name__}")
    if delta is not None:
        require(
            delta.action_type == ActionType.POSITION
            and delta.relative_to_state is True
            and delta.state_key == config.policy.state_key
            and delta.action_key == ACTION,
            "Rho DeltaActions must be POSITION, state-relative, and use the declared STATE and ACTION keys",
        )
        require(
            action_width == len(config.policy.state_mapping.indices),
            "Rho state-relative POSITION actions require equal model state and action widths",
        )
        indices = delta.absolute_idx
        require(
            indices is None
            or (
                isinstance(indices, list) and all(type(index) is int and 0 <= index < action_width for index in indices)
            ),
            "Rho absolute_idx must contain valid checkpoint action-channel indices",
        )
        require(type(delta.use_absolute_grippers) is bool, "Invalid Rho use_absolute_grippers setting")
    return delta


def _rho_contract(cfg: Any, config: EvaluationConfig) -> tuple[int, Any]:
    from rho.common.constants import ACTION, OBSERVATION_LANG, OBSERVATION_STATE
    from rho.common.types import ActionType, FeatureType

    require(
        config.policy.state_key == OBSERVATION_STATE and config.policy.prompt_key == OBSERVATION_LANG,
        "Rho native policies require state_key='observation.state' and prompt_key='task'",
    )
    require(cfg.policy.n_obs_steps == 1, "Rho observation history other than one step is unsupported")
    require(
        cfg.policy.chunk_size == config.policy.action_horizon, "Rho checkpoint action horizon differs from contract"
    )
    require(getattr(cfg.dataset, "action_type", None) == ActionType.POSITION, "Rho requires POSITION action semantics")
    require(isinstance(getattr(cfg.dataset, "normalization_mapping", None), dict), "Missing Rho normalization mapping")
    features = cfg.policy.feature_dict
    data_features = getattr(cfg.dataset, "transformed_features", None)
    require(isinstance(features, dict) and isinstance(data_features, dict), "Missing checkpoint feature definitions")
    state_width = len(config.policy.state_mapping.indices)
    expected = {ACTION, config.policy.state_key, *config.policy.image_keys.values()}
    require(
        set(features) == expected and set(data_features) == expected,
        "Rho checkpoint features differ from declared inputs",
    )
    require(
        set(cfg.policy.image_features) == set(config.policy.image_keys.values()),
        "Rho native image keys differ from the declared camera mapping",
    )
    action_feature = cfg.policy.action_feature
    require(
        action_feature is not None
        and len(action_feature.shape) == 1
        and type(action_feature.shape[0]) is int
        and action_feature.shape[0] > 0,
        "Rho checkpoint ACTION must be a nonempty vector",
    )
    action_width = action_feature.shape[0]
    for key in expected:
        for feature in (features[key], data_features[key]):
            if key == config.policy.state_key:
                require(
                    feature.type == FeatureType.STATE and tuple(feature.shape) == (state_width,),
                    "Rho checkpoint STATE differs from the mapped model state",
                )
            elif key == ACTION:
                require(
                    feature.type == FeatureType.ACTION and tuple(feature.shape) == (action_width,),
                    "Rho checkpoint ACTION feature definitions disagree",
                )
            else:
                require(
                    feature.type == FeatureType.VISUAL and len(feature.shape) == 3 and feature.shape[0] == 3,
                    f"Rho camera {key} must be a channel-first RGB feature",
                )
    temporal = cfg.policy.delta_indices_dict
    require(isinstance(temporal, dict), "Missing Rho observation temporal contract")
    for key in expected - {ACTION}:
        indices = temporal.get(key)
        require(indices is not None and list(indices) == [0], f"Rho requires only the current observation for {key}")
    delta = _rho_transforms(cfg.dataset, config, action_width)
    _rho_state_stats(cfg.dataset, config.policy.state_key, state_width)
    return action_width, delta


def _make_rho_interface(cfg: Any, policy: Any, delta: Any, state_key: str, action_width: int) -> Any:
    from rho.common.constants import ACTION
    from rho.common.normalize import Unnormalize
    from rho.common.transforms import AbsoluteActions
    from rho.eval.policy_interface import PolicyInterface, PolicyInterfaceConfig

    class MeasuredStateInterface(PolicyInterface):
        def __init__(self, interface_config: Any) -> None:
            self._raw_state = None
            self._action_unnormalize = Unnormalize(
                {ACTION: cfg.dataset.transformed_features[ACTION]},
                cfg.dataset.normalization_mapping,
                cfg.dataset.transformed_stats,
                cfg.policy.chunk_size,
            ).to(cfg.device)
            self._delta_inverse = (
                None
                if delta is None
                else AbsoluteActions(
                    state_key=state_key,
                    action_key=ACTION,
                    action_type=delta.action_type,
                    relative_to_state=delta.relative_to_state,
                    use_absolute_grippers=delta.use_absolute_grippers,
                    absolute_idx=None if delta.absolute_idx is None else list(delta.absolute_idx),
                    post_norm=False,
                )
            )
            interface_config.output_transforms = self._action_unnormalize
            super().__init__(interface_config)

        def reset(self) -> None:
            super().reset()
            self._raw_state = None

        def process_observation(self, obs: dict[str, Any], process_action: bool = False) -> dict[str, Any]:
            self._raw_state = obs[state_key].detach().clone()
            return super().process_observation(obs, process_action=process_action)

        def process_action(self, obs: dict[str, Any], action: Any) -> Any:
            require(
                tuple(action.shape) == (1, cfg.policy.chunk_size, action_width), "Unexpected Rho model action shape"
            )
            decoded = self._action_unnormalize({ACTION: action})[ACTION]
            if self._delta_inverse is None:
                return decoded
            require(self._raw_state is not None, "Missing measured model state for Rho action reconstruction")
            return self._delta_inverse({ACTION: decoded, state_key: self._raw_state})[ACTION]

    return MeasuredStateInterface(
        PolicyInterfaceConfig(
            data_config=cfg.dataset,
            policy=policy,
            device=cfg.device,
            observation_mapping={key: key for key in cfg.policy.feature_dict},
            eval_mode="standard",
            execution_horizon=cfg.policy.chunk_size,
        )
    )


class _RhoBackend:
    def __init__(self, config: EvaluationConfig, device: str) -> None:
        import rho
        import torch
        from rho.checkpoints import is_checkpoint_bundle
        from rho.common import normalize, transforms
        from rho.eval import eval_config, policy_interface
        from rho.policies import make_policy

        require(is_checkpoint_bundle(config.policy.checkpoint), "Rho requires an explicit, local checkpoint bundle")
        cfg = eval_config.EvalConfig(pretrained_checkpoint=str(config.policy.checkpoint), device=device)
        require(
            Path(cfg.pretrained_checkpoint).resolve() == config.policy.checkpoint.resolve(),
            "Rho resolved a different checkpoint; select the exact checkpoint bundle",
        )
        cfg.policy.device = device
        action_width, delta = _rho_contract(cfg, config)
        if hasattr(cfg.policy, "enable_gradient_checkpointing"):
            cfg.policy.enable_gradient_checkpointing = False
        policy = make_policy(cfg.policy)
        policy.load_from_pretrained(cfg.pretrained_checkpoint)
        policy.eval().requires_grad_(False).to(device)
        for module in policy.modules():
            if hasattr(module, "enable_gradient_checkpointing"):
                module.enable_gradient_checkpointing = False
        self._interface = _make_rho_interface(cfg, policy, delta, config.policy.state_key, action_width)
        self._config = config
        self._torch = torch
        self._action_width = action_width
        self._first_observation = True
        self.provenance = {
            **_provenance(config, device),
            "framework": _package_identity(
                "rho",
                (
                    rho,
                    eval_config,
                    policy_interface,
                    normalize,
                    transforms,
                    sys.modules[type(cfg.dataset).__module__],
                    sys.modules[type(policy).__module__],
                ),
            ),
            "runtime": _package_identity("torch", (torch,)),
            "policy_class": f"{type(policy).__module__}.{type(policy).__qualname__}",
            "action_horizon": cfg.policy.chunk_size,
            "output_width": action_width,
            "observation_history": 1,
            "seed_behavior": "numpy_and_torch_on_task_reset",
            "seed_scope": "process_global; no_deterministic_kernel_guarantee",
            "reset_behavior": "policy_reset_and_first_observation_reset_flag",
            "action_transform": "absolute" if delta is None else "pre_norm_state_relative_POSITION_DeltaActions",
            "delta_anchor": None if delta is None else "measured_model_state_before_preprocessing",
            "absolute_action_indices": None if delta is None else list(delta.absolute_idx or []),
            "use_absolute_grippers": None if delta is None else delta.use_absolute_grippers,
            "normalization": {
                str(getattr(key, "value", key)): str(getattr(value, "value", value))
                for key, value in cfg.dataset.normalization_mapping.items()
            },
            "normalization_source": "checkpoint_data_config_transformed_stats",
            "image_transforms": "native_get_transforms_remap_false_training_false",
            "limitations": ["POSITION only", "one observation", "no post-normalization or custom transforms", "no RTC"],
        }

    def infer(self, state: np.ndarray, images: dict[str, np.ndarray], *, seed: int, reset: bool) -> np.ndarray:
        observation = _observation(self._config, state, images, seed=seed, reset=reset)
        with _offline_runtime(), self._torch.no_grad():
            episode_reset = reset or self._first_observation
            if episode_reset:
                np.random.seed(seed)
                self._torch.manual_seed(seed)
                self._interface.policy.reset()
            state_key = self._config.policy.state_key
            observation[state_key] = self._torch.from_numpy(observation[state_key]).reshape(1, 1, -1)
            observation[self._config.policy.prompt_key] = [self._config.instruction]
            for key in self._config.policy.image_keys.values():
                observation[key] = self._torch.from_numpy(observation[key]).permute(2, 0, 1).float()[None] / 255.0
            observation["_reset_"] = episode_reset
            actions = self._interface.get_action_chunk(observation)
            require(isinstance(actions, self._torch.Tensor), "Rho interface did not return an action tensor")
            require(
                tuple(actions.shape) == (1, self._config.policy.action_horizon, self._action_width),
                "Rho interface returned an unexpected action shape",
            )
            result = _actions(
                actions.detach().float().cpu().numpy()[0], self._config.policy.action_horizon, self._action_width
            )
            self._first_observation = False
            return result


class _OpenPIBackend:
    def __init__(self, config: EvaluationConfig, device: str) -> None:
        os.environ["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"
        jax_preimported = "jax" in sys.modules

        import openpi
        from openpi.shared import download

        with _cached_openpi_assets(download):
            from openpi.policies import policy_config
            from openpi.training import config as training_config

            native_cfg = training_config.get_config(config.policy.training_config)
            require(
                native_cfg.model.action_horizon == config.policy.action_horizon,
                "OpenPI native model action horizon differs from contract",
            )
            is_pytorch = (config.policy.checkpoint / "model.safetensors").is_file()
            native_devices = []
            if is_pytorch:
                self._policy = policy_config.create_trained_policy(
                    native_cfg, config.policy.checkpoint, pytorch_device=device
                )
            else:
                import jax

                require(
                    (config.policy.checkpoint / "params").is_dir(), "OpenPI checkpoint has no native params directory"
                )
                visible = jax.devices()
                require(
                    len(visible) == 1,
                    "OpenPI's native JAX loader replicates weights; expose exactly one device before loading",
                )
                platform = "gpu" if device in {"cuda", "cuda:0"} else "cpu" if device in {"cpu", "cpu:0"} else None
                require(
                    platform is not None and visible[0].platform == platform,
                    "OpenPI JAX device differs from the request; configure JAX_PLATFORMS/device visibility first",
                )
                native_devices = [str(value) for value in visible]
                self._policy = policy_config.create_trained_policy(native_cfg, config.policy.checkpoint)
        self._config = config
        self._download = download
        self._first_observation = True
        self.provenance = {
            **_provenance(config, device),
            "framework": _package_identity(
                "openpi",
                (
                    openpi,
                    training_config,
                    policy_config,
                    download,
                    sys.modules[type(self._policy).__module__],
                ),
            ),
            "runtime": _package_identity(
                "torch" if is_pytorch else "jax", (sys.modules["torch" if is_pytorch else "jax"],)
            ),
            "training_config": config.policy.training_config,
            "policy_class": f"{type(self._policy).__module__}.{type(self._policy).__qualname__}",
            "checkpoint_format": "pytorch" if is_pytorch else "jax",
            "action_horizon": native_cfg.model.action_horizon,
            "model_action_dim": native_cfg.model.action_dim,
            "output_width": "native_output_transforms",
            "seed_behavior": "task_reset_only",
            "reset_behavior": "native_reset; default_Policy_inherits_noop_reset; native_RNG_not_reseeded",
            "normalization_source": "native_checkpoint_assets",
            "transforms": "native_create_trained_policy_input_and_output_pipeline",
            "asset_access": "local_or_existing_openpi_cache_only",
            "device_selection": "native_pytorch_device_parameter"
            if is_pytorch
            else "single_visible_jax_default_device",
            "jax_devices": native_devices,
            "jax_preimported": jax_preimported,
            "xla_python_client_preallocate": os.environ["XLA_PYTHON_CLIENT_PREALLOCATE"],
            "limitations": [
                "episode seed does not reset native model RNG",
                "native JAX loading requires one visible device",
                "auxiliary assets must already be local or cached",
                "preallocation setting cannot undo earlier JAX initialization",
            ],
        }

    def infer(self, state: np.ndarray, images: dict[str, np.ndarray], *, seed: int, reset: bool) -> np.ndarray:
        observation = _observation(self._config, state, images, seed=seed, reset=reset)
        with _offline_runtime(), _cached_openpi_assets(self._download):
            if reset or self._first_observation:
                self._policy.reset()
            result = self._policy.infer(observation)
            require(isinstance(result, dict) and "actions" in result, "OpenPI inference did not return actions")
            actions = _actions(result["actions"], self._config.policy.action_horizon)
            self._first_observation = False
            return actions


def load_backend(config: EvaluationConfig, device: str) -> Backend:
    """Load only a native Rho or OpenPI backend from a local checkpoint directory."""
    require(config.policy.backend in {"rho", "openpi"}, "Supported policy backends: rho, openpi")
    require(isinstance(device, str) and bool(device.strip()), "Specify a native policy device")
    require(config.policy.checkpoint.is_dir(), "Policy checkpoint must be an existing local directory")
    require(set(config.policy.image_keys) == set(config.image_shapes), "Map every declared camera to a model key")
    keys = [config.policy.state_key, config.policy.prompt_key, *config.policy.image_keys.values()]
    require(len(set(keys)) == len(keys), "Model observation keys must not alias")
    if config.policy.backend == "openpi":
        require(
            isinstance(config.policy.training_config, str) and bool(config.policy.training_config.strip()),
            "OpenPI requires a registered training_config",
        )
    with _offline_runtime():
        if config.policy.backend == "rho":
            return _RhoBackend(config, device)
        return _OpenPIBackend(config, device)
