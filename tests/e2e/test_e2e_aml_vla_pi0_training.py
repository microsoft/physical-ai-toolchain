"""
End-to-end lifecycle test for the Azure ML VLA pi0 evidence and promotion pipelines.

Registers a synthetic LeRobot dataset as an immutable Azure ML asset, runs the
six-stage evidence pipeline, proves the default path does not mutate the model
registry, exercises guarded-promotion denial paths, and registers the exact
candidate only after a passed decision.

```shell
uv run pytest -vv -s -m e2e tests/e2e/test_e2e_aml_vla_pi0_training.py
```
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.e2e._aml import (
    AzureMLJob,
    AzureMLWorkspace,
    archive_all_model_versions,
    assert_job_has_checkpoint,
    cancel_aml_job,
    download_aml_job_output,
    list_aml_model_versions,
    resolve_aml_job_output_uri,
    resolve_registered_model,
    submit_aml_vla_pipeline,
    submit_aml_vla_promotion,
    wait_until_aml_completed,
    wait_until_aml_failed,
    wait_until_aml_started,
)
from tests.e2e._common import e2e_name, log_e2e, run_command
from tests.e2e._lerobot_dataset import register_synthetic_lerobot_data_asset

_OUTPUT_NAMES = (
    "dataset_manifest",
    "workload_contract",
    "calibration_report",
    "checkpoints",
    "training_record",
    "candidate",
    "candidate_manifest",
    "evaluation",
    "policy",
    "decision",
)


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} is required for the Azure ML VLA evidence lifecycle")
    return value


def _repository_revision(repo_root: Path) -> str:
    result = run_command(["git", "rev-parse", "HEAD"], cwd=repo_root)
    revision = result.stdout.strip()
    if result.returncode != 0 or len(revision) != 40:
        raise AssertionError("Unable to resolve the repository commit for VLA evidence")
    return revision


def _find_output_file(root: Path, filename: str) -> Path:
    matches = list(root.rglob(filename))
    if len(matches) != 1:
        raise AssertionError(f"Expected one {filename!r} under {root}, found {len(matches)}")
    return matches[0]


def _write_promotion_policy(
    path: Path,
    *,
    producer_identity: str,
    pipeline_contract_fingerprint: str,
    code_revision: str,
) -> None:
    policy = {
        "schema_version": 1,
        "minimum_episodes": 1,
        "metric_rules": {
            "mse": {"operator": "lte", "limit": 1.0e12},
            "avg_inference_ms": {"operator": "lte", "limit": 1.0e12},
        },
        "allowed_producer_identity": producer_identity,
        "pipeline_contract_fingerprint": pipeline_contract_fingerprint,
        "code_revision": code_revision,
    }
    path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")


def _assert_denied_without_registry_mutation(
    job: AzureMLJob,
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    model_name: str,
    expected_versions: list[int],
) -> None:
    wait_until_aml_failed(job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    assert list_aml_model_versions(repo_root, aml_workspace, model_name) == expected_versions


@pytest.mark.e2e
@pytest.mark.requires_hf_token
def test_aml_vla_pi0_lifecycle_e2e(
    request: pytest.FixtureRequest,
    aml_workspace: AzureMLWorkspace,
    aml_compute_target: str,
    repo_root: Path,
    tmp_path: Path,
) -> None:
    producer_identity = _required_environment("E2E_AML_PRODUCER_IDENTITY")
    source_model_revision = _required_environment("E2E_VLA_PI0_HF_REVISION")
    _required_environment("HF_KEY_VAULT_URL")
    _required_environment("HF_TOKEN_SECRET_NAME")
    dataset_asset = register_synthetic_lerobot_data_asset(request, repo_root, aml_workspace)
    model_name = e2e_name("vla-pi0-e2e-aml-model")
    pipeline_path = repo_root / "training/vla/workflows/azureml/vla-training-pipeline.yaml"
    pipeline_contract_fingerprint = hashlib.sha256(pipeline_path.read_bytes()).hexdigest()
    code_revision = _repository_revision(repo_root)
    policy_path = tmp_path / "promotion-policy.json"
    _write_promotion_policy(
        policy_path,
        producer_identity=producer_identity,
        pipeline_contract_fingerprint=pipeline_contract_fingerprint,
        code_revision=code_revision,
    )
    jobs: list[AzureMLJob] = []

    def cleanup() -> None:
        for job in reversed(jobs):
            cancel_aml_job(job, repo_root)
        archive_all_model_versions(repo_root, aml_workspace, model_name)

    request.addfinalizer(cleanup)
    versions_before = list_aml_model_versions(repo_root, aml_workspace, model_name)
    evidence_job = submit_aml_vla_pipeline(
        repo_root,
        aml_workspace,
        dataset_asset=dataset_asset,
        dataset_repo_id="e2e/synthetic-pusht",
        promotion_policy=policy_path,
        model_name=model_name,
        compute_target=aml_compute_target,
        source_model_revision=source_model_revision,
        pipeline_contract_fingerprint=pipeline_contract_fingerprint,
        code_revision=code_revision,
        training_steps=2,
        eval_episodes=1,
    )
    jobs.append(evidence_job)
    wait_until_aml_started(evidence_job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    wait_until_aml_completed(evidence_job, repo_root, timeout_minutes=60, poll_interval_seconds=30)

    assert list_aml_model_versions(repo_root, aml_workspace, model_name) == versions_before
    for output_name in _OUTPUT_NAMES:
        expected = f"azureml://jobs/{evidence_job.name}/outputs/{output_name}"
        assert resolve_aml_job_output_uri(evidence_job, repo_root, output_name) == expected
    assert_job_has_checkpoint(evidence_job)

    decision_root = tmp_path / "decision"
    download_aml_job_output(evidence_job, repo_root, "decision", decision_root)
    decision_path = _find_output_file(decision_root, "promotion-decision.json")
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["status"] == "passed"

    non_passed_root = tmp_path / "non-passed-decision"
    non_passed_root.mkdir()
    non_passed_path = non_passed_root / "promotion-decision.json"
    decision["status"] = "failed"
    decision["reasons"] = ["e2e_forced_denial"]
    non_passed_path.write_text(json.dumps(decision, indent=2) + "\n", encoding="utf-8")
    non_passed_job = submit_aml_vla_promotion(
        repo_root,
        aml_workspace,
        evidence_job=evidence_job,
        model_name=model_name,
        compute_target=aml_compute_target,
        producer_identity=producer_identity,
        pipeline_contract_fingerprint=pipeline_contract_fingerprint,
        code_revision=code_revision,
        input_overrides={"inputs.decision.path": str(non_passed_root)},
    )
    jobs.append(non_passed_job)
    _assert_denied_without_registry_mutation(
        non_passed_job,
        repo_root,
        aml_workspace,
        model_name,
        versions_before,
    )

    candidate_root = tmp_path / "candidate"
    download_aml_job_output(evidence_job, repo_root, "candidate", candidate_root)
    candidate_config = _find_output_file(candidate_root, "config.json")
    candidate_config.write_text(candidate_config.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    tampered_job = submit_aml_vla_promotion(
        repo_root,
        aml_workspace,
        evidence_job=evidence_job,
        model_name=model_name,
        compute_target=aml_compute_target,
        producer_identity=producer_identity,
        pipeline_contract_fingerprint=pipeline_contract_fingerprint,
        code_revision=code_revision,
        input_overrides={"inputs.candidate.path": str(candidate_config.parent)},
    )
    jobs.append(tampered_job)
    _assert_denied_without_registry_mutation(
        tampered_job,
        repo_root,
        aml_workspace,
        model_name,
        versions_before,
    )

    forged_identity_job = submit_aml_vla_promotion(
        repo_root,
        aml_workspace,
        evidence_job=evidence_job,
        model_name=model_name,
        compute_target=aml_compute_target,
        producer_identity=f"forged-{producer_identity}",
        pipeline_contract_fingerprint=pipeline_contract_fingerprint,
        code_revision=code_revision,
    )
    jobs.append(forged_identity_job)
    _assert_denied_without_registry_mutation(
        forged_identity_job,
        repo_root,
        aml_workspace,
        model_name,
        versions_before,
    )

    promotion_job = submit_aml_vla_promotion(
        repo_root,
        aml_workspace,
        evidence_job=evidence_job,
        model_name=model_name,
        compute_target=aml_compute_target,
        producer_identity=producer_identity,
        pipeline_contract_fingerprint=pipeline_contract_fingerprint,
        code_revision=code_revision,
    )
    jobs.append(promotion_job)
    wait_until_aml_started(promotion_job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    wait_until_aml_completed(promotion_job, repo_root, timeout_minutes=15, poll_interval_seconds=30)
    versions_after = list_aml_model_versions(repo_root, aml_workspace, model_name)
    assert len(versions_after) == len(versions_before) + 1
    assert set(versions_before) < set(versions_after)
    resolve_registered_model(repo_root, aml_workspace, model_name=model_name)
    log_e2e("AzureML VLA pi0 evidence and guarded-promotion lifecycle passed")
