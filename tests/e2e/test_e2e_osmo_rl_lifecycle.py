"""
End-to-end lifecycle test for the OSMO RL (Isaac/SKRL) train -> eval path.

Submits a real OSMO training workflow that registers its checkpoint under a unique
AzureML model name, validates MLflow tracking and task success, resolves the concrete
registered model version (never ``latest``), then evaluates the ``models:/<name>/<version>``
checkpoint via ``submit-osmo-eval.sh``.

Set ``E2E_OSMO_ISAAC_EVAL_CHECKPOINT_URI`` to skip training and evaluate a pre-existing
checkpoint — a fast inner loop while fixing the eval path.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_rl_lifecycle.py
```
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from tests.e2e._aml import AzureMLWorkspace, archive_all_model_versions, resolve_registered_model
from tests.e2e._common import e2e_name, log_e2e
from tests.e2e._mlflow import assert_osmo_workflow_has_mlflow_tracking
from tests.e2e._osmo import (
    _OSMO_ISAAC_EVAL_CHECKPOINT_URI_ENV,
    assert_workflow_task_succeeded,
    fetch_workflow_task_logs,
    monitor_osmo_workflow,
    resolve_osmo_isaac_eval_checkpoint_override,
    submit_osmo_isaaclab_eval,
    submit_osmo_training,
)

_TASK = "Isaac-Velocity-Rough-Anymal-C-v0"
_ISAAC_TRAINING_TASK_NAME = "isaac-training"
_ISAAC_INFERENCE_TASK_NAME = "isaac-inference"


def _runtime_provenance(logs: str, marker: str) -> dict[str, object]:
    for line in logs.splitlines():
        if marker in line:
            payload = json.loads(line.partition(marker)[2])
            if isinstance(payload, dict):
                return payload
    raise AssertionError(f"Task logs did not contain {marker}")


def _declared_runtime_versions(repo_root: Path) -> dict[str, str]:
    payload = tomllib.loads((repo_root / "training/rl/pyproject.toml").read_text(encoding="utf-8"))
    versions = {}
    for requirement in payload["project"]["dependencies"]:
        name, separator, version = requirement.partition("==")
        if separator:
            versions[name.lower().replace("_", "-")] = version
    return versions


def test_resolve_osmo_isaac_eval_checkpoint_override_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_OSMO_ISAAC_EVAL_CHECKPOINT_URI_ENV, "models:/name/1")

    assert resolve_osmo_isaac_eval_checkpoint_override() == "models:/name/1"


def test_resolve_osmo_isaac_eval_checkpoint_override_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_OSMO_ISAAC_EVAL_CHECKPOINT_URI_ENV, raising=False)

    assert resolve_osmo_isaac_eval_checkpoint_override() is None


@pytest.mark.e2e
@pytest.mark.usefixtures("ensure_gpu_nodes_available")
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_osmo_rl_lifecycle_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
) -> None:
    log_e2e("Starting OSMO RL (Isaac Lab) lifecycle e2e test")
    checkpoint_uri = resolve_osmo_isaac_eval_checkpoint_override()
    if checkpoint_uri is None:
        register_model_name = e2e_name("rl-e2e-osmo-model")
        # Register cleanup before submit: the workflow registers the model server-side, so a later
        # assertion failure must not leak the registered version.
        request.addfinalizer(lambda: archive_all_model_versions(repo_root, aml_workspace, register_model_name))
        workflow = submit_osmo_training(
            repo_root,
            task=_TASK,
            max_iterations=10,
            num_envs=64,
            register_model_name=register_model_name,
        )
        monitor_osmo_workflow(request, workflow, repo_root, _ISAAC_TRAINING_TASK_NAME, phase="training")
        log_e2e("Validating OSMO training MLflow tracking")
        assert_osmo_workflow_has_mlflow_tracking(workflow, aml_workspace)
        log_e2e("Validating OSMO training workflow task success")
        assert_workflow_task_succeeded(workflow, repo_root, _ISAAC_TRAINING_TASK_NAME)
        provenance = _runtime_provenance(
            fetch_workflow_task_logs(workflow, repo_root, _ISAAC_TRAINING_TASK_NAME),
            "RUNTIME_PROVENANCE=",
        )
        assert provenance["install_mode"] == "reinstall_from_frozen_lock"
        assert provenance["missing"] == []
        assert provenance["actual"] == provenance["expected"]
        expected_lock_sha256 = hashlib.sha256((repo_root / "training/rl/uv.lock").read_bytes()).hexdigest()
        assert provenance["lock_sha256"] == expected_lock_sha256
        expected_versions = provenance["expected"]
        assert isinstance(expected_versions, dict)
        for name, version in _declared_runtime_versions(repo_root).items():
            assert expected_versions[name] == version
        model = resolve_registered_model(repo_root, aml_workspace, model_name=register_model_name)
        checkpoint_uri = f"models:/{model.name}/{model.version}"
    else:
        log_e2e(f"Using pre-configured eval checkpoint {checkpoint_uri} (training skipped)")

    eval_workflow = submit_osmo_isaaclab_eval(
        repo_root,
        aml_workspace,
        checkpoint_uri=checkpoint_uri,
        task=_TASK,
        num_envs=4,
        max_steps=50,
    )
    monitor_osmo_workflow(request, eval_workflow, repo_root, _ISAAC_INFERENCE_TASK_NAME, phase="eval")
    log_e2e("Validating OSMO eval workflow task success")
    assert_workflow_task_succeeded(eval_workflow, repo_root, _ISAAC_INFERENCE_TASK_NAME)
    log_e2e("OSMO RL lifecycle e2e test finished successfully")
