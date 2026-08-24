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

from pathlib import Path

import pytest

from tests.e2e._aml import AzureMLWorkspace, archive_all_model_versions, resolve_registered_model
from tests.e2e._common import e2e_name, log_e2e
from tests.e2e._mlflow import assert_osmo_workflow_has_mlflow_tracking
from tests.e2e._osmo import (
    _OSMO_ISAAC_EVAL_CHECKPOINT_URI_ENV,
    assert_workflow_task_succeeded,
    monitor_osmo_workflow,
    resolve_osmo_isaac_eval_checkpoint_override,
    submit_osmo_isaaclab_eval,
    submit_osmo_training,
)

_TASK = "Isaac-Velocity-Rough-Anymal-C-v0"
_ISAAC_TRAINING_TASK_NAME = "isaac-training"
_ISAAC_INFERENCE_TASK_NAME = "isaac-inference"


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
