"""Behavior tests for SO-101 profile loading and side-effect-free preflight."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.api.operator.models import (
    OperatorMode,
    PreflightCheckOutcome,
    PreflightLifecycle,
    PreflightRequest,
)
from src.api.operator.preflight import OperatorPreflightRunner
from src.api.operator.profiles import OperatorProfileError, load_operator_profile
from src.api.services.operator_preflight_service import (
    OperatorPreflightConflictError,
    OperatorPreflightNotFoundError,
    OperatorPreflightService,
)

_JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)


def _write_calibration(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {name: {"id": index} for index, name in enumerate(_JOINTS, start=1)}
        ),
        encoding="utf-8",
    )


def _write_profile(path: Path, root: Path) -> None:
    path.write_text(
        f"""
version = 1
name = "so101"
embodiment = "SO-101"
actuator_names = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]
minimum_free_bytes = 100
teleoperation_fps = 30
max_relative_target = 2.0

[leader]
port = "{root}/dev/so101_leader"
logical_id = "my_leader_arm"
usb_vendor_id = "1a86"
usb_product_id = "55d3"
usb_serial = "leader-serial"
calibration_file = "{root}/calibration/leader.json"

[follower]
port = "{root}/dev/so101_follower"
logical_id = "my_follower_arm"
usb_vendor_id = "1a86"
usb_product_id = "55d3"
usb_serial = "follower-serial"
calibration_file = "{root}/calibration/follower.json"

[wrist_camera]
path = "{root}/dev/wrist-video-index0"
usb_vendor_id = "05a3"
usb_product_id = "9230"
width = 640
height = 480
fps = 30

[front_camera]
usb_vendor_id = "8086"
usb_product_id = "0b5b"
usb_serial = "front-serial"
usb_descriptor_serial = "front-usb-serial"
product = "Intel RealSense D405"
width = 640
height = 480
fps = 30

