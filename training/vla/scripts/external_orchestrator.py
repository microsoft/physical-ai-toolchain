"""Run the VLA evidence workflow as resumable standalone Azure ML jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from azure.ai.ml import MLClient, load_component
from azure.ai.ml.entities import ManagedIdentityConfiguration, ResourceConfiguration
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2
SCHEMA_VERSION = 1
DEFAULT_STATE_FILE = Path("infrastructure/setup/generated/vla-external-orchestrator-state.json")
COMPONENT_ROOT = Path(__file__).resolve().parents[1] / "workflows/azureml/components"

_STAGES = ("preflight", "training", "finalize", "evaluate", "decide")
_COMPUTE_KEYS = (*_STAGES, "calibrate")
_GPU_STAGES = frozenset({"calibrate", "training", "evaluate"})
_SUCCESS_STATUS = "Completed"
_FAILURE_STATUSES = frozenset({"Canceled", "Failed", "NotResponding"})
_REQUIRED_INPUTS = frozenset(
    {
        "adapter_name",
        "azure_client_id",
        "code_repository",
        "code_revision",
        "dataset",
        "dataset_asset_id",
        "dataset_repo_id",
        "eval_episodes",
        "gradient_checkpointing",
        "headroom_fraction",
        "hf_key_vault_url",
        "hf_token_secret_name",
        "init_from_policy_hf_repo_id",
        "init_from_policy_hf_revision",
        "job_name",
        "log_freq",
        "mixed_precision",
        "mlflow_http_request_timeout",
        "mlflow_token_refresh_retries",
        "model_name",
        "output_dir",
        "pipeline_contract_fingerprint",
        "policy_dtype",
        "policy_type",
        "probe_timeout_seconds",
        "promotion_policy",
        "rename_map_b64",
        "save_freq",
        "train_expert_only",
        "training_steps",
        "use_imagenet_stats",
    }
)
_OUTPUTS = {
    "preflight": ("dataset_manifest",),
    "calibrate": ("workload_contract", "calibration_report"),
    "training": ("checkpoints", "training_record"),
    "finalize": ("candidate", "candidate_manifest"),
    "evaluate": ("evaluation",),
    "decide": ("decision", "policy"),
}
_COMPONENT_FILES = {"training": "train", **{stage: stage for stage in _OUTPUTS if stage != "training"}}


class ConfigurationError(ValueError):
    """Raised when orchestrator configuration or state is invalid."""


class JobsOperations(Protocol):
    """Azure ML job operations used by the orchestrator."""

    def create_or_update(self, job: Any, *, experiment_name: str) -> Any:
        """Create or update one job."""

    def get(self, name: str) -> Any:
        """Get one job by name."""


class Client(Protocol):
    """Minimal Azure ML client contract used by the orchestrator."""

    jobs: JobsOperations


@dataclass(frozen=True)
class WorkspaceConfig:
    """Azure ML workspace coordinates."""

    subscription_id: str
    resource_group: str
    workspace_name: str


@dataclass(frozen=True)
class OrchestratorConfig:
    """Validated external-orchestrator configuration."""

    workspace: WorkspaceConfig
    compute: dict[str, str]
    instance_types: dict[str, str]
    inputs: dict[str, Any]
    calibration_candidates: tuple[int, ...]
    experiment_name: str
    managed_identity_client_id: str | None

    @classmethod
    def load(cls, path: Path) -> OrchestratorConfig:
        """Load and validate an orchestrator JSON configuration."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"Unable to load configuration: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigurationError("Configuration must be a JSON object")

        workspace = _required_mapping(raw, "workspace")
        workspace_config = WorkspaceConfig(
            subscription_id=_required_string(workspace, "subscription_id"),
            resource_group=_required_string(workspace, "resource_group"),
            workspace_name=_required_string(workspace, "workspace_name"),
        )
        compute_raw = _required_mapping(raw, "compute")
        compute = {stage: _required_string(compute_raw, stage) for stage in _COMPUTE_KEYS}
        inputs = dict(_required_mapping(raw, "inputs"))
        missing_inputs = sorted(_REQUIRED_INPUTS - inputs.keys())
        if missing_inputs:
            raise ConfigurationError(f"Missing inputs: {', '.join(missing_inputs)}")

        candidates_raw = raw.get("calibration_candidates", [1, 2, 4])
        if not isinstance(candidates_raw, list) or any(
            not isinstance(candidate, int) or isinstance(candidate, bool) for candidate in candidates_raw
        ):
            raise ConfigurationError("calibration_candidates must be a JSON array of integers")
        candidates = tuple(candidates_raw)
        if not candidates or any(candidate <= 0 for candidate in candidates):
            raise ConfigurationError("calibration_candidates must contain positive integers")
        if candidates != tuple(sorted(set(candidates))):
            raise ConfigurationError("calibration_candidates must be sorted and unique")

        instance_types_raw = raw.get("instance_types", {})
        if not isinstance(instance_types_raw, dict):
            raise ConfigurationError("instance_types must be a JSON object")
        instance_types = {stage: _required_string(instance_types_raw, stage) for stage in _GPU_STAGES}
        experiment_name = raw.get("experiment_name", "vla-external-orchestrator")
        if not isinstance(experiment_name, str) or not experiment_name.strip():
            raise ConfigurationError("experiment_name must be a non-empty string")
        client_id = raw.get("managed_identity_client_id")
        if client_id is not None and (not isinstance(client_id, str) or not client_id.strip()):
            raise ConfigurationError("managed_identity_client_id must be a non-empty string or null")

        return cls(
            workspace=workspace_config,
            compute=compute,
            instance_types=instance_types,
            inputs=inputs,
            calibration_candidates=candidates,
            experiment_name=experiment_name,
            managed_identity_client_id=client_id,
        )

    def fingerprint(self) -> str:
        """Return a stable fingerprint that protects resume configuration."""
        payload = {
            "workspace": self.workspace.__dict__,
            "compute": self.compute,
            "instance_types": self.instance_types,
            "inputs": self.inputs,
            "calibration_candidates": self.calibration_candidates,
            "experiment_name": self.experiment_name,
            "managed_identity_client_id": self.managed_identity_client_id,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _required_mapping(parent: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(f"{key} must be a JSON object")
    return value


def _required_string(parent: Mapping[str, Any], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty string")
    return value


def atomic_write_state(path: Path, state: Mapping[str, Any]) -> None:
    """Atomically replace the durable JSON state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_or_create_state(path: Path, config: OrchestratorConfig) -> dict[str, Any]:
    """Load durable state or initialize a new orchestration."""
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(f"Unable to load state: {exc}") from exc
        if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
            raise ConfigurationError("State has an unsupported schema version")
        if state.get("config_fingerprint") != config.fingerprint():
            raise ConfigurationError("Configuration does not match the existing state file")
        return state

    state = {
        "schema_version": SCHEMA_VERSION,
        "orchestration_id": uuid.uuid4().hex[:12],
        "config_fingerprint": config.fingerprint(),
        "status": "Running",
        "jobs": {},
        "selected_calibration_candidate": None,
        "final_outputs": {},
        "error": None,
    }
    atomic_write_state(path, state)
    return state


def output_uri(job_name: str, output_name: str) -> str:
    """Build an immutable Azure ML named-output URI."""
    return f"azureml://jobs/{job_name}/outputs/{output_name}"


def _job_key(stage: str, candidate: int | None = None) -> str:
    return f"calibrate_{candidate}" if stage == "calibrate" else stage


def _job_name(state: Mapping[str, Any], stage: str, candidate: int | None = None) -> str:
    suffix = f"calibrate-{candidate}" if candidate is not None else stage
    return f"vlaext-{state['orchestration_id']}-{suffix}"


def _outputs(job_name: str, stage: str) -> dict[str, str]:
    return {name: output_uri(job_name, name) for name in _OUTPUTS[stage]}


def _record_job(
    state: dict[str, Any],
    state_file: Path,
    stage: str,
    candidate: int | None = None,
) -> dict[str, Any]:
    key = _job_key(stage, candidate)
    jobs = state["jobs"]
    if key not in jobs:
        name = _job_name(state, stage, candidate)
        jobs[key] = {
            "name": name,
            "stage": stage,
            "candidate": candidate,
            "status": "Planned",
            "outputs": _outputs(name, stage),
        }
        atomic_write_state(state_file, state)
    return jobs[key]


def _input(config: OrchestratorConfig, name: str) -> Any:
    return config.inputs[name]


def _stage_inputs(
    config: OrchestratorConfig,
    state: Mapping[str, Any],
    stage: str,
    candidate: int | None = None,
) -> dict[str, Any]:
    jobs = state["jobs"]
    preflight = jobs.get("preflight", {}).get("outputs", {})
    calibration_key = f"calibrate_{state.get('selected_calibration_candidate')}"
    calibration = jobs.get(calibration_key, {}).get("outputs", {})
    training = jobs.get("training", {}).get("outputs", {})
    finalize = jobs.get("finalize", {}).get("outputs", {})
    evaluate = jobs.get("evaluate", {}).get("outputs", {})

    if stage == "preflight":
        return {
            "dataset": _input(config, "dataset"),
            "dataset_asset_id": _input(config, "dataset_asset_id"),
            "dataset_repo_id": _input(config, "dataset_repo_id"),
        }
    if stage == "calibrate":
        return {
            "dataset": _input(config, "dataset"),
            "dataset_manifest": preflight["dataset_manifest"],
            "dataset_asset_id": _input(config, "dataset_asset_id"),
            "adapter_name": _input(config, "adapter_name"),
            "dataset_repo_id": _input(config, "dataset_repo_id"),
            "policy_type": _input(config, "policy_type"),
            "mixed_precision": _input(config, "mixed_precision"),
            "policy_dtype": _input(config, "policy_dtype"),
            "init_from_policy_hf_repo_id": _input(config, "init_from_policy_hf_repo_id"),
            "init_from_policy_hf_revision": _input(config, "init_from_policy_hf_revision"),
            "train_expert_only": _input(config, "train_expert_only"),
            "gradient_checkpointing": _input(config, "gradient_checkpointing"),
            "use_imagenet_stats": _input(config, "use_imagenet_stats"),
            "rename_map_b64": _input(config, "rename_map_b64"),
            "micro_batch_size": candidate,
            "headroom_fraction": _input(config, "headroom_fraction"),
            "probe_timeout_seconds": _input(config, "probe_timeout_seconds"),
            "code_repository": _input(config, "code_repository"),
            "code_revision": _input(config, "code_revision"),
            "compute_target": config.compute["calibrate"],
            "azure_client_id": _input(config, "azure_client_id"),
            "hf_key_vault_url": _input(config, "hf_key_vault_url"),
            "hf_token_secret_name": _input(config, "hf_token_secret_name"),
        }
    if stage == "training":
        return {
            "dataset": _input(config, "dataset"),
            "dataset_manifest": preflight["dataset_manifest"],
            "dataset_asset_id": _input(config, "dataset_asset_id"),
            "calibration_report": calibration["calibration_report"],
            "workload_contract": calibration["workload_contract"],
            "adapter_name": _input(config, "adapter_name"),
            "dataset_repo_id": _input(config, "dataset_repo_id"),
            "policy_type": _input(config, "policy_type"),
            "init_from_policy_hf_repo_id": _input(config, "init_from_policy_hf_repo_id"),
            "init_from_policy_hf_revision": _input(config, "init_from_policy_hf_revision"),
            "train_expert_only": _input(config, "train_expert_only"),
            "gradient_checkpointing": _input(config, "gradient_checkpointing"),
            "use_imagenet_stats": _input(config, "use_imagenet_stats"),
            "rename_map_b64": _input(config, "rename_map_b64"),
            "training_steps": _input(config, "training_steps"),
            "save_freq": _input(config, "save_freq"),
            "log_freq": _input(config, "log_freq"),
            "job_name": _input(config, "job_name"),
            "output_dir": _input(config, "output_dir"),
            "mixed_precision": _input(config, "mixed_precision"),
            "policy_dtype": _input(config, "policy_dtype"),
            "subscription_id": config.workspace.subscription_id,
            "resource_group": config.workspace.resource_group,
            "workspace_name": config.workspace.workspace_name,
            "mlflow_token_refresh_retries": _input(config, "mlflow_token_refresh_retries"),
            "mlflow_http_request_timeout": _input(config, "mlflow_http_request_timeout"),
            "azure_client_id": _input(config, "azure_client_id"),
            "hf_key_vault_url": _input(config, "hf_key_vault_url"),
            "hf_token_secret_name": _input(config, "hf_token_secret_name"),
            "code_repository": _input(config, "code_repository"),
            "code_revision": _input(config, "code_revision"),
            "compute_target": config.compute["training"],
        }
    if stage == "finalize":
        return {
            "checkpoints": training["checkpoints"],
            "training_record": training["training_record"],
            "dataset_manifest": preflight["dataset_manifest"],
            "adapter_name": _input(config, "adapter_name"),
            "policy_type": _input(config, "policy_type"),
        }
    if stage == "evaluate":
        return {
            "candidate": finalize["candidate"],
            "candidate_manifest": finalize["candidate_manifest"],
            "training_record": training["training_record"],
            "dataset": _input(config, "dataset"),
            "dataset_manifest": preflight["dataset_manifest"],
            "dataset_asset_id": _input(config, "dataset_asset_id"),
            "dataset_repo_id": _input(config, "dataset_repo_id"),
            "policy_type": _input(config, "policy_type"),
            "eval_episodes": _input(config, "eval_episodes"),
            "job_name": _input(config, "job_name"),
        }
    if stage == "decide":
        return {
            "candidate_manifest": finalize["candidate_manifest"],
            "training_record": training["training_record"],
            "evaluation": evaluate["evaluation"],
            "promotion_policy": _input(config, "promotion_policy"),
            "model_name": _input(config, "model_name"),
        }
    raise ConfigurationError(f"Unsupported stage: {stage}")


def _build_job(
    config: OrchestratorConfig,
    state: Mapping[str, Any],
    record: Mapping[str, Any],
    component_loader: Callable[[str | Path], Any],
) -> Any:
    stage = record["stage"]
    component = component_loader(COMPONENT_ROOT / f"{_COMPONENT_FILES[stage]}.yaml")
    job = component(**_stage_inputs(config, state, stage, record["candidate"]))
    job.name = record["name"]
    job.display_name = f"VLA external orchestration - {record['name']}"
    job.compute = config.compute[stage]
    job.identity = ManagedIdentityConfiguration(client_id=config.managed_identity_client_id)
    if stage in _GPU_STAGES:
        job.resources = ResourceConfiguration(instance_type=config.instance_types[stage])
    job.tags = {
        "code_revision": str(_input(config, "code_revision")),
        "framework": "lerobot",
        "lifecycle_stage": stage,
        "orchestration_id": str(state["orchestration_id"]),
        "pipeline_contract_fingerprint": str(_input(config, "pipeline_contract_fingerprint")),
    }
    return job


def _reconcile_or_submit(
    config: OrchestratorConfig,
    state: dict[str, Any],
    state_file: Path,
    client: Client,
    record: dict[str, Any],
    component_loader: Callable[[str | Path], Any],
) -> str:
    try:
        remote = client.jobs.get(record["name"])
    except ResourceNotFoundError:
        job = _build_job(config, state, record, component_loader)
        remote = client.jobs.create_or_update(job, experiment_name=config.experiment_name)
    remote_name = getattr(remote, "name", None)
    remote_status = getattr(remote, "status", None)
    if not isinstance(remote_name, str) or not remote_name:
        raise RuntimeError("Azure ML returned a job without a name")
    if not isinstance(remote_status, str) or not remote_status:
        raise RuntimeError(f"Azure ML job {remote_name} has no status")
    record["name"] = remote_name
    record["status"] = remote_status
    record["outputs"] = _outputs(remote_name, record["stage"])
    atomic_write_state(state_file, state)
    return remote_status


def _advance_job(
    config: OrchestratorConfig,
    state: dict[str, Any],
    state_file: Path,
    client: Client,
    stage: str,
    component_loader: Callable[[str | Path], Any],
    candidate: int | None = None,
) -> str:
    record = _record_job(state, state_file, stage, candidate)
    if record["status"] == _SUCCESS_STATUS or record["status"] in _FAILURE_STATUSES:
        return record["status"]
    return _reconcile_or_submit(config, state, state_file, client, record, component_loader)


def _fail(state: dict[str, Any], state_file: Path, message: str) -> None:
    state["status"] = "Failed"
    state["error"] = message
    atomic_write_state(state_file, state)


def advance(
    config: OrchestratorConfig,
    state: dict[str, Any],
    state_file: Path,
    client: Client,
    component_loader: Callable[[str | Path], Any] = load_component,
) -> dict[str, Any]:
    """Reconcile the workflow and submit at most through the next active job."""
    if state["status"] != "Running":
        return state

    status = _advance_job(config, state, state_file, client, "preflight", component_loader)
    if status in _FAILURE_STATUSES:
        _fail(state, state_file, f"preflight job terminated with status {status}")
        return state
    if status != _SUCCESS_STATUS:
        return state

    for candidate in config.calibration_candidates:
        status = _advance_job(config, state, state_file, client, "calibrate", component_loader, candidate)
        if status not in _FAILURE_STATUSES | {_SUCCESS_STATUS}:
            return state

    successful_candidates = [
        candidate
        for candidate in config.calibration_candidates
        if state["jobs"][_job_key("calibrate", candidate)]["status"] == _SUCCESS_STATUS
    ]
    if not successful_candidates:
        _fail(state, state_file, "all calibration candidates failed")
        return state
    state["selected_calibration_candidate"] = max(successful_candidates)
    atomic_write_state(state_file, state)

    for stage in ("training", "finalize", "evaluate", "decide"):
        status = _advance_job(config, state, state_file, client, stage, component_loader)
        if status in _FAILURE_STATUSES:
            _fail(state, state_file, f"{stage} job terminated with status {status}")
            return state
        if status != _SUCCESS_STATUS:
            return state

    state["status"] = "Completed"
    state["final_outputs"] = dict(state["jobs"]["decide"]["outputs"])
    atomic_write_state(state_file, state)
    return state


def create_parser() -> argparse.ArgumentParser:
    """Create the external-orchestrator argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Orchestrator JSON configuration")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE, help="Durable JSON state path")
    return parser


def _create_client(config: OrchestratorConfig) -> MLClient:
    credential = DefaultAzureCredential(managed_identity_client_id=config.managed_identity_client_id)
    return MLClient(
        credential=credential,
        subscription_id=config.workspace.subscription_id,
        resource_group_name=config.workspace.resource_group,
        workspace_name=config.workspace.workspace_name,
    )


def run(args: argparse.Namespace, client_factory: Callable[[OrchestratorConfig], Client] = _create_client) -> int:
    """Advance one orchestration until a cloud job is active or the workflow terminates."""
    config = OrchestratorConfig.load(args.config)
    state = load_or_create_state(args.state_file, config)
    state = advance(config, state, args.state_file, client_factory(config))
    print(json.dumps(state, indent=2, sort_keys=True))
    return EXIT_FAILURE if state["status"] == "Failed" else EXIT_SUCCESS


def main() -> int:
    """Run the external Azure ML orchestrator."""
    try:
        return run(create_parser().parse_args())
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Orchestration error: {exc}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
