from __future__ import annotations

import json
from pathlib import Path

import pytest

from operator_worker.acquisition import create_runtime
from operator_worker.calibration import JOINTS, CalibrationError, validate_calibration_file
from operator_worker.protocol import SessionSettings
from operator_worker.resources import CleanupReport


def _calibration() -> dict[str, dict[str, int]]:
    return {
        name: {
            "id": index,
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": 100,
            "range_max": 4_000,
        }
        for index, name in enumerate(JOINTS, start=1)
    }


def _physical_profile(tmp_path: Path) -> dict[str, object]:
    leader_calibration = tmp_path / "leader.json"
    follower_calibration = tmp_path / "follower.json"
    leader_calibration.write_text(json.dumps(_calibration()), encoding="utf-8")
    follower_calibration.write_text(json.dumps(_calibration()), encoding="utf-8")
    return {
        "version": 1,
        "name": "so101-local",
        "embodiment": "SO-101",
        "actuator_names": list(JOINTS),
        "minimum_free_bytes": 0,
        "teleoperation_fps": 30,
        "max_relative_target": 5.0,
        "leader": {
            "port": "/dev/serial/by-id/leader",
            "logical_id": "leader",
            "usb_vendor_id": "1234",
            "usb_product_id": "5678",
            "usb_serial": "leader-serial",
            "calibration_file": str(leader_calibration),
        },
        "follower": {
            "port": "/dev/serial/by-id/follower",
            "logical_id": "follower",
            "usb_vendor_id": "1234",
            "usb_product_id": "5678",
            "usb_serial": "follower-serial",
            "calibration_file": str(follower_calibration),
        },
        "wrist_camera": {
            "path": "/dev/v4l/by-id/wrist",
            "usb_vendor_id": "1234",
            "usb_product_id": "5678",
            "width": 640,
            "height": 480,
            "fps": 30,
        },
        "front_camera": {
            "usb_vendor_id": "1234",
            "usb_product_id": "5678",
            "usb_serial": "front-sdk",
            "usb_descriptor_serial": "front-usb",
            "product": "D405",
            "width": 640,
            "height": 480,
            "fps": 30,
        },
        "recording": {
            "fps": 30,
            "episode_time_s": 60,
            "reset_time_s": 30,
            "upload_default": False,
        },
    }


def test_given_no_execution_mode_when_runtime_created_then_simulation_is_used() -> None:
    runtime = create_runtime({}, SessionSettings(mode="teleoperate", control_fps=30))

    runtime.acquire()
    report = runtime.cleanup()

    assert report.cleanup_complete is True
    assert report.torque_verified_off is True


def test_given_physical_mode_when_runtime_created_then_independent_checks_precede_builder(tmp_path: Path) -> None:
    events: list[str] = []
    profile = _physical_profile(tmp_path)
    settings = SessionSettings(mode="teleoperate", control_fps=30, execution_mode="physical")
    runtime = object()

    result = create_runtime(
        profile,
        settings,
        calibration_validator=lambda path: events.append(f"calibration:{path.name}") or {},
        camera_checker=lambda _profile: events.append("cameras") or CleanupReport(True, ("front", "wrist"), ()),
        physical_builder=lambda _profile, _settings, _stop_event: events.append("build") or runtime,
    )

    assert result is runtime
    assert events == ["calibration:leader.json", "calibration:follower.json", "cameras", "build"]


def test_given_failed_camera_check_when_physical_runtime_created_then_motion_runtime_is_not_built(
    tmp_path: Path,
) -> None:
    built = False

    def build_runtime(*_args: object) -> object:
        nonlocal built
        built = True
        return object()

    with pytest.raises(RuntimeError, match="camera"):
        create_runtime(
            _physical_profile(tmp_path),
            SessionSettings(mode="teleoperate", control_fps=30, execution_mode="physical"),
            camera_checker=lambda _profile: CleanupReport(False, (), ("front unavailable",)),
            physical_builder=build_runtime,
        )

    assert built is False


def test_given_nonportable_saved_calibration_when_validated_then_it_fails_closed(tmp_path: Path) -> None:
    calibration = _calibration()
    calibration["gripper"]["range_max"] = 5_000
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(calibration), encoding="utf-8")

    with pytest.raises(CalibrationError, match="ranges"):
        validate_calibration_file(path)


def test_given_simulation_policy_when_run_then_telemetry_preview_and_stop_are_supported() -> None:
    settings = SessionSettings(
        mode="policy",
        control_fps=30,
        max_relative_target=1.0,
        rollout_time_s=5,
    )
    runtime = create_runtime({}, settings)
    telemetry: list[object] = []
    previews: list[tuple[str, bytes, float]] = []
    runtime.set_telemetry_callback(lambda sample: (telemetry.append(sample), runtime.request_stop()))
    runtime.set_preview_callback(lambda camera, payload, captured_at: previews.append((camera, payload, captured_at)))
    runtime.acquire()
    runtime.enable_motion()

    runtime.policy()
    report = runtime.cleanup()

    assert len(telemetry) == 1
    assert {preview[0] for preview in previews} == {"front", "wrist"}
    assert all(len(preview[1]) <= 300_000 for preview in previews)
    assert report.cleanup_complete is True


def test_given_simulation_recording_when_finished_then_lerobot_source_is_discoverable(tmp_path: Path) -> None:
    dataset_root = tmp_path / "captured-dataset"
    settings = SessionSettings(
        mode="record",
        control_fps=30,
        dataset_root=str(dataset_root),
        dataset_id="captured-dataset",
        repo_id="local/captured-dataset",
        episode_time_s=5,
    )
    runtime = create_runtime({}, settings)
    runtime.set_telemetry_callback(lambda _sample: runtime.request_stop())
    runtime.acquire()
    runtime.enable_motion()

    runtime.record()
    result = runtime.command("finish")
    report = runtime.cleanup()

    assert result.phase == "finalized"
    assert (dataset_root / "meta" / "info.json").is_file()
    assert (dataset_root / "data").is_dir()
    assert report.cleanup_complete is True
