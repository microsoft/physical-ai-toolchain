"""Tests for the resumable external VLA Azure ML orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from azure.core.exceptions import ResourceNotFoundError

from training.vla.scripts.external_orchestrator import (
    ConfigurationError,
    OrchestratorConfig,
    WorkspaceConfig,
    advance,
    atomic_write_state,
    load_or_create_state,
)


class _FakeComponent:
    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, **inputs: Any) -> SimpleNamespace:
        return SimpleNamespace(component_name=self.name, inputs=inputs)


class _FakeJobs:
    def __init__(self, submit_statuses: dict[str, str] | None = None) -> None:
        self.remote: dict[str, SimpleNamespace] = {}
        self.submitted: list[SimpleNamespace] = []
        self.submit_statuses = submit_statuses or {}

    def create_or_update(self, job: SimpleNamespace, *, experiment_name: str) -> SimpleNamespace:
        job.experiment_name = experiment_name
        self.submitted.append(job)
        status = self.submit_statuses.get(job.name, "Running")
        remote = SimpleNamespace(name=job.name, status=status)
        self.remote[job.name] = remote
        return remote

    def get(self, name: str) -> SimpleNamespace:
        if name not in self.remote:
            raise ResourceNotFoundError("missing")
        return self.remote[name]


class _FakeClient:
    def __init__(self, submit_statuses: dict[str, str] | None = None) -> None:
        self.jobs = _FakeJobs(submit_statuses)


def _config() -> OrchestratorConfig:
    inputs: dict[str, Any] = {
        "adapter_name": "lerobot-pi",
        "azure_client_id": "none",
        "code_repository": "https://example.test/repository.git",
        "code_revision": "a" * 40,
        "dataset": "azureml:dataset:1",
        "dataset_asset_id": "azureml:dataset:1",
        "dataset_repo_id": "org/dataset",
        "eval_episodes": 10,
        "gradient_checkpointing": "true",
        "headroom_fraction": 0.1,
        "hf_key_vault_url": "none",
        "hf_token_secret_name": "none",
        "init_from_policy_hf_repo_id": "org/model",
        "init_from_policy_hf_revision": "b" * 40,
        "job_name": "vla-test",
        "log_freq": 1,
        "mixed_precision": "bf16",
        "mlflow_http_request_timeout": 60,
        "mlflow_token_refresh_retries": 3,
        "model_name": "vla-candidate",
        "output_dir": "/workspace/outputs/train",
        "pipeline_contract_fingerprint": "c" * 64,
        "policy_dtype": "bfloat16",
        "policy_type": "pi05",
        "probe_timeout_seconds": 3600,
        "promotion_policy": "azureml:promotion-policy:1",
        "rename_map_b64": "none",
        "save_freq": 1,
        "train_expert_only": "true",
        "training_steps": 2,
        "use_imagenet_stats": "false",
    }
    return OrchestratorConfig(
        workspace=WorkspaceConfig("subscription", "resource-group", "workspace"),
        compute={
            stage: "azureml:arc-compute"
            for stage in (*("preflight", "training", "finalize", "evaluate", "decide"), "calibrate")
        },
        instance_types={"calibrate": "gpu", "training": "gpu", "evaluate": "gpu"},
        inputs=inputs,
        calibration_candidates=(1, 2, 4),
        experiment_name="vla-external-test",
        managed_identity_client_id=None,
    )


def _loader(path: str | Path) -> _FakeComponent:
    return _FakeComponent(Path(path).stem)


def _job_name(state: dict[str, Any], suffix: str) -> str:
    return f"vlaext-{state['orchestration_id']}-{suffix}"


def test_given_new_state_when_advanced_then_preflight_is_persisted_and_submitted_once(tmp_path: Path) -> None:
    # Arrange
    state_file = tmp_path / "state.json"
    state = load_or_create_state(state_file, _config())
    client = _FakeClient()

    # Act
    result = advance(_config(), state, state_file, client, _loader)

    # Assert
    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert result["jobs"]["preflight"]["status"] == "Running"
    assert persisted["jobs"]["preflight"]["name"] == client.jobs.submitted[0].name
    assert len(client.jobs.submitted) == 1


def test_given_existing_nonterminal_job_when_resumed_then_it_is_not_submitted_again(tmp_path: Path) -> None:
    # Arrange
    state_file = tmp_path / "state.json"
    state = load_or_create_state(state_file, _config())
    client = _FakeClient()
    advance(_config(), state, state_file, client, _loader)

    # Act
    result = advance(_config(), state, state_file, client, _loader)

    # Assert
    assert result["jobs"]["preflight"]["status"] == "Running"
    assert len(client.jobs.submitted) == 1


def test_given_planned_job_accepted_before_state_update_when_resumed_then_remote_job_is_reconciled(
    tmp_path: Path,
) -> None:
    # Arrange
    state_file = tmp_path / "state.json"
    state = load_or_create_state(state_file, _config())
    name = _job_name(state, "preflight")
    state["jobs"]["preflight"] = {
        "name": name,
        "stage": "preflight",
        "candidate": None,
        "status": "Planned",
        "outputs": {"dataset_manifest": f"azureml://jobs/{name}/outputs/dataset_manifest"},
    }
    atomic_write_state(state_file, state)
    client = _FakeClient()
    client.jobs.remote[name] = SimpleNamespace(name=name, status="Running")

    # Act
    result = advance(_config(), state, state_file, client, _loader)

    # Assert
    assert result["jobs"]["preflight"]["status"] == "Running"
    assert client.jobs.submitted == []


def test_given_failed_candidate_and_two_safe_candidates_when_advanced_then_largest_safe_output_trains(
    tmp_path: Path,
) -> None:
    # Arrange
    state_file = tmp_path / "state.json"
    state = load_or_create_state(state_file, _config())
    prefix = f"vlaext-{state['orchestration_id']}"
    statuses = {
        f"{prefix}-preflight": "Completed",
        f"{prefix}-calibrate-1": "Completed",
        f"{prefix}-calibrate-2": "Failed",
        f"{prefix}-calibrate-4": "Completed",
        f"{prefix}-training": "Running",
    }
    client = _FakeClient(statuses)

    # Act
    result = advance(_config(), state, state_file, client, _loader)

    # Assert
    submitted_names = [job.name for job in client.jobs.submitted]
    training = client.jobs.submitted[-1]
    assert submitted_names == list(statuses)
    assert result["selected_calibration_candidate"] == 4
    assert training.component_name == "train"
    assert training.inputs["calibration_report"] == (f"azureml://jobs/{prefix}-calibrate-4/outputs/calibration_report")


@pytest.mark.parametrize("stage_status", ["Failed", "Canceled", "NotResponding"])
def test_given_terminal_preflight_failure_when_advanced_then_workflow_stops(
    tmp_path: Path,
    stage_status: str,
) -> None:
    # Arrange
    state_file = tmp_path / "state.json"
    state = load_or_create_state(state_file, _config())
    client = _FakeClient({_job_name(state, "preflight"): stage_status})

    # Act
    result = advance(_config(), state, state_file, client, _loader)

    # Assert
    assert result["status"] == "Failed"
    assert result["error"] == f"preflight job terminated with status {stage_status}"
    assert len(client.jobs.submitted) == 1


def test_given_all_jobs_complete_when_advanced_then_final_ids_and_uris_are_reported(tmp_path: Path) -> None:
    # Arrange
    state_file = tmp_path / "state.json"
    state = load_or_create_state(state_file, _config())
    prefix = f"vlaext-{state['orchestration_id']}"
    suffixes = ("preflight", "calibrate-1", "calibrate-2", "calibrate-4", "training", "finalize", "evaluate", "decide")
    client = _FakeClient({f"{prefix}-{suffix}": "Completed" for suffix in suffixes})

    # Act
    result = advance(_config(), state, state_file, client, _loader)

    # Assert
    assert result["status"] == "Completed"
    assert result["final_outputs"] == {
        "decision": f"azureml://jobs/{prefix}-decide/outputs/decision",
        "policy": f"azureml://jobs/{prefix}-decide/outputs/policy",
    }
    assert [job.name for job in client.jobs.submitted] == [f"{prefix}-{suffix}" for suffix in suffixes]


def test_given_real_components_when_advanced_then_all_standalone_builders_are_valid(tmp_path: Path) -> None:
    # Arrange
    state_file = tmp_path / "state.json"
    state = load_or_create_state(state_file, _config())
    prefix = f"vlaext-{state['orchestration_id']}"
    suffixes = ("preflight", "calibrate-1", "calibrate-2", "calibrate-4", "training", "finalize", "evaluate", "decide")
    client = _FakeClient({f"{prefix}-{suffix}": "Completed" for suffix in suffixes})

    # Act
    result = advance(_config(), state, state_file, client)

    # Assert
    assert result["status"] == "Completed"
    assert all(job.identity.type == "managed_identity" for job in client.jobs.submitted)
    assert [job.resources.instance_type for job in client.jobs.submitted if job.resources] == ["gpu"] * 5


def test_given_changed_config_when_state_loaded_then_resume_is_rejected(tmp_path: Path) -> None:
    # Arrange
    state_file = tmp_path / "state.json"
    config = _config()
    load_or_create_state(state_file, config)
    changed = OrchestratorConfig(
        workspace=config.workspace,
        compute=config.compute,
        instance_types=config.instance_types,
        inputs={**config.inputs, "training_steps": 99},
        calibration_candidates=config.calibration_candidates,
        experiment_name=config.experiment_name,
        managed_identity_client_id=config.managed_identity_client_id,
    )

    # Act & Assert
    with pytest.raises(ConfigurationError, match="does not match"):
        load_or_create_state(state_file, changed)
