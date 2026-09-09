"""
End-to-end test for the OSMO VLA (NVIDIA GR00T) LeRobot fine-tuning submission path.

Submits a real OSMO workflow, waits for it to complete, and validates that the
fine-tuning workflow task succeeded. Generates and stages a minimal synthetic
GR00T dataset by default, so no dataset needs to be pre-staged; set
``E2E_VLA_DATASET_BLOB_URL`` to fine-tune against a pre-staged dataset instead.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_vla_finetune.py
```
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.e2e._aml import AzureMLWorkspace, archive_all_model_versions, resolve_registered_model
from tests.e2e._common import e2e_name, log_e2e
from tests.e2e._mlflow import assert_osmo_vla_has_mlflow_tracking, delete_mlflow_experiment, delete_mlflow_run
from tests.e2e._osmo import (
    _vla_base_model_args,
    assert_workflow_task_succeeded,
    cancel_osmo_workflow,
    fetch_workflow_task_logs,
    start_task_pod_log_stream,
    submit_osmo_vla_finetune,
    wait_until_osmo_completed,
    wait_until_osmo_started,
)

_VLA_TASK_NAME = "train"


def _vla_runtime_provenance(logs: str) -> dict[str, object]:
    marker = "VLA_RUNTIME_PROVENANCE="
    for line in logs.splitlines():
        if marker in line:
            payload = json.loads(line.partition(marker)[2])
            if isinstance(payload, dict):
                return payload
    raise AssertionError(f"Task logs did not contain {marker}")


def test_vla_base_model_forwards_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2E_VLA_BASE_MODEL", "nvidia/GR00T-N1.5-3B")
    monkeypatch.setenv("E2E_VLA_BASE_MODEL_REVISION", "abc123")

    assert _vla_base_model_args() == [
        "--base-model",
        "nvidia/GR00T-N1.5-3B",
        "--base-model-revision",
        "abc123",
    ]


def test_vla_base_model_requires_revision_for_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2E_VLA_BASE_MODEL", "nvidia/GR00T-N1.5-3B")
    monkeypatch.delenv("E2E_VLA_BASE_MODEL_REVISION", raising=False)

    with pytest.raises(pytest.skip.Exception):
        _vla_base_model_args()


def test_vla_base_model_allows_absolute_path_without_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2E_VLA_BASE_MODEL", "/models/local-groot")
    monkeypatch.delenv("E2E_VLA_BASE_MODEL_REVISION", raising=False)

    assert _vla_base_model_args() == ["--base-model", "/models/local-groot"]


def test_vla_base_model_revision_without_model_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("E2E_VLA_BASE_MODEL", raising=False)
    monkeypatch.setenv("E2E_VLA_BASE_MODEL_REVISION", "abc123")

    with pytest.raises(pytest.skip.Exception):
        _vla_base_model_args()


@pytest.mark.e2e
@pytest.mark.usefixtures("ensure_gpu_nodes_available")
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_osmo_vla_finetune_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
) -> None:
    log_e2e("Starting OSMO VLA (GR00T) fine-tuning e2e test")
    register_model_name = e2e_name("vla-e2e-osmo-model")
    request.addfinalizer(lambda: delete_mlflow_experiment(aml_workspace, register_model_name))
    request.addfinalizer(lambda: archive_all_model_versions(repo_root, aml_workspace, register_model_name))
    workflow = submit_osmo_vla_finetune(
        repo_root,
        aml_workspace,
        request=request,
        max_steps=2,
        save_steps=2,
        batch_size=1,
        dataloader_workers=0,
        register_model_name=register_model_name,
    )
    request.addfinalizer(lambda: cancel_osmo_workflow(workflow, repo_root))

    log_stream = start_task_pod_log_stream(workflow, repo_root, _VLA_TASK_NAME)
    request.addfinalizer(log_stream.stop)

    log_e2e(f"Waiting for OSMO VLA fine-tuning workflow {workflow.workflow_id} to start")
    wait_until_osmo_started(workflow, repo_root)
    log_e2e(f"Waiting for OSMO VLA fine-tuning workflow {workflow.workflow_id} to complete")
    # GR00T provisions its training environment inside the workflow before the short fine-tune starts.
    wait_until_osmo_completed(workflow, repo_root, timeout_minutes=45)
    log_stream.stop()
    log_e2e("Validating OSMO VLA fine-tuning workflow task success")
    assert_workflow_task_succeeded(workflow, repo_root, _VLA_TASK_NAME)
    provenance = _vla_runtime_provenance(fetch_workflow_task_logs(workflow, repo_root, _VLA_TASK_NAME))
    assert provenance["isaac_groot_ref"]
    assert provenance["base_model"]
    assert provenance["base_model_revision"]
    assert re.fullmatch(r"[0-9a-f]{40}", str(provenance["isaac_groot_ref"]))
    assert re.fullmatch(r"[0-9a-f]{40}", str(provenance["base_model_revision"]))
    runtime_versions = provenance["runtime_versions"]
    expected_runtime_versions = provenance["expected_runtime_versions"]
    assert isinstance(runtime_versions, dict)
    assert runtime_versions == expected_runtime_versions
    run_id = assert_osmo_vla_has_mlflow_tracking(workflow, aml_workspace)
    request.addfinalizer(lambda: delete_mlflow_run(aml_workspace, run_id))
    model = resolve_registered_model(repo_root, aml_workspace, model_name=register_model_name)
    workflow.handle.resource_identifiers["azureml_model"] = f"{model.name}:{model.version}"
    log_e2e("OSMO VLA fine-tuning e2e test finished successfully")
