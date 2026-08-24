"""
End-to-end lifecycle test for the OSMO IL (LeRobot/ACT) train -> eval path.

Stages a synthetic dataset, submits a real OSMO training workflow that registers its
checkpoint under a unique AzureML model name, validates MLflow tracking and task success,
resolves the concrete registered model version (never ``latest``), then evaluates that
model against the same dataset via ``submit-osmo-lerobot-eval.sh``.

Set ``E2E_LEROBOT_EVAL_POLICY_REPO_ID`` with ``E2E_LEROBOT_EVAL_POLICY_REVISION`` (a pinned
HuggingFace repo) or ``E2E_LEROBOT_EVAL_MODEL`` (AzureML ``name:version``) to skip training and
evaluate a pre-existing policy — a fast inner loop while fixing the eval path.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_il_lifecycle.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import AzureMLWorkspace, archive_all_model_versions, resolve_registered_model
from tests.e2e._common import e2e_name, log_e2e
from tests.e2e._lerobot_dataset import stage_synthetic_lerobot_dataset
from tests.e2e._mlflow import (
    assert_osmo_lerobot_eval_has_mlflow_tracking,
    assert_osmo_lerobot_training_has_mlflow_tracking,
)
from tests.e2e._osmo import (
    _LEROBOT_EVAL_MODEL_ENV,
    _LEROBOT_EVAL_POLICY_REPO_ENV,
    _LEROBOT_EVAL_POLICY_REVISION_ENV,
    assert_workflow_task_succeeded,
    monitor_osmo_workflow,
    osmo_lerobot_policy_source_from_model,
    resolve_osmo_lerobot_eval_policy_override,
    submit_osmo_lerobot_eval,
    submit_osmo_lerobot_training,
)

_LEROBOT_TRAIN_TASK_NAME = "lerobot-train"
_LEROBOT_EVAL_TASK_NAME = "lerobot-eval"


def test_resolve_osmo_lerobot_eval_policy_override_repo_forwards_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LEROBOT_EVAL_POLICY_REPO_ENV, "org/policy")
    monkeypatch.setenv(_LEROBOT_EVAL_POLICY_REVISION_ENV, "abc123")
    monkeypatch.delenv(_LEROBOT_EVAL_MODEL_ENV, raising=False)

    source = resolve_osmo_lerobot_eval_policy_override()

    assert source is not None
    assert source.args == ("--policy-repo-id", "org/policy", "--policy-revision", "abc123")
    assert source.description == "HuggingFace policy repo org/policy@abc123"


def test_resolve_osmo_lerobot_eval_policy_override_repo_requires_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_LEROBOT_EVAL_POLICY_REPO_ENV, "org/policy")
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REVISION_ENV, raising=False)
    monkeypatch.delenv(_LEROBOT_EVAL_MODEL_ENV, raising=False)

    with pytest.raises(pytest.skip.Exception):
        resolve_osmo_lerobot_eval_policy_override()


def test_resolve_osmo_lerobot_eval_policy_override_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REPO_ENV, raising=False)
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REVISION_ENV, raising=False)
    monkeypatch.setenv(_LEROBOT_EVAL_MODEL_ENV, "name:version")

    source = resolve_osmo_lerobot_eval_policy_override()

    assert source is not None
    assert source.args == ("--from-aml-model", "--model-name", "name", "--model-version", "version")
    assert source.description == "AzureML model name:version"


def test_resolve_osmo_lerobot_eval_policy_override_model_without_colon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REPO_ENV, raising=False)
    monkeypatch.setenv(_LEROBOT_EVAL_MODEL_ENV, "name_only")

    with pytest.raises(pytest.skip.Exception):
        resolve_osmo_lerobot_eval_policy_override()


def test_resolve_osmo_lerobot_eval_policy_override_model_empty_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REPO_ENV, raising=False)
    monkeypatch.setenv(_LEROBOT_EVAL_MODEL_ENV, "name:")

    with pytest.raises(pytest.skip.Exception):
        resolve_osmo_lerobot_eval_policy_override()


def test_resolve_osmo_lerobot_eval_policy_override_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_LEROBOT_EVAL_POLICY_REPO_ENV, raising=False)
    monkeypatch.delenv(_LEROBOT_EVAL_MODEL_ENV, raising=False)

    assert resolve_osmo_lerobot_eval_policy_override() is None


@pytest.mark.e2e
@pytest.mark.usefixtures("ensure_gpu_nodes_available")
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_osmo_il_lifecycle_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
    storage_account: str,
) -> None:
    log_e2e("Starting OSMO IL (LeRobot) lifecycle e2e test")
    policy_source = resolve_osmo_lerobot_eval_policy_override()
    dataset = stage_synthetic_lerobot_dataset(request, repo_root, storage_account)
    if policy_source is None:
        register_model_name = e2e_name("il-e2e-osmo-model")
        # Register cleanup before submit: the workflow registers the model server-side, so a later
        # assertion failure must not leak the registered version.
        request.addfinalizer(lambda: archive_all_model_versions(repo_root, aml_workspace, register_model_name))
        workflow = submit_osmo_lerobot_training(
            repo_root,
            aml_workspace,
            blob_url=dataset.blob_url,
            policy_type="act",
            training_steps=10,
            save_freq=5,
            batch_size=8,
            learning_rate="1e-4",
            log_freq=1,
            register_model_name=register_model_name,
        )
        monitor_osmo_workflow(request, workflow, repo_root, _LEROBOT_TRAIN_TASK_NAME, phase="LeRobot training")
        log_e2e("Validating OSMO LeRobot training MLflow tracking")
        assert_osmo_lerobot_training_has_mlflow_tracking(workflow, aml_workspace)
        log_e2e("Validating OSMO LeRobot training workflow task success")
        assert_workflow_task_succeeded(workflow, repo_root, _LEROBOT_TRAIN_TASK_NAME)
        model = resolve_registered_model(repo_root, aml_workspace, model_name=register_model_name)
        policy_source = osmo_lerobot_policy_source_from_model(model)
    else:
        log_e2e(f"Using pre-configured eval policy {policy_source.description} (training skipped)")

    eval_workflow = submit_osmo_lerobot_eval(
        repo_root,
        aml_workspace,
        policy_source=policy_source,
        policy_type="act",
        eval_episodes=1,
        eval_batch_size=1,
        blob_storage_account=dataset.storage_account,
        blob_container=dataset.container,
        blob_prefix=dataset.prefix,
    )
    monitor_osmo_workflow(request, eval_workflow, repo_root, _LEROBOT_EVAL_TASK_NAME, phase="LeRobot eval")
    log_e2e("Validating OSMO LeRobot eval MLflow tracking")
    assert_osmo_lerobot_eval_has_mlflow_tracking(eval_workflow, aml_workspace)
    log_e2e("Validating OSMO LeRobot eval workflow task success")
    assert_workflow_task_succeeded(eval_workflow, repo_root, _LEROBOT_EVAL_TASK_NAME)
    log_e2e("OSMO LeRobot lifecycle e2e test finished successfully")
