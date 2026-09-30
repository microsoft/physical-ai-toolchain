"""Tests for VLA calibration execution semantics."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml

from training.vla.scripts import calibrate_vla

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CALIBRATION_COMPONENT = _REPO_ROOT / "training/vla/workflows/azureml/components/calibrate.yaml"
_PIPELINE = _REPO_ROOT / "training/vla/workflows/azureml/vla-training-pipeline.yaml"


def test_given_self_check_when_calibration_runs_then_contract_checks_pass(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = calibrate_vla.create_parser().parse_args(["--self-check"])

    result = calibrate_vla.run(args)

    assert result == calibrate_vla.EXIT_SUCCESS
    assert capsys.readouterr().out == "VLA calibration self-check passed\n"


def test_given_successful_candidate_when_calibration_runs_then_evidence_is_written(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    workload = calibrate_vla._sample_workload()
    workload_output = tmp_path / "workload"
    report_output = tmp_path / "report"
    args = calibrate_vla.create_parser().parse_args(
        [
            "--workload-output-dir",
            str(workload_output),
            "--output-dir",
            str(report_output),
            "--candidate-batch-sizes",
            "1,2",
        ]
    )
    mocker.patch.object(calibrate_vla, "_build_workload", return_value=workload)
    run_candidate = mocker.patch.object(
        calibrate_vla,
        "_run_candidate",
        side_effect=[
            {
                "micro_batch_size": 1,
                "outcome": "success",
                "device_name": "test-gpu",
                "peak_allocated_bytes": 50,
                "peak_reserved_bytes": 60,
                "free_bytes_after_step": 40,
                "total_bytes": 100,
                "duration_seconds": 0.5,
                "samples_per_second": 2.0,
                "optimizer_steps": 1,
            },
            {"micro_batch_size": 2, "outcome": "oom", "error_type": "OutOfMemoryError"},
        ],
    )

    result = calibrate_vla.run(args)

    report = yaml.safe_load((report_output / "calibration-report.json").read_text(encoding="utf-8"))
    assert result == calibrate_vla.EXIT_SUCCESS
    assert report["recommendation"]["micro_batch_size"] == 1
    assert (workload_output / "workload.json").is_file()
    assert run_candidate.call_count == 2


def test_given_one_optimizer_step_when_probe_runs_then_measurements_are_written(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    probe_output = tmp_path / "probe.json"
    update_policy = mocker.Mock(return_value="updated")
    lerobot_train = SimpleNamespace(update_policy=update_policy)
    lerobot_train.main = lambda: lerobot_train.update_policy()
    cuda = SimpleNamespace(
        is_available=lambda: True,
        device_count=lambda: 1,
        synchronize=lambda: None,
        mem_get_info=lambda: (40, 100),
        get_device_name=lambda: "test-gpu",
        max_memory_allocated=lambda: 50,
        max_memory_reserved=lambda: 60,
        empty_cache=mocker.Mock(),
        OutOfMemoryError=RuntimeError,
    )
    torch_module = ModuleType("torch")
    torch_module.cuda = cuda
    lerobot_module = ModuleType("lerobot")
    scripts_module = ModuleType("lerobot.scripts")
    scripts_module.lerobot_train = lerobot_train
    mocker.patch.dict(
        sys.modules,
        {"torch": torch_module, "lerobot": lerobot_module, "lerobot.scripts": scripts_module},
    )
    args = calibrate_vla.create_parser().parse_args(
        [
            "--probe",
            "--probe-output",
            str(probe_output),
            "--probe-batch-size",
            "2",
            "--expected-world-size",
            "1",
            "--",
            "--policy.type=smolvla",
        ]
    )

    result = calibrate_vla.run(args)

    measurements = yaml.safe_load(probe_output.read_text(encoding="utf-8"))
    assert result == calibrate_vla.EXIT_SUCCESS
    assert measurements["optimizer_steps"] == 1
    assert measurements["peak_reserved_bytes"] == 60
    assert lerobot_train.update_policy is update_policy
    cuda.empty_cache.assert_called_once()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--probe"], "Probe mode requires"),
        (["--resolve-training-batch-size"], "Training batch-size resolution requires"),
        ([], "Calibration requires"),
        (
            ["--workload-output-dir", "workload", "--output-dir", "report", "--headroom-fraction", "1"],
            "headroom-fraction",
        ),
        (
            ["--workload-output-dir", "workload", "--output-dir", "report", "--probe-timeout-seconds", "0"],
            "probe-timeout-seconds",
        ),
    ],
)
def test_given_invalid_cli_inputs_when_calibration_runs_then_it_is_rejected(
    arguments: list[str],
    message: str,
) -> None:
    args = calibrate_vla.create_parser().parse_args(arguments)

    with pytest.raises(calibrate_vla.CalibrationError, match=message):
        calibrate_vla.run(args)


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