[recording]
fps = 30
episode_time_s = 60
reset_time_s = 30
upload_default = false
""".strip() + "\n",
        encoding="utf-8",
    )


def _create_sysfs_usb_device(
    root: Path,
    name: str,
    *,
    vendor: str,
    product: str,
    serial: str,
    product_name: str = "",
) -> Path:
    device = root / "sys/bus/usb/devices" / name
    device.mkdir(parents=True)
    (device / "idVendor").write_text(vendor, encoding="utf-8")
    (device / "idProduct").write_text(product, encoding="utf-8")
    (device / "serial").write_text(serial, encoding="utf-8")
    (device / "product").write_text(product_name, encoding="utf-8")
    return device


def _prepare_ready_host(root: Path) -> tuple[Path, Path]:
    profile_path = root / "so101.toml"
    (root / "dev").mkdir()
    for device_name in ("ttyACM0", "ttyACM1", "video6"):
        (root / "dev" / device_name).touch()
    (root / "dev/so101_leader").symlink_to("ttyACM0")
    (root / "dev/so101_follower").symlink_to("ttyACM1")
    (root / "dev/wrist-video-index0").symlink_to("video6")
    _write_calibration(root / "calibration/leader.json")
    _write_calibration(root / "calibration/follower.json")
    _write_profile(profile_path, root)

    tty_root = root / "sys/class/tty"
    video_root = root / "sys/class/video4linux"
    for name, serial in (("ttyACM0", "leader-serial"), ("ttyACM1", "follower-serial")):
        usb = _create_sysfs_usb_device(
            root,
            f"usb-{name}",
            vendor="1a86",
            product="55d3",
            serial=serial,
        )
        (tty_root / name).parent.mkdir(parents=True, exist_ok=True)
        (tty_root / name).symlink_to(usb)
    wrist_usb = _create_sysfs_usb_device(
        root,
        "usb-video6",
        vendor="05a3",
        product="9230",
        serial="wrist",
    )
    (video_root / "video6").parent.mkdir(parents=True, exist_ok=True)
    (video_root / "video6").symlink_to(wrist_usb)
    _create_sysfs_usb_device(
        root,
        "usb-front",
        vendor="8086",
        product="0b5b",
        serial="front-usb-serial",
        product_name="Intel RealSense D405",
    )
    data_root = root / "datasets"
    data_root.mkdir()
    return profile_path, data_root


class TestOperatorProfiles:
    def test_checked_in_profile_disables_relative_target_clamp(self) -> None:
        profile_path = (
            Path(__file__).parents[2] / "src/api/operator/profile_data/so101.toml"
        )

        profile = load_operator_profile(profile_path, environ={})

        assert profile.max_relative_target is None

    def test_checked_in_profile_resolves_portable_calibration_paths(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        profile_path = (
            Path(__file__).parents[2] / "src/api/operator/profile_data/so101.toml"
        )
        monkeypatch.setenv("HOME", str(tmp_path))

        profile = load_operator_profile(profile_path, environ={})

        calibration_root = tmp_path / ".cache/huggingface/lerobot/calibration"
        assert profile.leader.calibration_file == (
            calibration_root / "teleoperators/so_leader/my_leader_arm.json"
        )
        assert profile.follower.calibration_file == (
            calibration_root / "robots/so_follower/my_follower_arm.json"
        )

    def test_loads_strict_profile_and_allowlisted_override(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        profile_path, _ = _prepare_ready_host(tmp_path)
        baseline = load_operator_profile(profile_path, environ={})
        monkeypatch.setenv("OPERATOR_SO101_FRONT_CAMERA_SERIAL", "replacement")

        overridden = load_operator_profile(profile_path, environ=os.environ)

        assert baseline.front_camera.usb_serial == "front-serial"
        assert baseline.front_camera.usb_descriptor_serial == "front-usb-serial"
        assert overridden.front_camera.usb_serial == "replacement"
        assert overridden.fingerprint != baseline.fingerprint

    def test_rejects_unknown_operator_override(self, tmp_path: Path) -> None:
        profile_path, _ = _prepare_ready_host(tmp_path)

        with pytest.raises(OperatorProfileError, match="Unknown OPERATOR_SO101"):
            load_operator_profile(
                profile_path,
                environ={"OPERATOR_SO101_UNSAFE_PORT": "/dev/ttyACM0"},
            )

    def test_rejects_duplicate_arm_identity(self, tmp_path: Path) -> None:
        profile_path, _ = _prepare_ready_host(tmp_path)
        content = profile_path.read_text(encoding="utf-8").replace(
            'usb_serial = "follower-serial"',
            'usb_serial = "leader-serial"',
        )
        profile_path.write_text(content, encoding="utf-8")

        with pytest.raises(OperatorProfileError, match="unique"):
            load_operator_profile(profile_path, environ={})

    def test_rejects_unknown_version_and_coerced_types(self, tmp_path: Path) -> None:
        profile_path, _ = _prepare_ready_host(tmp_path)
        profile_path.write_text(
            profile_path.read_text(encoding="utf-8").replace(
                "version = 1", "version = 2"
            ),
            encoding="utf-8",
        )
        with pytest.raises(OperatorProfileError):
            load_operator_profile(profile_path, environ={})

        _write_profile(profile_path, tmp_path)
        profile_path.write_text(
            profile_path.read_text(encoding="utf-8").replace(
                "fps = 30", 'fps = "30"', 1
            ),
            encoding="utf-8",
        )
        with pytest.raises(OperatorProfileError):
            load_operator_profile(profile_path, environ={})


class TestOperatorPreflightRunner:
    def test_ready_profile_passes_without_opening_devices(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        profile = load_operator_profile(profile_path, environ={})
        monkeypatch.setattr(
            "builtins.open",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("open")),
        )
        runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: True,
            access_check=lambda _path, _mode: True,
        )

        result = runner.run(profile, mode=OperatorMode.RECORD, upload_requested=False)

        assert result.start_eligible is True
        assert result.resource_fingerprint
        assert all(
            check.outcome is not PreflightCheckOutcome.BLOCKING
            for check in result.checks
        )

    def test_realsense_interfaces_with_one_serial_count_as_one_camera(
        self, tmp_path: Path
    ) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        _create_sysfs_usb_device(
            tmp_path,
            "usb-front-interface",
            vendor="8086",
            product="0b5b",
            serial="front-usb-serial",
            product_name="Intel RealSense D405",
        )
        runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: True,
            access_check=lambda _path, _mode: True,
        )

        result = runner.run(
            load_operator_profile(profile_path, environ={}), mode=OperatorMode.RECORD
        )

        front = next(check for check in result.checks if check.name == "front_camera")
        assert front.outcome is PreflightCheckOutcome.PASSED

    @pytest.mark.parametrize(
        ("mutation", "expected_check"),
        [
            (lambda root: (root / "dev/so101_leader").unlink(), "leader_device"),
            (
                lambda root: (root / "calibration/follower.json").write_text(
                    "{}", encoding="utf-8"
                ),
                "follower_calibration",
            ),
            (
                lambda root: (root / "sys/bus/usb/devices/usb-front/serial").write_text(
                    "another-camera", encoding="utf-8"
                ),
                "front_camera",
            ),
        ],
    )
    def test_missing_or_mismatched_resources_block(
        self,
        tmp_path: Path,
        mutation,
        expected_check: str,
    ) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        mutation(tmp_path)
        runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: True,
            access_check=lambda _path, _mode: True,
        )

        result = runner.run(
            load_operator_profile(profile_path, environ={}),
            mode=OperatorMode.RECORD,
            upload_requested=False,
        )

        assert result.start_eligible is False
        assert (
            next(
                check for check in result.checks if check.name == expected_check
            ).outcome
            is PreflightCheckOutcome.BLOCKING
        )

    def test_one_byte_below_disk_threshold_blocks(self, tmp_path: Path) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 99,
            device_mode_check=lambda _path: True,
            access_check=lambda _path, _mode: True,
        )

        result = runner.run(
            load_operator_profile(profile_path, environ={}), mode=OperatorMode.RECORD
        )

        assert (
            next(
                check for check in result.checks if check.name == "dataset_storage"
            ).outcome
            is PreflightCheckOutcome.BLOCKING
        )

    def test_upload_requested_without_token_blocks(self, tmp_path: Path) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: True,
            access_check=lambda _path, _mode: True,
            environ={},
            credential_path=tmp_path / "missing-token",
        )

        result = runner.run(
            load_operator_profile(profile_path, environ={}),
            mode=OperatorMode.RECORD,
            upload_requested=True,
        )

        assert (
            next(
                check for check in result.checks if check.name == "upload_credentials"
            ).outcome
            is PreflightCheckOutcome.BLOCKING
        )

    def test_visible_device_holder_blocks(self, tmp_path: Path) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        fd_path = tmp_path / "proc/999/fd"
        fd_path.mkdir(parents=True)
        (fd_path / "3").symlink_to((tmp_path / "dev/ttyACM0").resolve())
        runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: True,
            access_check=lambda _path, _mode: True,
        )

        result = runner.run(
            load_operator_profile(profile_path, environ={}), mode=OperatorMode.RECORD
        )

        ownership = next(
            check for check in result.checks if check.name == "device_ownership"
        )
        assert ownership.outcome is PreflightCheckOutcome.BLOCKING
        assert ownership.remediation
        assert result.start_eligible is False

    def test_policy_runtime_requires_executable_and_complete_checkpoint(
        self,
        tmp_path: Path,
    ) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        profile = load_operator_profile(profile_path, environ={})
        missing_runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: True,
            access_check=lambda _path, _mode: True,
        )

        missing = missing_runner.run(profile, mode=OperatorMode.POLICY)
        assert next(
            check for check in missing.checks if check.name == "policy_runtime"
        ).outcome is (PreflightCheckOutcome.BLOCKING)

        python = tmp_path / "policy-python"
        python.write_text("#!/bin/sh\n", encoding="utf-8")
        python.chmod(0o700)
        checkpoint = tmp_path / "checkpoint"
        checkpoint.mkdir()
        for name in (
            "config.json",
            "model.safetensors",
            "policy_preprocessor.json",
            "policy_postprocessor.json",
        ):
            (checkpoint / name).touch()
        ready_runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: True,
            access_check=lambda path, _mode: path == python or path.exists(),
            policy_python=python,
            policy_checkpoint=checkpoint,
        )

        ready = ready_runner.run(profile, mode=OperatorMode.POLICY)
        assert next(
            check for check in ready.checks if check.name == "policy_runtime"
        ).outcome is (PreflightCheckOutcome.PASSED)

    def test_device_and_calibration_validation_fail_closed(
        self, tmp_path: Path
    ) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        profile = load_operator_profile(profile_path, environ={})
        inaccessible = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: False,
            access_check=lambda _path, _mode: True,
        ).run(profile, mode=OperatorMode.RECORD)
        assert next(
            check for check in inaccessible.checks if check.name == "leader_device"
        ).outcome is (PreflightCheckOutcome.BLOCKING)
        assert next(
            check for check in inaccessible.checks if check.name == "wrist_camera"
        ).outcome is (PreflightCheckOutcome.BLOCKING)

        profile.leader.calibration_file.write_text("not-json", encoding="utf-8")
        malformed = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: True,
            access_check=lambda _path, _mode: True,
        ).run(profile, mode=OperatorMode.RECORD)
        assert next(
            check for check in malformed.checks if check.name == "leader_calibration"
        ).outcome is (PreflightCheckOutcome.BLOCKING)

    def test_storage_credentials_and_visibility_report_exact_readiness(
        self,
        tmp_path: Path,
    ) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        profile = load_operator_profile(profile_path, environ={})
        token = tmp_path / "token"
        token.write_text("token", encoding="utf-8")
        runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "missing-proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: True,
            access_check=lambda _path, _mode: True,
            environ={},
            credential_path=token,
        )

        result = runner.run(
            profile,
            mode=OperatorMode.RECORD,
            upload_requested=True,
        )

        assert next(
            check for check in result.checks if check.name == "upload_credentials"
        ).outcome is (PreflightCheckOutcome.PASSED)
        ownership = next(
            check for check in result.checks if check.name == "device_ownership"
        )
        assert ownership.outcome is PreflightCheckOutcome.WARNING
        assert result.ownership_complete is False

        missing_storage = OperatorPreflightRunner(
            data_root=tmp_path / "missing-datasets",
            access_check=lambda _path, _mode: True,
        )._storage_check(profile, {})
        assert missing_storage.outcome is PreflightCheckOutcome.BLOCKING

    def test_front_camera_nodes_require_matching_video_identity(
        self, tmp_path: Path
    ) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        profile = load_operator_profile(profile_path, environ={})
        front_usb = tmp_path / "sys/bus/usb/devices/usb-front"
        front_video = tmp_path / "sys/class/video4linux/video8"
        front_video.symlink_to(front_usb)
        runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_video_root=tmp_path / "sys/class/video4linux",
        )

        assert runner._front_camera_nodes(profile) == [Path("/dev/video8")]

        runner.sys_video_root = tmp_path / "missing-video"
        assert runner._front_camera_nodes(profile) == []


class TestOperatorPreflightService:
    def test_idempotency_cancellation_and_expiry(self, tmp_path: Path) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        current_time = datetime(2026, 7, 22, tzinfo=UTC)
        runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: True,
            access_check=lambda _path, _mode: True,
        )
        service = OperatorPreflightService(
            profiles={"so101": load_operator_profile(profile_path, environ={})},
            runner=runner,
            now=lambda: current_time,
            ttl=timedelta(seconds=30),
        )
        request = PreflightRequest(
            command_id="preflight-1",
            profile="so101",
            mode=OperatorMode.RECORD,
        )

        first = service.create(request)
        replay = service.create(request)
        assert replay.preflight_id == first.preflight_id
        assert first.lifecycle is PreflightLifecycle.COMPLETED

        cancelled = service.cancel(first.preflight_id, command_id="cancel-preflight")
        assert cancelled.lifecycle is PreflightLifecycle.CANCELLED
        assert cancelled.start_eligible is False

        with pytest.raises(OperatorPreflightConflictError, match="payload"):
            service.create(
                request.model_copy(update={"mode": OperatorMode.TELEOPERATE})
            )

        fresh = service.create(request.model_copy(update={"command_id": "preflight-2"}))
        current_time += timedelta(seconds=30)
        expired = service.get(fresh.preflight_id)
        assert expired.lifecycle is PreflightLifecycle.EXPIRED
        assert expired.start_eligible is False

    def test_preflight_can_be_consumed_only_once(self, tmp_path: Path) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        service = OperatorPreflightService(
            profiles={"so101": load_operator_profile(profile_path, environ={})},
            runner=OperatorPreflightRunner(
                data_root=data_root,
                sys_tty_root=tmp_path / "sys/class/tty",
                sys_video_root=tmp_path / "sys/class/video4linux",
                sys_usb_root=tmp_path / "sys/bus/usb/devices",
                proc_root=tmp_path / "proc",
                free_bytes=lambda _path: 100,
                device_mode_check=lambda _path: True,
                access_check=lambda _path, _mode: True,
            ),
        )
        result = service.create(
            PreflightRequest(
                command_id="consume-create",
                profile="so101",
                mode=OperatorMode.TELEOPERATE,
            )
        )

        consumed = service.consume(result.preflight_id)
        assert consumed.lifecycle is PreflightLifecycle.CONSUMED
        with pytest.raises(OperatorPreflightConflictError):
            service.consume(result.preflight_id)

    def test_all_preflight_indexes_are_bounded(self, tmp_path: Path) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        runner = OperatorPreflightRunner(
            data_root=data_root,
            sys_tty_root=tmp_path / "sys/class/tty",
            sys_video_root=tmp_path / "sys/class/video4linux",
            sys_usb_root=tmp_path / "sys/bus/usb/devices",
            proc_root=tmp_path / "proc",
            free_bytes=lambda _path: 100,
            device_mode_check=lambda _path: True,
            access_check=lambda _path, _mode: True,
        )
        service = OperatorPreflightService(
            profiles={"so101": load_operator_profile(profile_path, environ={})},
            runner=runner,
        )

        for index in range(140):
            result = service.create(
                PreflightRequest(
                    command_id=f"create-{index}",
                    profile="so101",
                    mode=OperatorMode.RECORD,
                )
            )
            service.cancel(result.preflight_id, command_id=f"cancel-{index}")

        assert len(service._results) <= 128
        assert len(service._commands) <= 128
        assert len(service._cancel_commands) <= 128

    def test_not_found_and_cancel_replay_contracts(self, tmp_path: Path) -> None:
        profile_path, data_root = _prepare_ready_host(tmp_path)
        service = OperatorPreflightService(
            profiles={"so101": load_operator_profile(profile_path, environ={})},
            runner=OperatorPreflightRunner(
                data_root=data_root,
                sys_tty_root=tmp_path / "sys/class/tty",
                sys_video_root=tmp_path / "sys/class/video4linux",
                sys_usb_root=tmp_path / "sys/bus/usb/devices",
                proc_root=tmp_path / "proc",
                free_bytes=lambda _path: 100,
                device_mode_check=lambda _path: True,
                access_check=lambda _path, _mode: True,
            ),
        )

        with pytest.raises(OperatorPreflightNotFoundError):
            service.create(
                PreflightRequest(
                    command_id="unknown-profile",
                    profile="missing",
                    mode=OperatorMode.RECORD,
                )
            )
        with pytest.raises(OperatorPreflightNotFoundError):
            service.get("missing")

        result = service.create(
            PreflightRequest(
                command_id="create",
                profile="so101",
                mode=OperatorMode.RECORD,
            )
        )
        cancelled = service.cancel(result.preflight_id, command_id="cancel")
        assert service.cancel(result.preflight_id, command_id="cancel") == cancelled
        assert (
            service.cancel(result.preflight_id, command_id="cancel-again") == cancelled
        )

        other = service.create(
            PreflightRequest(
                command_id="other",
                profile="so101",
                mode=OperatorMode.RECORD,
            )
        )
        with pytest.raises(OperatorPreflightConflictError, match="command_id"):
            service.cancel(other.preflight_id, command_id="cancel")
