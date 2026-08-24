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

from pathlib import Path

import pytest

from tests.e2e._aml import AzureMLWorkspace
from tests.e2e._common import log_e2e
from tests.e2e._osmo import (
    _vla_base_model_args,
    assert_workflow_task_succeeded,
    cancel_osmo_workflow,
    start_task_pod_log_stream,
    submit_osmo_vla_finetune,
    wait_until_osmo_completed,
    wait_until_osmo_started,
)

_VLA_TASK_NAME = "train"


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
    workflow = submit_osmo_vla_finetune(
        repo_root,
        aml_workspace,
        request=request,
        max_steps=2,
        save_steps=2,
        batch_size=1,
        dataloader_workers=0,
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
    # MLflow mirroring only runs when Azure upload/model registration is enabled; this test omits those side effects.
    log_e2e("Skipping MLflow assertion because Azure upload/model registration is intentionally disabled")
    log_e2e("Validating OSMO VLA fine-tuning workflow task success")
    assert_workflow_task_succeeded(workflow, repo_root, _VLA_TASK_NAME)
    log_e2e("OSMO VLA fine-tuning e2e test finished successfully")
