"""Tests for VLA calibration execution semantics."""

from __future__ import annotations

import signal
import subprocess
from pathlib import Path

import pytest
import yaml

from training.vla.scripts import calibrate_vla

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CALIBRATION_COMPONENT = _REPO_ROOT / "training/vla/workflows/azureml/components/calibrate.yaml"
_PIPELINE = _REPO_ROOT / "training/vla/workflows/azureml/vla-training-pipeline.yaml"


@pytest.mark.parametrize("train_expert_only", [False, True])
def test_given_resolved_trainable_scope_when_probe_arguments_built_then_value_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    train_expert_only: bool,
) -> None:
    monkeypatch.setenv("DATASET_REPO_ID", "org/dataset")

    arguments = calibrate_vla._build_probe_arguments(
        ("--policy.type=smolvla",),
        2,
        tmp_path / "probe",
        train_expert_only,
    )

    assert f"--policy.train_expert_only={str(train_expert_only).lower()}" in arguments


def test_given_mixed_precision_when_candidate_runs_then_accelerate_launch_matches_training(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    process = mocker.MagicMock()
    process.wait.return_value = -signal.SIGKILL
    popen = mocker.patch.object(calibrate_vla.subprocess, "Popen", return_value=process)

    result = calibrate_vla._run_candidate(
        1,
        ("--dataset.repo_id=org/dataset", "--policy.type=smolvla"),
        tmp_path,
        60,
        1,
        "bf16",
        False,
    )

    command = popen.call_args.args[0]
    assert command[:4] == ["accelerate", "launch", "--num_processes=1", "--mixed_precision=bf16"]
    assert result["outcome"] == "oom"


def test_given_successful_probe_when_candidate_runs_then_result_is_returned_without_exposing_token(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    sentinel_token = "sentinel-token-must-not-appear"
    monkeypatch.setenv("HF_TOKEN", sentinel_token)
    process = mocker.MagicMock()
    popen = mocker.patch.object(calibrate_vla.subprocess, "Popen", return_value=process)

    def write_success_result(*, timeout: int) -> int:
        assert timeout == 60
        (tmp_path / "batch-1.json").write_text(
            '{"micro_batch_size": 1, "outcome": "success"}',
            encoding="utf-8",
        )
        return 0

    process.wait.side_effect = write_success_result

    result = calibrate_vla._run_candidate(
        1,
        ("--dataset.repo_id=org/dataset", "--policy.type=pi05"),
        tmp_path,
        60,
        1,
        "bf16",
        True,
    )

    command = popen.call_args.args[0]
    output = capsys.readouterr()
    assert result["outcome"] == "success"
    assert sentinel_token not in " ".join(command)
    assert sentinel_token not in output.out
    assert sentinel_token not in output.err


def test_given_probe_timeout_when_candidate_runs_then_timeout_is_retained(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    process = mocker.MagicMock()
    process.wait.side_effect = subprocess.TimeoutExpired(cmd="accelerate", timeout=60)
    mocker.patch.object(calibrate_vla.subprocess, "Popen", return_value=process)
    terminate = mocker.patch.object(calibrate_vla, "_terminate_process_group")

    result = calibrate_vla._run_candidate(
        1,
        ("--dataset.repo_id=org/dataset", "--policy.type=pi05"),
        tmp_path,
        60,
        1,
        "bf16",
        True,
    )

    assert result == {"micro_batch_size": 1, "outcome": "timeout", "timeout_seconds": 60}
    terminate.assert_called_once_with(process)


def test_given_unexpected_probe_exit_when_candidate_runs_then_calibration_fails(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    process = mocker.MagicMock()
    process.wait.return_value = 17
    mocker.patch.object(calibrate_vla.subprocess, "Popen", return_value=process)

    with pytest.raises(calibrate_vla.CalibrationError, match="return code 17"):
        calibrate_vla._run_candidate(
            1,
            ("--dataset.repo_id=org/dataset", "--policy.type=pi05"),
            tmp_path,
            60,
            1,
            "bf16",
            True,
        )


def test_given_calibration_contract_when_parsed_then_only_secret_coordinates_are_wired() -> None:
    component = yaml.safe_load(_CALIBRATION_COMPONENT.read_text(encoding="utf-8"))
    pipeline = yaml.safe_load(_PIPELINE.read_text(encoding="utf-8"))
    calibration_inputs = pipeline["jobs"]["calibration_step"]["inputs"]

    expected = {"azure_client_id", "hf_key_vault_url", "hf_token_secret_name"}
    assert expected <= set(component["inputs"])
    assert expected <= set(calibration_inputs)
    assert "hf_token" not in component["inputs"]
    assert "hf_token" not in calibration_inputs
