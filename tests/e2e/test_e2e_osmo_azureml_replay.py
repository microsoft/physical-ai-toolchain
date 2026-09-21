"""End-to-end test for replaying persisted OSMO output into AzureML.

Seeds a minimal checkpoint in OSMO shared output storage, submits the production
replay wrapper, and validates MLflow artifacts and AzureML model registration.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_azureml_replay.py
```
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e._aml import (
    AzureMLWorkspace,
    archive_all_model_versions,
    assert_registered_model_has_artifacts,
    resolve_registered_model,
)
from tests.e2e._common import e2e_name, log_e2e
from tests.e2e._mlflow import assert_osmo_replay_has_mlflow_run
from tests.e2e._osmo import (
    assert_workflow_task_succeeded,
    cleanup_osmo_replay_output,
    monitor_osmo_workflow,
    submit_osmo_azureml_replay,
    submit_osmo_replay_output_fixture,
)

_OUTPUT_TASK_NAME = "manage-output"
_REPLAY_TASK_NAME = "mirror"


@pytest.mark.e2e
@pytest.mark.usefixtures("ensure_osmo_cli_available")
def test_osmo_azureml_replay_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    monkeypatch: pytest.MonkeyPatch,
    repo_root: Path,
) -> None:
    model_name = e2e_name("replay-e2e-osmo-model")
    request.addfinalizer(lambda: archive_all_model_versions(repo_root, aml_workspace, model_name))

    log_e2e("Seeding an OSMO persisted replay output fixture")
    seed_workflow, source_output_uri = submit_osmo_replay_output_fixture(repo_root)
    request.addfinalizer(lambda: cleanup_osmo_replay_output(repo_root, source_output_uri))
    monitor_osmo_workflow(request, seed_workflow, repo_root, _OUTPUT_TASK_NAME, phase="replay fixture seed")
    assert_workflow_task_succeeded(seed_workflow, repo_root, _OUTPUT_TASK_NAME)

    source_run_id = seed_workflow.workflow_id
    monkeypatch.setenv("OSMO_OUTPUT_SUBPATH", "run-e2e")
    replay_workflow = submit_osmo_azureml_replay(
        repo_root,
        source_run_id=source_run_id,
        source_output_uri=source_output_uri,
        model_name=model_name,
    )
    monitor_osmo_workflow(request, replay_workflow, repo_root, _REPLAY_TASK_NAME, phase="AzureML replay")
    assert_workflow_task_succeeded(replay_workflow, repo_root, _REPLAY_TASK_NAME)
    assert_osmo_replay_has_mlflow_run(
        source_run_id=source_run_id,
        model_name=model_name,
        aml_workspace=aml_workspace,
    )
    model = resolve_registered_model(repo_root, aml_workspace, model_name=model_name)
    assert_registered_model_has_artifacts(repo_root, aml_workspace, model)
    log_e2e("OSMO AzureML replay e2e test finished successfully")
