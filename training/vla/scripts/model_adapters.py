"""Translate model-family configuration into VLA training behavior."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Protocol

from vla_contracts import SCHEMA_VERSION, ContractError, RecordKind, canonical_json, sha256_bytes, validate_record

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2

COMMON_LIFECYCLE_FIELDS = frozenset(
    {
        "source_model",
        "dataset",
        "code",
        "runtime",
        "adapter",
        "compute",
        "calibration",
        "approval",
        "run",
        "evaluation",
        "incident",
        "promotion",
    }
)

_REQUIRED_ADAPTER_CAPABILITIES = frozenset(
    {
        "source_validation",
        "dependency_setup",
        "observation_mapping",
        "precision",
        "trainable_scope",
        "checkpointing",
        "training_arguments",
    }
)

_GROOT_PAPER_CAPABILITIES = frozenset(
    {
        "source_validation",
        "dependency_setup",
        "observation_mapping",
        "precision",
        "trainable_scope",
        "checkpointing",
        "training_arguments",
    }
)


class AdapterError(ValueError):
    """Raised when model-family configuration is unsupported."""


@dataclass(frozen=True, slots=True)
class AdapterRequest:
    """Model-family options supplied by generic orchestration."""

    policy_type: str
    mixed_precision: str = "bf16"
    policy_dtype: str | None = None
    train_expert_only: bool | None = None
    gradient_checkpointing: bool = False
    rename_map: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class AdapterResolution:
    """Validated adapter output consumed by the training runtime."""

    adapter_name: str
    adapter_version: str
    policy_type: str
    dependency_extra: str
    required_model_files: tuple[str, ...]
    gated_repositories: tuple[str, ...]
    default_source_model: str | None
    default_source_revision: str | None
    requires_hf_token: bool
    use_imagenet_stats: bool
    environment: Mapping[str, str]
    training_arguments: tuple[str, ...]
    config_sha256: str

    def contract_record(self, created_at: str) -> dict[str, object]:
        """Return the common adapter lifecycle record."""
        record: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "kind": RecordKind.ADAPTER.value,
            "created_at": created_at,
            "name": self.adapter_name,
            "version": self.adapter_version,
            "config_sha256": self.config_sha256,
        }
        validate_record(record, RecordKind.ADAPTER)
        return record


class ModelAdapter(Protocol):
    """Model-family adapter interface."""

    name: str
    version: str
    default_policy_type: str
    capabilities: frozenset[str]

    def resolve(self, request: AdapterRequest) -> AdapterResolution:
        """Validate a request and return runtime configuration."""


class LeRobotPiAdapter:
    """Adapter for LeRobot pi0, pi0_fast, and pi05 policies."""

    name = "lerobot-pi"
    version = "1"
    default_policy_type = "pi0"
    capabilities = _REQUIRED_ADAPTER_CAPABILITIES

    _POLICY_TYPES = frozenset({"pi0", "pi0_fast", "pi05"})
    _MIXED_PRECISION = frozenset({"no", "fp16", "bf16"})
    _POLICY_DTYPES = frozenset({"float32", "bfloat16"})
    _REQUIRED_MODEL_FILES = ("config.json", "model.safetensors")
    _GATED_REPOSITORIES = ("google/paligemma-3b-pt-224",)

    def resolve(self, request: AdapterRequest) -> AdapterResolution:
        """Validate PI options and translate them into runtime values."""
        if request.policy_type not in self._POLICY_TYPES:
            raise AdapterError(f"Unsupported LeRobot PI policy type: {request.policy_type}")
        if request.mixed_precision not in self._MIXED_PRECISION:
            raise AdapterError(f"Unsupported mixed precision mode: {request.mixed_precision}")
        if request.policy_dtype is not None and request.policy_dtype not in self._POLICY_DTYPES:
            raise AdapterError(f"Unsupported PI policy dtype: {request.policy_dtype}")

        rename_map = dict(request.rename_map or {})
        valid_rename_map = all(
            isinstance(key, str) and key and isinstance(value, str) and value for key, value in rename_map.items()
        )
        if not valid_rename_map:
            raise AdapterError("PI camera rename_map must contain non-empty string keys and values")

        environment = {
            "POLICY_TYPE": request.policy_type,
            "MIXED_PRECISION": request.mixed_precision,
            "TRAIN_EXPERT_ONLY": str(request.train_expert_only or False).lower(),
            "GRADIENT_CHECKPOINTING": str(request.gradient_checkpointing).lower(),
            "USE_IMAGENET_STATS": "false",
        }
        if request.policy_dtype is not None:
            environment["POLICY_DTYPE"] = request.policy_dtype
        if rename_map:
            environment["RENAME_MAP_JSON"] = canonical_json(rename_map)

        config = asdict(request)
        config["rename_map"] = rename_map
        config_sha256 = sha256_bytes(canonical_json(config).encode("utf-8"))
        return AdapterResolution(
            adapter_name=self.name,
            adapter_version=self.version,
            policy_type=request.policy_type,
            dependency_extra="pi",
            required_model_files=self._REQUIRED_MODEL_FILES,
            gated_repositories=self._GATED_REPOSITORIES,
            default_source_model=None,
            default_source_revision=None,
            requires_hf_token=True,
            use_imagenet_stats=False,
            environment=environment,
            training_arguments=("--policy.push_to_hub=false", "--wandb.enable=false"),
            config_sha256=config_sha256,
        )


class SmolVLAAdapter:
    """Adapter for pretrained LeRobot SmolVLA fine-tuning."""

    name = "lerobot-smolvla"
    version = "1"
    default_policy_type = "smolvla"
    capabilities = _REQUIRED_ADAPTER_CAPABILITIES

    _POLICY_TYPES = frozenset({"smolvla"})
    _MIXED_PRECISION = frozenset({"no", "fp16", "bf16"})
    _REQUIRED_MODEL_FILES = ("config.json", "model.safetensors")
    _DEFAULT_SOURCE_MODEL = "lerobot/smolvla_base"
    _DEFAULT_SOURCE_REVISION = "d9f33c94a60fb382c90dea2164c96845bd955e28"

    def resolve(self, request: AdapterRequest) -> AdapterResolution:
        """Validate SmolVLA options and translate them into runtime values."""
        if request.policy_type not in self._POLICY_TYPES:
            raise AdapterError(f"Unsupported LeRobot SmolVLA policy type: {request.policy_type}")
        if request.mixed_precision not in self._MIXED_PRECISION:
            raise AdapterError(f"Unsupported mixed precision mode: {request.mixed_precision}")
        if request.policy_dtype is not None:
            raise AdapterError("SmolVLA does not expose the PI policy dtype option")
        if request.gradient_checkpointing:
            raise AdapterError("SmolVLA does not expose the PI gradient checkpointing option")

        rename_map = dict(request.rename_map or {})
        valid_rename_map = all(
            isinstance(key, str) and key and isinstance(value, str) and value for key, value in rename_map.items()
        )
        if not valid_rename_map:
            raise AdapterError("SmolVLA rename_map must contain non-empty string keys and values")

        train_expert_only = True if request.train_expert_only is None else request.train_expert_only
        environment = {
            "POLICY_TYPE": request.policy_type,
            "MIXED_PRECISION": request.mixed_precision,
            "TRAIN_EXPERT_ONLY": str(train_expert_only).lower(),
            "USE_IMAGENET_STATS": "false",
        }
        if rename_map:
            environment["RENAME_MAP_JSON"] = canonical_json(rename_map)

        config = asdict(request)
        config["rename_map"] = rename_map
        config["train_expert_only"] = train_expert_only
        config_sha256 = sha256_bytes(canonical_json(config).encode("utf-8"))
        return AdapterResolution(
            adapter_name=self.name,
            adapter_version=self.version,
            policy_type=request.policy_type,
            dependency_extra="smolvla",
            required_model_files=self._REQUIRED_MODEL_FILES,
            gated_repositories=(),
            default_source_model=self._DEFAULT_SOURCE_MODEL,
            default_source_revision=self._DEFAULT_SOURCE_REVISION,
            requires_hf_token=False,
            use_imagenet_stats=False,
            environment=environment,
            training_arguments=("--policy.push_to_hub=false", "--wandb.enable=false"),
            config_sha256=config_sha256,
        )


_ADAPTERS: dict[str, ModelAdapter] = {
    LeRobotPiAdapter.name: LeRobotPiAdapter(),
    SmolVLAAdapter.name: SmolVLAAdapter(),
}


def get_adapter(name: str) -> ModelAdapter:
    """Return a registered model-family adapter."""
    try:
        return _ADAPTERS[name]
    except KeyError as exc:
        raise AdapterError(f"Unknown model adapter: {name}") from exc


def _run_self_check() -> None:
    adapter = get_adapter("lerobot-pi")
    resolutions = [
        adapter.resolve(AdapterRequest(policy_type=policy_type)) for policy_type in ("pi0", "pi0_fast", "pi05")
    ]
    if {resolution.policy_type for resolution in resolutions} != {"pi0", "pi0_fast", "pi05"}:
        raise AdapterError("PI policy aliases did not resolve")
    for resolution in resolutions:
        resolution.contract_record("2026-09-22T00:00:00Z")

    smolvla = get_adapter("lerobot-smolvla").resolve(AdapterRequest(policy_type="smolvla"))
    if smolvla.dependency_extra != "smolvla" or smolvla.default_source_model != "lerobot/smolvla_base":
        raise AdapterError("SmolVLA dependency or immutable source defaults did not resolve")
    if smolvla.environment.get("TRAIN_EXPERT_ONLY") != "true" or smolvla.use_imagenet_stats:
        raise AdapterError("SmolVLA trainable-scope or normalization defaults did not resolve")
    smolvla.contract_record("2026-09-22T00:00:00Z")

    try:
        get_adapter("lerobot-smolvla").resolve(
            AdapterRequest(policy_type="smolvla", gradient_checkpointing=True)
        )
    except AdapterError:
        pass
    else:
        raise AdapterError("PI-only gradient checkpointing passed SmolVLA validation")

    try:
        adapter.resolve(AdapterRequest(policy_type="unknown"))
    except AdapterError:
        pass
    else:
        raise AdapterError("Unknown PI policy type passed validation")

    try:
        get_adapter("groot")
    except AdapterError:
        pass
    else:
        raise AdapterError("Unimplemented GR00T adapter resolved")

    if not _REQUIRED_ADAPTER_CAPABILITIES.issubset(_GROOT_PAPER_CAPABILITIES):
        missing = sorted(_REQUIRED_ADAPTER_CAPABILITIES - _GROOT_PAPER_CAPABILITIES)
        raise AdapterError(f"GR00T paper mapping lacks capabilities: {', '.join(missing)}")
    if any(field.startswith(("pi", "paligemma", "camera")) for field in COMMON_LIFECYCLE_FIELDS):
        raise AdapterError("Common lifecycle fields contain PI-specific behavior")


def create_parser() -> argparse.ArgumentParser:
    """Create the adapter command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", help="Run adapter registry and isolation checks")
    parser.add_argument("--adapter", default="lerobot-pi", help="Adapter registry name")
    parser.add_argument("--policy-type", help="Policy type to resolve")
    parser.add_argument("--mixed-precision", default="bf16", choices=["no", "fp16", "bf16"])
    parser.add_argument("--policy-dtype", choices=["float32", "bfloat16"])
    parser.add_argument("--train-expert-only", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--rename-map", default="{}", help="JSON object mapping observation keys")
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute an adapter self-check or resolve one request."""
    if args.self_check:
        _run_self_check()
        print("VLA model adapter self-check passed")
        return EXIT_SUCCESS
    adapter = get_adapter(args.adapter)
    policy_type = args.policy_type or adapter.default_policy_type
    try:
        rename_map = json.loads(args.rename_map)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"--rename-map must be valid JSON: {exc}") from exc
    if not isinstance(rename_map, dict):
        raise AdapterError("--rename-map must be a JSON object")
    resolution = adapter.resolve(
        AdapterRequest(
            policy_type=policy_type,
            mixed_precision=args.mixed_precision,
            policy_dtype=args.policy_dtype,
            train_expert_only=args.train_expert_only,
            gradient_checkpointing=args.gradient_checkpointing,
            rename_map=rename_map,
        )
    )
    print(json.dumps(asdict(resolution), indent=2, sort_keys=True))
    return EXIT_SUCCESS


def main() -> int:
    """Run the model adapter CLI."""
    try:
        return run(create_parser().parse_args())
    except (AdapterError, ContractError) as exc:
        print(f"Adapter validation failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stderr.close()
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
