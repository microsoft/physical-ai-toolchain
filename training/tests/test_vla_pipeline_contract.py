"""Tests for VLA Azure ML component and training-lineage contracts."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

from training.vla.scripts.preflight_dataset import build_dataset_record
from training.vla.scripts.vla_contracts import RecordKind, load_record, write_record

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENTRYPOINT = _REPO_ROOT / "training/vla/scripts/azureml-component-entry.sh"
_COMPONENT_ROOT = _REPO_ROOT / "training/vla/workflows/azureml/components"
_PIPELINE = _REPO_ROOT / "training/vla/workflows/azureml/vla-training-pipeline.yaml"


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
        }
    )
    return environment, training_record


def test_given_training_component_when_parsed_then_registration_is_absent() -> None:
    component = yaml.safe_load((_COMPONENT_ROOT / "train.yaml").read_text(encoding="utf-8"))

    assert "register_checkpoint" not in component["inputs"]
    assert {"dataset", "dataset_manifest", "dataset_asset_id"} <= set(component["inputs"])
    assert "training_record" in component["outputs"]


def test_given_evaluation_component_when_parsed_then_finalized_evidence_is_required() -> None:
    component = yaml.safe_load((_COMPONENT_ROOT / "evaluate.yaml").read_text(encoding="utf-8"))

    assert {
        "candidate",
        "candidate_manifest",
        "training_record",
        "dataset",
        "dataset_manifest",
        "dataset_asset_id",
    } <= set(component["inputs"])
    assert set(component["outputs"]) == {"evaluation"}
    assert component["inputs"]["candidate"]["mode"] == "ro_mount"
    assert "training/vla/lerobot" in component["command"]
    assert 'REGISTER_MODEL="none"' in component["command"]
    assert "find " not in component["command"]


def test_given_evidence_pipeline_when_parsed_then_six_stages_are_serialized() -> None:
    pipeline = yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))
    jobs = pipeline["jobs"]

    assert list(jobs) == [
        "preflight_step",
        "calibration_step",
        "training_step",
        "finalize_step",
        "evaluate_step",
        "decide_step",
    ]
    assert pipeline["settings"]["continue_on_step_failure"] is False
    assert pipeline["identity"] == {"type": "managed_identity"}
    assert "register_checkpoint" not in pipeline["inputs"]
    assert "dataset_revision" not in pipeline["inputs"]
    assert all("compute" in job for job in jobs.values())
    assert jobs["calibration_step"]["inputs"]["dataset"] == "${{parent.inputs.dataset}}"
    assert jobs["training_step"]["inputs"]["dataset"] == "${{parent.inputs.dataset}}"
    assert jobs["evaluate_step"]["inputs"]["dataset"] == "${{parent.inputs.dataset}}"
    assert jobs["evaluate_step"]["inputs"]["candidate"] == "${{parent.jobs.finalize_step.outputs.candidate}}"
    assert jobs["decide_step"]["inputs"]["evaluation"] == "${{parent.jobs.evaluate_step.outputs.evaluation}}"
    assert {
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
    } == set(pipeline["outputs"])


def test_given_successful_trainer_when_entrypoint_returns_then_run_record_exists(tmp_path: Path) -> None:
    trainer = tmp_path / "trainer.sh"
    trainer.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nmkdir -p "${AZURE_ML_OUTPUT_CHECKPOINTS}/last/pretrained_model"\n'
        'printf model >"${AZURE_ML_OUTPUT_CHECKPOINTS}/last/pretrained_model/model.safetensors"\n',
        encoding="utf-8",
    )
    trainer.chmod(0o755)
    environment, training_record = _training_environment(tmp_path, trainer)

    result = subprocess.run(["bash", str(_ENTRYPOINT), "train"], cwd=_REPO_ROOT, env=environment, check=False)

    assert result.returncode == 0
    assert load_record(training_record / "training-record.json", RecordKind.RUN)["run_id"] == "evidence-job"


def test_given_failed_trainer_when_entrypoint_returns_then_no_run_record_exists(tmp_path: Path) -> None:
    trainer = tmp_path / "trainer.sh"
    trainer.write_text("#!/usr/bin/env bash\nexit 17\n", encoding="utf-8")
    trainer.chmod(0o755)
    environment, training_record = _training_environment(tmp_path, trainer)

    result = subprocess.run(["bash", str(_ENTRYPOINT), "train"], cwd=_REPO_ROOT, env=environment, check=False)

    assert result.returncode == 17
    assert not training_record.exists()
