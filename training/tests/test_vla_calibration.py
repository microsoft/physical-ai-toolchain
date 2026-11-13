"""Tests for VLA calibration execution semantics."""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml

from tests.e2e._lerobot_dataset import _info_json as e2e_fixture_info
from tests.e2e.test_e2e_aml_vla_training import _RENAME_MAP as E2E_RENAME_MAP
from training.vla.scripts import calibrate_vla

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CALIBRATION_SWEEP = _REPO_ROOT / "training/vla/workflows/azureml/vla-calibration-sweep.yaml"


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


def test_given_safe_sweep_trial_when_calibration_runs_then_objective_is_logged_after_outputs(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    workload = calibrate_vla._sample_workload()
    workload_output = tmp_path / "workload"
    report_output = tmp_path / "report"
    args = calibrate_vla.create_parser().parse_args(
        [
            "--sweep-trial",
            "--workload-output-dir",
            str(workload_output),
            "--output-dir",
            str(report_output),
            "--candidate-batch-sizes",
            "2",
        ]
    )
    mocker.patch.object(calibrate_vla, "_build_workload", return_value=workload)
    mocker.patch.object(
        calibrate_vla,
        "_run_candidate",
        return_value={
            "micro_batch_size": 2,
            "outcome": "success",
            "device_name": "test-gpu",
            "peak_allocated_bytes": 50,
            "peak_reserved_bytes": 60,
            "free_bytes_after_step": 40,
            "total_bytes": 100,
            "duration_seconds": 0.5,
            "samples_per_second": 4.0,
            "optimizer_steps": 1,
        },
    )
    log_metric = mocker.Mock()
    mlflow_module = ModuleType("mlflow")
    mlflow_module.log_metric = log_metric
    mocker.patch.dict(sys.modules, {"mlflow": mlflow_module})

    result = calibrate_vla.run(args)

    assert result == calibrate_vla.EXIT_SUCCESS
    assert (workload_output / "workload.json").is_file()
    assert (report_output / "calibration-report.json").is_file()
    log_metric.assert_called_once_with(calibrate_vla.SWEEP_OBJECTIVE_METRIC, 2)


def test_given_unsafe_sweep_trial_when_calibration_runs_then_objective_is_not_logged(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    args = calibrate_vla.create_parser().parse_args(
        [
            "--sweep-trial",
            "--workload-output-dir",
            str(tmp_path / "workload"),
            "--output-dir",
            str(tmp_path / "report"),
            "--candidate-batch-sizes",
            "4",
        ]
    )
    mocker.patch.object(calibrate_vla, "_build_workload", return_value=calibrate_vla._sample_workload())
    mocker.patch.object(
        calibrate_vla,
        "_run_candidate",
        return_value={"micro_batch_size": 4, "outcome": "oom", "error_type": "OutOfMemoryError"},
    )
    mlflow_module = ModuleType("mlflow")
    mlflow_module.log_metric = mocker.Mock()
    mocker.patch.dict(sys.modules, {"mlflow": mlflow_module})

    with pytest.raises(calibrate_vla.CalibrationError, match="did not retain"):
        calibrate_vla.run(args)

    mlflow_module.log_metric.assert_not_called()


def test_given_multiple_candidates_when_sweep_trial_runs_then_it_is_rejected(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    args = calibrate_vla.create_parser().parse_args(
        [
            "--sweep-trial",
            "--workload-output-dir",
            str(tmp_path / "workload"),
            "--output-dir",
            str(tmp_path / "report"),
            "--candidate-batch-sizes",
            "1,2",
        ]
    )
    mocker.patch.object(calibrate_vla, "_build_workload", return_value=calibrate_vla._sample_workload())

    with pytest.raises(calibrate_vla.CalibrationError, match="exactly one"):
        calibrate_vla.run(args)


def test_given_one_optimizer_step_when_probe_runs_then_measurements_are_written(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    probe_output = tmp_path / "probe.json"
    update_policy = mocker.Mock(return_value="updated")
    make_policy = mocker.Mock()
    lerobot_train = SimpleNamespace(update_policy=update_policy, make_policy=make_policy)
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
    assert lerobot_train.make_policy is make_policy
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
    assert command[4] == str(Path(calibrate_vla.__file__).resolve())
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


def test_given_oom_probe_when_accelerate_exits_one_then_oom_result_is_returned(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    process = mocker.MagicMock()
    mocker.patch.object(calibrate_vla.subprocess, "Popen", return_value=process)

    def write_oom_result(*, timeout: int) -> int:
        (tmp_path / "batch-4.json").write_text(
            '{"micro_batch_size": 4, "outcome": "oom", "error_type": "OutOfMemoryError"}',
            encoding="utf-8",
        )
        return 1

    process.wait.side_effect = write_oom_result

    result = calibrate_vla._run_candidate(
        4,
        ("--dataset.repo_id=org/dataset", "--policy.type=smolvla"),
        tmp_path,
        60,
        1,
        "bf16",
        True,
    )

    assert result == {"micro_batch_size": 4, "outcome": "oom", "error_type": "OutOfMemoryError"}


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


def test_given_calibration_sweep_when_parsed_then_only_secret_coordinates_are_wired() -> None:
    sweep = yaml.safe_load(_CALIBRATION_SWEEP.read_text(encoding="utf-8"))
    command = sweep["trial"]["command"]

    assert command.startswith("env AZUREML_PARAMETER_adapter_name=")
    assert "${{search_space.micro_batch_size}}" in command
    assert "CALIBRATION_SWEEP_TRIAL=true" in command
    expected = {"azure_client_id", "hf_key_vault_url", "hf_token_secret_name"}
    assert expected <= set(sweep["inputs"])
    assert "hf_token" not in sweep["inputs"]


def _dataset_manifest(features: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": calibrate_vla.SCHEMA_VERSION,
        "kind": calibrate_vla.RecordKind.DATASET.value,
        "created_at": "2026-10-22T00:00:00Z",
        "asset_id": "azureml:robot-data:1",
        "dataset_repo_id": "org/robot-data",
        "features_sha256": calibrate_vla.sha256_bytes(calibrate_vla.canonical_json(features).encode("utf-8")),
        "metadata_sha256": "a" * 64,
        "total_episodes": 1,
    }


def test_given_e2e_fixture_and_rename_map_when_dataset_contract_built_then_camera_is_mapped() -> None:
    features = e2e_fixture_info()["features"]

    _, input_shapes = calibrate_vla._dataset_contract(
        {"features": features}, _dataset_manifest(features), E2E_RENAME_MAP
    )

    assert input_shapes == {"observation.images.camera1": [3, 96, 96]}


@pytest.mark.parametrize(
    ("features", "rename_map", "expected"),
    [
        pytest.param(
            {
                "observation.image": {"dtype": "video", "shape": [96, 96, 3]},
                "observation.state": {"dtype": "float32", "shape": [2]},
            },
            {"observation.image": "observation.images.base_0_rgb"},
            {"observation.images.base_0_rgb": [3, 96, 96]},
            id="single-camera-renamed",
        ),
        pytest.param(
            {
                "observation.images.scene": {"dtype": "video", "shape": [360, 640, 3]},
                "observation.images.wrist": {"dtype": "video", "shape": [360, 640, 3]},
                "action": {"dtype": "float32", "shape": [7]},
            },
            {
                "observation.images.scene": "observation.images.base_0_rgb",
                "observation.images.wrist": "observation.images.left_wrist_0_rgb",
            },
            {
                "observation.images.base_0_rgb": [3, 360, 640],
                "observation.images.left_wrist_0_rgb": [3, 360, 640],
            },
            id="two-cameras-renamed",
        ),
        pytest.param(
            {"observation.images.top": {"dtype": "image", "shape": [480, 640, 3]}},
            {},
            {"observation.images.top": [3, 480, 640]},
            id="canonical-camera-not-renamed",
        ),
    ],
)
def test_given_visual_features_when_dataset_contract_built_then_renamed_shapes_are_recorded(
    features: dict[str, Any], rename_map: dict[str, str], expected: dict[str, list[int]]
) -> None:
    _, input_shapes = calibrate_vla._dataset_contract({"features": features}, _dataset_manifest(features), rename_map)

    assert input_shapes == expected


@pytest.mark.parametrize(
    ("features", "rename_map", "message"),
    [
        pytest.param(
            {
                "observation.images.left": {"dtype": "video", "shape": [96, 96, 3]},
                "observation.images.right": {"dtype": "video", "shape": [96, 96, 3]},
            },
            {
                "observation.images.left": "observation.images.base_0_rgb",
                "observation.images.right": "observation.images.base_0_rgb",
            },
            "duplicate feature",
            id="cameras-renamed-to-same-key",
        ),
        pytest.param(
            {"observation.image": {"dtype": "video", "shape": [96, 96]}},
            {},
            "positive HWC shape",
            id="invalid-camera-shape",
        ),
        pytest.param(
            {"observation.state": {"dtype": "float32", "shape": [2]}},
            {},
            "does not contain image observation features",
            id="no-camera",
        ),
        pytest.param({"observation.image": "video"}, {}, "must be an object", id="malformed-feature"),
    ],
)
def test_given_invalid_visual_features_when_dataset_contract_built_then_they_are_rejected(
    features: dict[str, Any], rename_map: dict[str, str], message: str
) -> None:
    with pytest.raises(calibrate_vla.CalibrationError, match=message):
        calibrate_vla._dataset_contract({"features": features}, _dataset_manifest(features), rename_map)


_TERM_RESISTANT_PROCESS_TREE = """
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

role, directory = sys.argv[1], Path(sys.argv[2])
if role == "launcher":
    subprocess.Popen([sys.executable, __file__, "worker", str(directory)])
elif role == "worker":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    subprocess.Popen([sys.executable, __file__, "descendant", str(directory)])
else:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
(directory / f"{role}.pid").write_text(str(os.getpid()), encoding="utf-8")
time.sleep(60)
"""


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="process-group membership is read from /proc")
def test_given_workers_outlive_launcher_when_group_terminated_then_every_member_stops(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = tmp_path / "process_tree.py"
    script.write_text(_TERM_RESISTANT_PROCESS_TREE, encoding="utf-8")
    monkeypatch.setattr(calibrate_vla, "_PROCESS_GROUP_TERM_GRACE_SECONDS", 0.5)
    process = subprocess.Popen([sys.executable, str(script), "launcher", str(tmp_path)], start_new_session=True)
    try:
        deadline = time.monotonic() + 30
        while not all((tmp_path / f"{role}.pid").is_file() for role in ("launcher", "worker", "descendant")):
            assert time.monotonic() < deadline, "the probe process tree did not start"
            time.sleep(0.05)

        calibrate_vla._terminate_process_group(process)

        assert process.returncode == -signal.SIGTERM
        assert calibrate_vla._live_process_group_members(process.pid) == []
    finally:
        if calibrate_vla._live_process_group_members(process.pid):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def test_given_exited_process_group_when_terminated_then_no_signal_is_sent(mocker: pytest.MockFixture) -> None:
    process = mocker.Mock(pid=4321)
    process.poll.return_value = 0
    mocker.patch.object(calibrate_vla, "_live_process_group_members", return_value=[])
    killpg = mocker.patch.object(calibrate_vla.os, "killpg")

    calibrate_vla._terminate_process_group(process)

    killpg.assert_not_called()


def test_given_process_group_survives_sigkill_when_terminated_then_calibration_fails(
    mocker: pytest.MockFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = mocker.Mock(pid=4321)
    process.poll.return_value = 0
    mocker.patch.object(calibrate_vla, "_live_process_group_members", return_value=[4322])
    killpg = mocker.patch.object(calibrate_vla.os, "killpg")
    monkeypatch.setattr(calibrate_vla, "_PROCESS_GROUP_TERM_GRACE_SECONDS", 0)
    monkeypatch.setattr(calibrate_vla, "_PROCESS_GROUP_KILL_GRACE_SECONDS", 0)

    with pytest.raises(calibrate_vla.CalibrationError, match="still running after SIGKILL"):
        calibrate_vla._terminate_process_group(process)

    assert [call.args for call in killpg.call_args_list] == [(4321, signal.SIGTERM), (4321, signal.SIGKILL)]


def test_given_completed_probe_when_candidate_runs_then_its_process_group_is_cleaned_up(
    mocker: pytest.MockFixture,
    tmp_path: Path,
) -> None:
    process = mocker.MagicMock()

    def write_success_result(*, timeout: int) -> int:
        (tmp_path / "batch-1.json").write_text('{"micro_batch_size": 1, "outcome": "success"}', encoding="utf-8")
        return 0

    process.wait.side_effect = write_success_result
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

    assert result["outcome"] == "success"
    terminate.assert_called_once_with(process)


def _configure_workload_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, tuple[str, ...]]:
    """Exercise real workload generation with a copied code root and CPU-only CUDA metadata."""
    dataset = tmp_path / "dataset"
    (dataset / "meta").mkdir(parents=True)
    features = {
        "observation.image": {"dtype": "video", "shape": [96, 96, 3]},
        "observation.state": {"dtype": "float32", "shape": [2]},
    }
    (dataset / "meta/info.json").write_text(
        calibrate_vla.canonical_json({"features": features}),
        encoding="utf-8",
    )
    manifest = tmp_path / "dataset.json"
    calibrate_vla.write_record(manifest, _dataset_manifest(features))
    code_root = tmp_path / "code"
    shutil.copytree(_REPO_ROOT / "training/vla/scripts", code_root / "vla/scripts")
    shutil.copytree(_REPO_ROOT / "training/vla/lerobot", code_root / "vla/lerobot")
    environment = {
        "DATASET_REPO_ID": "org/robot-data",
        "DATASET_MANIFEST": str(manifest),
        "INIT_FROM_POLICY_HF_REPO_ID": "lerobot/pi05_base",
        "INIT_FROM_POLICY_HF_REVISION": "b" * 40,
        "CODE_REPOSITORY": "https://github.com/microsoft/physical-ai-toolchain.git",
        "CODE_REVISION": "c" * 40,
        "RUNTIME_IMAGE": f"runtime@sha256:{'d' * 64}",
        "COMPUTE_TARGET": "azureml:gpu",
        "VLA_MODEL_ADAPTER": "lerobot-pi",
        "POLICY_TYPE": "pi05",
        "MIXED_PRECISION": "bf16",
        "TRAIN_EXPERT_ONLY": "true",
        "GRADIENT_CHECKPOINTING": "false",
        "USE_IMAGENET_STATS": "false",
        "LEROBOT_PROJECT": str(code_root / "vla/lerobot"),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: 1, get_device_name=lambda: "test-gpu")
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda))
    monkeypatch.setattr(calibrate_vla, "_CODE_ROOT", code_root)
    rename_map = calibrate_vla.canonical_json({"observation.image": "observation.images.base_0_rgb"})
    return code_root, (f"--dataset.root={dataset}", f"--rename_map={rename_map}")


def test_given_unchanged_uploaded_code_when_calibration_reused_then_batch_is_resolved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, arguments = _configure_workload_inputs(monkeypatch, tmp_path)
    workload = calibrate_vla._build_workload(arguments)
    workload_path = tmp_path / "workload.json"
    workload_path.write_text(calibrate_vla.canonical_json(workload), encoding="utf-8")
    report_path = tmp_path / "calibration-report.json"
    calibrate_vla.write_record(report_path, calibrate_vla._build_report(workload, [_successful_probe(1)], 0.1))

    batch_size = calibrate_vla.resolve_recommended_batch_size(
        report_path, workload_path, calibrate_vla._build_workload(arguments)
    )

    assert batch_size == 1
    assert workload["code"]["content_sha256"] == calibrate_vla.code_content_fingerprint(tmp_path / "code")


def test_given_changed_uploaded_code_with_same_revision_when_calibration_reused_then_it_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    code_root, arguments = _configure_workload_inputs(monkeypatch, tmp_path)
    workload = calibrate_vla._build_workload(arguments)
    workload_path = tmp_path / "workload.json"
    workload_path.write_text(calibrate_vla.canonical_json(workload), encoding="utf-8")
    report_path = tmp_path / "calibration-report.json"
    calibrate_vla.write_record(report_path, calibrate_vla._build_report(workload, [_successful_probe(1)], 0.1))
    entrypoint = code_root / "vla/scripts/azureml-component-entry.sh"
    entrypoint.write_text(entrypoint.read_text(encoding="utf-8") + "\n# changed after calibration\n", encoding="utf-8")

    with pytest.raises(calibrate_vla.ContractError, match="effective training inputs"):
        calibrate_vla.resolve_recommended_batch_size(
            report_path, workload_path, calibrate_vla._build_workload(arguments)
        )


def _successful_probe(batch_size: int) -> dict[str, Any]:
    return {
        "micro_batch_size": batch_size,
        "outcome": "success",
        "device_name": "test-gpu",
        "peak_allocated_bytes": 50,
        "peak_reserved_bytes": 60,
        "free_bytes_after_step": 40,
        "total_bytes": 100,
        "duration_seconds": 0.5,
        "samples_per_second": 2.0,
        "optimizer_steps": 1,
    }
