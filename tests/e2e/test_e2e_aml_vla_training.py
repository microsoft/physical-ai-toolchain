"""
End-to-end test for the Azure ML VLA calibration sweep and training pipeline.

Registers a synthetic LeRobot dataset as an immutable Azure ML asset, runs the
standalone calibration sweep with SmolVLA, binds the selected trial's outputs to
the training pipeline, and verifies that training used the recommended
micro-batch and produced a SmolVLA candidate.

Calibration and training check out the current ``HEAD`` commit, so push it
before running the test.

```shell
uv run pytest -o addopts='' -vv -s -m e2e tests/e2e/test_e2e_aml_vla_training.py
```
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tests.e2e._aml import (
    AzureMLJob,
    AzureMLWorkspace,
    cancel_aml_job,
    download_aml_job_output,
    resolve_aml_sweep_best_child,
    submit_aml_vla_calibration_sweep,
    submit_aml_vla_pipeline,
    wait_until_aml_completed,
    wait_until_aml_started,
)
from tests.e2e._common import log_e2e, run_command
from tests.e2e._lerobot_dataset import register_synthetic_lerobot_data_asset

_SMOLVLA_REPO_ID = "lerobot/smolvla_base"
_SMOLVLA_REVISION = "d9f33c94a60fb382c90dea2164c96845bd955e28"
_CODE_REPOSITORY = "https://github.com/microsoft/physical-ai-toolchain.git"
_RENAME_MAP = {"observation.image": "observation.images.camera1"}


def _repository_revision(repo_root: Path) -> str:
    result = run_command(["git", "rev-parse", "HEAD"], cwd=repo_root)
    revision = result.stdout.strip()
    if result.returncode != 0 or len(revision) != 40:
        raise AssertionError("Unable to resolve the repository commit for VLA training")
    return revision


def _load_output_json(job: AzureMLJob, repo_root: Path, output_name: str, filename: str, root: Path) -> Any:
    download_root = root / output_name
    download_aml_job_output(job, repo_root, output_name, download_root)
    matches = list(download_root.rglob(filename))
    if len(matches) != 1:
        raise AssertionError(f"Expected one {filename!r} in output {output_name!r}, found {len(matches)}")
    return json.loads(matches[0].read_text(encoding="utf-8"))


@pytest.mark.e2e
def test_aml_vla_calibrated_training_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    aml_compute_target: str,
    repo_root: Path,
    tmp_path: Path,
) -> None:
    dataset_asset = register_synthetic_lerobot_data_asset(request, repo_root, aml_workspace)
    pipeline_path = repo_root / "training/vla/workflows/azureml/vla-training-pipeline.yaml"
    vla_inputs = {
        "dataset.path": dataset_asset,
        "dataset_asset_id": dataset_asset,
        "dataset_repo_id": "e2e/synthetic-pusht",
        "policy_type": "smolvla",
        "init_from_policy_hf_repo_id": _SMOLVLA_REPO_ID,
        "init_from_policy_hf_revision": _SMOLVLA_REVISION,
        "adapter_name": "lerobot-smolvla",
        "code_repository": _CODE_REPOSITORY,
        "code_revision": _repository_revision(repo_root),
        "train_expert_only": "true",
        "gradient_checkpointing": "false",
        "use_imagenet_stats": "false",
        "mixed_precision": "bf16",
        "policy_dtype": "none",
        "rename_map_b64": base64.b64encode(json.dumps(_RENAME_MAP).encode("utf-8")).decode("ascii"),
    }
    jobs: list[AzureMLJob] = []

    def cleanup() -> None:
        for job in reversed(jobs):
            cancel_aml_job(job, repo_root)

    request.addfinalizer(cleanup)

    sweep_job = submit_aml_vla_calibration_sweep(
        repo_root,
        aml_workspace,
        compute_target=aml_compute_target,
        vla_inputs=vla_inputs,
        headroom_fraction=0.1,
    )
    jobs.append(sweep_job)
    wait_until_aml_started(sweep_job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    wait_until_aml_completed(sweep_job, repo_root, timeout_minutes=180, poll_interval_seconds=60)
    calibration_trial = resolve_aml_sweep_best_child(sweep_job, repo_root)

    training_job = submit_aml_vla_pipeline(
        repo_root,
        aml_workspace,
        compute_target=aml_compute_target,
        vla_inputs=vla_inputs,
        calibration_trial=calibration_trial,
        pipeline_contract_fingerprint=hashlib.sha256(pipeline_path.read_bytes()).hexdigest(),
        training_steps=2,
    )
    jobs.append(training_job)
    wait_until_aml_started(training_job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    wait_until_aml_completed(training_job, repo_root, timeout_minutes=90, poll_interval_seconds=30)

    calibration_report = _load_output_json(
        calibration_trial, repo_root, "calibration_report", "calibration-report.json", tmp_path
    )
    training_record = _load_output_json(training_job, repo_root, "training_record", "training-record.json", tmp_path)
    candidate_manifest = _load_output_json(
        training_job, repo_root, "candidate_manifest", "candidate-manifest.json", tmp_path
    )

    recommended_batch = calibration_report["recommendation"]["micro_batch_size"]
    assert training_record["kind"] == "run"
    assert training_record["effective_batch"]["micro_batch_per_rank"] == recommended_batch
    assert candidate_manifest["kind"] == "candidate"
    assert candidate_manifest["policy_type"] == "smolvla"
    assert candidate_manifest["files"]
    log_e2e(f"AzureML VLA calibrated training passed: micro_batch_size={recommended_batch}")
