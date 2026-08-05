from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import numpy as np

from operator_worker.acquisition import CleanupReport
from operator_worker.config import WorkerProfile
from operator_worker.so101 import SO101Runtime, _discard_dataset, build_so101_runtime


def _profile(tmp_path: Path) -> WorkerProfile:
    calibration = {
        name: {
            "id": index,
            "drive_mode": 0,
            "homing_offset": 0,
            "range_min": 100,
            "range_max": 4000,
        }
        for index, name in enumerate(
            (
                "shoulder_pan",
                "shoulder_lift",
                "elbow_flex",
                "wrist_flex",
                "wrist_roll",
                "gripper",
            ),
            start=1,
        )
    }
    leader_calibration = tmp_path / "leader.json"
    follower_calibration = tmp_path / "follower.json"
    leader_calibration.write_text(json.dumps(calibration), encoding="utf-8")
    follower_calibration.write_text(json.dumps(calibration), encoding="utf-8")
    return WorkerProfile.model_validate(
        {
            "version": 1,
            "name": "so101",
                "embodiment": "SO-101",
                "actuator_names": [
                    "shoulder_pan",
                    "shoulder_lift",
                    "elbow_flex",
                    "wrist_flex",
                    "wrist_roll",
                    "gripper",
                ],
            "minimum_free_bytes": 0,
            "teleoperation_fps": 30,
            "max_relative_target": 2.0,
            "leader": {
                "port": tmp_path / "leader",
                "logical_id": "my_leader_arm",
                "usb_vendor_id": "1a86",
                "usb_product_id": "55d3",
                "usb_serial": "leader",
                "calibration_file": leader_calibration,
            },
            "follower": {
                "port": tmp_path / "follower",
                "logical_id": "my_follower_arm",
                "usb_vendor_id": "1a86",
                "usb_product_id": "55d3",
                "usb_serial": "follower",
                "calibration_file": follower_calibration,
            },
            "wrist_camera": {
                "path": tmp_path / "video",
                "usb_vendor_id": "05a3",
                "usb_product_id": "9230",
                "width": 640,
                "height": 480,
                "fps": 30,
            },
            "front_camera": {
                "usb_vendor_id": "8086",
                "usb_product_id": "0b5b",
                "usb_serial": "218622278289",
                "usb_descriptor_serial": "323743071153",
                "product": "Intel RealSense D405",
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
            "fingerprint": "profile",
        }
    )


def test_factory_constructs_without_connecting_hardware(tmp_path: Path) -> None:
    runtime = build_so101_runtime(_profile(tmp_path))

    assert runtime.leader.bus.is_connected is False
    assert runtime.follower.bus.is_connected is False
    assert runtime.follower.cameras == {}
    assert runtime.profile.max_relative_target == 2.0

    cleanup = runtime.cleanup()

    assert cleanup.cleanup_complete is True
    assert cleanup.torque_verified_off is True


def test_profile_json_round_trip_preserves_paths(tmp_path: Path) -> None:
    profile = _profile(tmp_path)

    restored = WorkerProfile.model_validate_json(profile.model_dump_json())

    assert restored.leader.port == profile.leader.port
    assert restored.wrist_camera.path == profile.wrist_camera.path


def test_preview_encoder_emits_bounded_jpeg() -> None:
    previews = []
    runtime = SO101Runtime.__new__(SO101Runtime)
    runtime.preview_callback = lambda *args: previews.append(args)
    runtime.last_preview_at = {}
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    runtime._publish_previews({"wrist": frame, "front": frame})

    assert [preview[0] for preview in previews] == ["wrist", "front"]
    assert all(preview[1].startswith(b"\xff\xd8") for preview in previews)
    assert all(len(preview[1]) < 300_000 for preview in previews)


def test_discard_dataset_removes_only_the_named_session_root(tmp_path: Path) -> None:
    dataset_root = tmp_path / "demo-session"
    dataset_root.mkdir()
    (dataset_root / "episode.parquet").write_bytes(b"saved episode")

    assert _discard_dataset(dataset_root, "demo-session") is True
    assert not dataset_root.exists()


def test_discard_dataset_rejects_a_mismatched_session_root(tmp_path: Path) -> None:
    dataset_root = tmp_path / "another-session"
    dataset_root.mkdir()

    try:
        _discard_dataset(dataset_root, "demo-session")
    except RuntimeError as error:
        assert "does not match" in str(error)
    else:
        raise AssertionError("mismatched dataset root should be rejected")

    assert dataset_root.exists()


def test_discard_cleanup_releases_resources_before_deleting_dataset(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "demo-session"
    dataset_root.mkdir()
    events: list[str] = []

    class RecordingSession:
        def finalize_for_cleanup(self) -> None:
            events.append("finalize")

    class Transaction:
        def release_all(self) -> CleanupReport:
            assert dataset_root.exists()
            events.append("release")
            return CleanupReport(True, ("follower", "leader"), ())

    runtime = SO101Runtime.__new__(SO101Runtime)
    runtime.stop_event = Event()
    runtime.recording_session = RecordingSession()
    runtime.transaction = Transaction()
    runtime.policy_client = None
    runtime.discard_requested = True
    runtime.settings = SimpleNamespace(
        dataset_root=dataset_root,
        dataset_id="demo-session",
    )
    runtime.leader_resource = SimpleNamespace(torque_verified_off=True)
    runtime.follower_resource = SimpleNamespace(torque_verified_off=True)

    report = runtime.cleanup()

    assert events == ["finalize", "release"]
    assert not dataset_root.exists()
    assert report.cleanup_complete is True
    assert report.released == ("follower", "leader", "dataset")
