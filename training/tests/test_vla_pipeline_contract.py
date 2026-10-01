"""Tests for VLA Azure ML component and training-lineage contracts."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from training.vla.scripts import sweep_failure_smoke
from training.vla.scripts.preflight_dataset import build_dataset_record
from training.vla.scripts.vla_contracts import RecordKind, load_record, write_record

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENTRYPOINT = _REPO_ROOT / "training/vla/scripts/azureml-component-entry.sh"
_COMPONENT_ROOT = _REPO_ROOT / "training/vla/workflows/azureml/components"
_PIPELINE = _REPO_ROOT / "training/vla/workflows/azureml/vla-training-pipeline.yaml"
_CALIBRATION_SWEEP = _REPO_ROOT / "training/vla/workflows/azureml/vla-calibration-sweep.yaml"
_SWEEP_SMOKE = _REPO_ROOT / "training/vla/workflows/azureml/sweep-failure-smoke.yaml"
_VLA_README = _REPO_ROOT / "training/vla/README.md"
_ARC_SETUP = _REPO_ROOT / "docs/training/vla-azureml-arc-setup.md"


def _write_dataset(root: Path) -> Path:
    (root / "meta").mkdir(parents=True)
    (root / "data").mkdir()
    info = {
        "codebase_version": "v3.0",
        "total_episodes": 1,
        "features": {"observation.images.base": {"dtype": "video", "shape": [224, 224, 3]}},
    }
    (root / "meta/info.json").write_text(json.dumps(info), encoding="utf-8")
    (root / "data/episode.parquet").write_bytes(b"episode")
    return root


def _training_environment(tmp_path: Path, trainer: Path) -> tuple[dict[str, str], Path]:
    dataset = _write_dataset(tmp_path / "dataset")
    manifest_dir = tmp_path / "manifest"
    manifest = build_dataset_record(
        dataset,
        "azureml:robot-data:7",
        "org/robot-data",
        "2026-09-28T00:00:00Z",
    )
    write_record(manifest_dir / "dataset.json", manifest)
    workload_dir = tmp_path / "workload"
    workload_dir.mkdir()
    (workload_dir / "workload.json").write_text('{"workload":"test"}\n', encoding="utf-8")
    calibration_dir = tmp_path / "calibration"
    calibration_dir.mkdir()
    checkpoints = tmp_path / "checkpoints"
    training_record = tmp_path / "training-record"
    values = {
        "adapter_name": "lerobot-pi",
        "subscription_id": "subscription",
        "resource_group": "resource-group",
        "workspace_name": "workspace",
        "dataset_repo_id": "org/robot-data",
        "dataset_asset_id": "azureml:robot-data:7",
        "policy_type": "pi0",
        "init_from_policy_hf_repo_id": "org/model",
        "init_from_policy_hf_revision": "a" * 40,
        "train_expert_only": "false",
        "gradient_checkpointing": "false",
        "use_imagenet_stats": "false",
        "training_steps": "1",
        "save_freq": "1",
        "log_freq": "1",
        "job_name": "test-run",
        "output_dir": str(tmp_path / "local-output"),
        "mixed_precision": "bf16",
        "code_repository": "org/repo",
        "code_revision": "b" * 40,
        "compute_target": "gpu",
        "runtime_image": f"image@sha256:{'c' * 64}",
    }
    environment = os.environ.copy()
    environment.update({f"AZUREML_PARAMETER_{name}": value for name, value in values.items()})
    environment.update(
        {
            "AZUREML_RUN_ID": "evidence-job",
            "VLA_SHARED_TRAIN_ENTRY": str(trainer),
            "AZURE_ML_INPUT_dataset": str(dataset),
            "AZURE_ML_INPUT_dataset_manifest": str(manifest_dir),
            "AZURE_ML_INPUT_calibration_report": str(calibration_dir),
            "AZURE_ML_INPUT_workload_contract": str(workload_dir),
            "AZURE_ML_OUTPUT_checkpoints": str(checkpoints),
            "AZURE_ML_OUTPUT_training_record": str(training_record),
            "AZURE_ML_OUTPUT_candidate": str(tmp_path / "candidate"),
            "AZURE_ML_OUTPUT_candidate_manifest": str(tmp_path / "candidate-manifest"),
        }
    )
    return environment, training_record


def test_given_training_component_when_parsed_then_registration_is_absent() -> None:
    component = yaml.safe_load((_COMPONENT_ROOT / "train.yaml").read_text(encoding="utf-8"))

    assert "register_checkpoint" not in component["inputs"]
    assert {"dataset", "dataset_manifest", "dataset_asset_id"} <= set(component["inputs"])
    assert {"training_record", "candidate", "candidate_manifest"} <= set(component["outputs"])


def test_given_calibration_entrypoint_when_parsed_then_trial_creates_its_manifest() -> None:
    entrypoint = _ENTRYPOINT.read_text(encoding="utf-8")
    calibrate_mode = entrypoint.split("  calibrate)\n", maxsplit=1)[1].split("  train)\n", maxsplit=1)[0]

    assert "dataset_manifest_dir=$(mktemp -d)" in calibrate_mode
    assert '--manifest-output "${dataset_manifest_dir}"' in calibrate_mode
    assert "AZURE_ML_INPUT_dataset_manifest" not in calibrate_mode


def test_given_training_pipeline_when_parsed_then_only_training_stages_are_serialized() -> None:
    pipeline = yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))
    jobs = pipeline["jobs"]

    assert list(jobs) == [
        "preflight_step",
        "training_step",
    ]
    assert pipeline["settings"]["continue_on_step_failure"] is False
    assert pipeline["identity"] == {"type": "managed_identity"}
    assert "register_checkpoint" not in pipeline["inputs"]
    assert "dataset_revision" not in pipeline["inputs"]
    assert {"workload_contract", "calibration_report"} <= set(pipeline["inputs"])
    assert {"candidate_batch_sizes", "compute_calibrate"}.isdisjoint(pipeline["inputs"])
    assert "compute_finalize" not in pipeline["inputs"]
    assert all("compute" in job for job in jobs.values())
    assert jobs["training_step"]["inputs"]["dataset"] == "${{parent.inputs.dataset}}"
    assert jobs["training_step"]["inputs"]["calibration_report"] == "${{parent.inputs.calibration_report}}"
    assert jobs["training_step"]["inputs"]["workload_contract"] == "${{parent.inputs.workload_contract}}"
    assert jobs["training_step"]["resources"]["instance_type"] == "gpu-high-memory"
    assert jobs["training_step"]["outputs"]["candidate"] == "${{parent.outputs.candidate}}"
    assert jobs["training_step"]["outputs"]["candidate_manifest"] == "${{parent.outputs.candidate_manifest}}"
    assert {
        "dataset_manifest",
        "checkpoints",
        "training_record",
        "candidate",
        "candidate_manifest",
    } == set(pipeline["outputs"])
    assert {"promotion_policy", "model_name", "eval_episodes", "compute_evaluate", "compute_decide"}.isdisjoint(
        pipeline["inputs"]
    )


def test_given_arc_submission_docs_when_read_then_selected_outputs_use_datastore_paths() -> None:
    for document in (_VLA_README, _ARC_SETUP):
        content = document.read_text(encoding="utf-8")

        assert "properties.best_child_run_id" in content
        assert "workspaceblobstore/paths/azureml/$BEST_CALIBRATION_RUN" in content
        assert "azureml://jobs/$CALIBRATION_JOB/outputs" not in content


def test_given_calibration_sweep_when_parsed_then_trials_are_isolated_and_outputs_are_selectable() -> None:
    sweep = yaml.safe_load(_CALIBRATION_SWEEP.read_text(encoding="utf-8"))

    assert sweep["type"] == "sweep"
    assert sweep["sampling_algorithm"] == "grid"
    assert sweep["search_space"]["micro_batch_size"] == {"type": "choice", "values": [1, 2, 4]}
    assert sweep["objective"] == {"goal": "maximize", "primary_metric": "safe_micro_batch_size"}
    assert sweep["limits"]["max_total_trials"] == 3
    assert sweep["limits"]["max_concurrent_trials"] == 1
    assert sweep["resources"]["instance_type"] == "gpu-high-memory"
    assert sweep["trial"]["resources"]["instance_type"] == "gpu-high-memory"
    assert sweep["trial"]["code"] == "../../.."
    assert "${{search_space.micro_batch_size}}" in sweep["trial"]["command"]
    assert set(sweep["outputs"]) == {"workload_contract", "calibration_report"}


def test_given_training_components_when_parsed_then_code_assets_are_training_scoped() -> None:
    components = ("preflight.yaml", "train.yaml")

    for component_name in components:
        component = yaml.safe_load((_COMPONENT_ROOT / component_name).read_text(encoding="utf-8"))
        assert component["code"] == "../../../.."


def test_given_sweep_smoke_when_parsed_then_failure_is_isolated_and_success_is_selectable() -> None:
    sweep = yaml.safe_load(_SWEEP_SMOKE.read_text(encoding="utf-8"))

    assert sweep["type"] == "sweep"
    assert sweep["sampling_algorithm"] == "grid"
    assert sweep["search_space"]["mode"] == {"type": "choice", "values": ["success", "fail"]}
    assert sweep["objective"] == {"goal": "maximize", "primary_metric": "sweep_smoke_score"}
    assert sweep["limits"]["max_total_trials"] == 2
    assert sweep["limits"]["max_concurrent_trials"] == 1
    assert sweep["resources"]["instance_type"] == "defaultinstancetype"
    assert sweep["trial"]["resources"]["instance_type"] == "defaultinstancetype"
    assert sweep["trial"]["code"] == "../../scripts"
    assert sweep["trial"]["environment"] == {"image": "mcr.microsoft.com/azureml/openmpi5.0-ubuntu24.04:latest"}
    assert set(sweep["outputs"]) == {"smoke_result"}
    assert "azureml-mlflow==1.62.0.post6" in sweep["trial"]["command"]
    assert "mlflow-skinny==3.13.0" in sweep["trial"]["command"]
    assert "${{search_space.mode}}" in sweep["trial"]["command"]
    assert "${{outputs.smoke_result}}" in sweep["trial"]["command"]


def test_given_successful_sweep_smoke_trial_when_run_then_result_and_metric_are_published(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    log_metric = mocker.patch.object(sweep_failure_smoke.mlflow, "log_metric")

    result = sweep_failure_smoke.run(SimpleNamespace(mode="success", output_dir=tmp_path))

    assert result == 0
    assert json.loads((tmp_path / "result.json").read_text(encoding="utf-8")) == {
        "mode": "success",
        "selected": True,
    }
    log_metric.assert_called_once_with(sweep_failure_smoke.SMOKE_METRIC, 1)


def test_given_failed_sweep_smoke_trial_when_run_then_it_fails_without_metric(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    log_metric = mocker.patch.object(sweep_failure_smoke.mlflow, "log_metric")

    with pytest.raises(RuntimeError, match="Deliberate sweep trial failure"):
        sweep_failure_smoke.run(SimpleNamespace(mode="fail", output_dir=tmp_path))

    assert not (tmp_path / "result.json").exists()
    log_metric.assert_not_called()


def test_given_successful_trainer_when_entrypoint_returns_then_run_record_exists(tmp_path: Path) -> None:
    trainer = tmp_path / "trainer.sh"
    trainer.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nmkdir -p "${AZURE_ML_OUTPUT_CHECKPOINTS}/last/pretrained_model"\n'
        'printf \'{"type":"policy"}\\n\' >"${AZURE_ML_OUTPUT_CHECKPOINTS}/last/pretrained_model/config.json"\n'
        'printf model >"${AZURE_ML_OUTPUT_CHECKPOINTS}/last/pretrained_model/model.safetensors"\n',
        encoding="utf-8",
    )
    trainer.chmod(0o755)
    environment, training_record = _training_environment(tmp_path, trainer)

    result = subprocess.run(["bash", str(_ENTRYPOINT), "train"], cwd=_REPO_ROOT, env=environment, check=False)

    assert result.returncode == 0
    assert load_record(training_record / "training-record.json", RecordKind.RUN)["run_id"] == "evidence-job"
    assert (tmp_path / "candidate/model.safetensors").is_file()
    assert (tmp_path / "candidate-manifest/candidate-manifest.json").is_file()


def test_given_failed_trainer_when_entrypoint_returns_then_no_run_record_exists(tmp_path: Path) -> None:
    trainer = tmp_path / "trainer.sh"
    trainer.write_text("#!/usr/bin/env bash\nexit 17\n", encoding="utf-8")
    trainer.chmod(0o755)
    environment, training_record = _training_environment(tmp_path, trainer)

    result = subprocess.run(["bash", str(_ENTRYPOINT), "train"], cwd=_REPO_ROOT, env=environment, check=False)

    assert result.returncode == 17
    assert not training_record.exists()
