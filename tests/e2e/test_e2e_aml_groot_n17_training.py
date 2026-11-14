"""
End-to-end lifecycle test for Azure ML GR00T N1.7 fine-tuning.

Registers a synthetic GR00T dataset as a versioned Azure ML data asset, then runs
four checks through ``training/vla/scripts/submit-azureml-groot-training.sh``:

1. A preflight job that resolves the Hugging Face token from Key Vault through the
   Azure ML job identity and validates GPUs, dataset metadata, and the modality config.
2. A short training job that writes checkpoints to the ``checkpoints`` output.
3. A resume job that restores the latest checkpoint from the training job output.
4. Model registration from the resume job.

The test logs every Azure ML job name, terminal state, data asset, and model version
so the run can be cited as evidence.

Required environment:

* ``E2E_GROOT_HF_TOKEN_SECRET_URL``: Key Vault secret URL holding a Hugging Face token
  with access to the gated ``nvidia/Cosmos-Reason2-2B`` backbone. The compute identity
  needs ``Key Vault Secrets User`` on the secret.

Optional environment:

* ``E2E_AML_INSTANCE_TYPE``: InstanceType for Azure ML Kubernetes compute.
* ``E2E_GROOT_NUM_GPUS``: GPUs per job (default ``1``); must match the InstanceType.
* ``AZUREML_COMPUTE``: compute target override.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_groot_n17_training.py
```
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from tests.e2e._aml import (
    AzureMLJob,
    AzureMLWorkspace,
    aml_job_output_datastore_uri,
    archive_aml_data_asset,
    assert_aml_job_logs_contain,
    assert_job_has_checkpoint,
    assert_registered_model_has_blob_artifacts,
    cancel_aml_job,
    cleanup_aml_job_and_model_versions,
    register_aml_uri_folder_data_asset,
    resolve_registered_model,
    submit_aml_groot_n17_training,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import e2e_name, log_e2e, parse_provenance_marker

_HF_TOKEN_SECRET_URL_ENV = "E2E_GROOT_HF_TOKEN_SECRET_URL"
_NUM_GPUS_ENV = "E2E_GROOT_NUM_GPUS"
_KEY_VAULT_MARKER = "[azureml] loaded Hugging Face token from Key Vault"
_PROVENANCE_MARKER = "VLA_RUNTIME_PROVENANCE="
_TRAIN_STEPS = 2
_RESUME_STEPS = 4
# Kubernetes GPU nodes scale from zero and the default image installs GR00T at job start.
_START_TIMEOUT_MINUTES = 40
_COMPLETE_TIMEOUT_MINUTES = 90


def _run_to_completion(job: AzureMLJob, repo_root: Path, phase: str) -> None:
    log_e2e(f"Waiting for AzureML GR00T N1.7 {phase} job {job.name} to start")
    wait_until_aml_started(job, repo_root, timeout_minutes=_START_TIMEOUT_MINUTES, poll_interval_seconds=30)
    log_e2e(f"Waiting for AzureML GR00T N1.7 {phase} job {job.name} to complete")
    wait_until_aml_completed(job, repo_root, timeout_minutes=_COMPLETE_TIMEOUT_MINUTES, poll_interval_seconds=30)


@pytest.mark.e2e
@pytest.mark.usefixtures("aml_compute_target")
def test_aml_groot_n17_lifecycle_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    repo_root: Path,
) -> None:
    hf_token_secret_url = os.environ.get(_HF_TOKEN_SECRET_URL_ENV, "").strip()
    if not hf_token_secret_url:
        pytest.skip(f"{_HF_TOKEN_SECRET_URL_ENV} is not set")
    num_gpus = int(os.environ.get(_NUM_GPUS_ENV, "1"))

    # Imported lazily so the numpy/pyarrow/imageio generator stack only loads when the test runs.
    from tests.e2e._vla_dataset import (
        build_synthetic_dataset,
        validate_synthetic_dataset,
        write_n17_modality_config_file,
    )

    log_e2e("Starting AzureML GR00T N1.7 lifecycle e2e test")
    work_dir = Path(tempfile.mkdtemp(prefix="groot-n17-e2e-"))
    request.addfinalizer(lambda: shutil.rmtree(work_dir, ignore_errors=True))
    dataset_dir = build_synthetic_dataset(work_dir / "dataset")
    validate_synthetic_dataset(dataset_dir)
    modality_config = write_n17_modality_config_file(work_dir / "e2e_synth_modality_config.py")

    dataset_name = e2e_name("groot-n17-e2e-dataset")
    request.addfinalizer(lambda: archive_aml_data_asset(repo_root, aml_workspace, dataset_name))
    dataset_asset = register_aml_uri_folder_data_asset(
        repo_root, aml_workspace, name=dataset_name, version="1", local_path=dataset_dir
    )

    common = {
        "dataset_asset": dataset_asset,
        "modality_config": modality_config,
        "hf_token_secret_url": hf_token_secret_url,
        "num_gpus": num_gpus,
        "batch_size": num_gpus,
        "save_steps": 1,
    }

    preflight_job = submit_aml_groot_n17_training(
        repo_root, aml_workspace, max_steps=_TRAIN_STEPS, preflight_only=True, **common
    )
    request.addfinalizer(lambda: cancel_aml_job(preflight_job, repo_root))
    _run_to_completion(preflight_job, repo_root, "preflight")
    assert_aml_job_logs_contain(preflight_job, repo_root, [_KEY_VAULT_MARKER, "TRAIN_RESULT=PREFLIGHT_PASS"])

    train_job = submit_aml_groot_n17_training(repo_root, aml_workspace, max_steps=_TRAIN_STEPS, **common)
    request.addfinalizer(lambda: cancel_aml_job(train_job, repo_root))
    _run_to_completion(train_job, repo_root, "training")
    train_logs = assert_aml_job_logs_contain(train_job, repo_root, [_KEY_VAULT_MARKER])
    train_provenance = parse_provenance_marker(train_logs, _PROVENANCE_MARKER)
    assert train_provenance["resume_from_checkpoint"] is False
    assert_job_has_checkpoint(train_job)

    register_model_name = e2e_name("groot-n17-e2e-aml-model")
    resume_job = submit_aml_groot_n17_training(
        repo_root,
        aml_workspace,
        max_steps=_RESUME_STEPS,
        resume_checkpoint=aml_job_output_datastore_uri(train_job, repo_root, "checkpoints"),
        register_model_name=register_model_name,
        **common,
    )
    request.addfinalizer(
        lambda: cleanup_aml_job_and_model_versions(resume_job, repo_root, aml_workspace, register_model_name)
    )
    _run_to_completion(resume_job, repo_root, "resume")
    resume_logs = assert_aml_job_logs_contain(
        resume_job,
        repo_root,
        [_KEY_VAULT_MARKER, "[setup] patched N1.7 Trainer for deterministic data resume"],
    )
    linked = [line for line in resume_logs.splitlines() if "[azureml] linked resume checkpoint: " in line]
    assert linked, "resume job did not link a checkpoint from the training job output"
    assert linked[-1].rstrip().endswith(f"checkpoint-{_TRAIN_STEPS}"), linked[-1]
    resume_provenance = parse_provenance_marker(resume_logs, _PROVENANCE_MARKER)
    assert resume_provenance["resume_from_checkpoint"] is True
    assert resume_provenance["isaac_groot_ref"] == train_provenance["isaac_groot_ref"]
    assert_job_has_checkpoint(resume_job)

    model = resolve_registered_model(repo_root, aml_workspace, model_name=register_model_name)
    assert_registered_model_has_blob_artifacts(repo_root, aml_workspace, model)

    log_e2e(
        "AzureML GR00T N1.7 lifecycle evidence: "
        f"dataset={dataset_asset}; "
        f"preflight={preflight_job.name}:{preflight_job.terminal_status}; "
        f"train={train_job.name}:{train_job.terminal_status}; "
        f"resume={resume_job.name}:{resume_job.terminal_status}; "
        f"model={model.name}:{model.version}; "
        f"isaac_groot_ref={train_provenance['isaac_groot_ref']}"
    )
    log_e2e("AzureML GR00T N1.7 lifecycle e2e test finished successfully")
