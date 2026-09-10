"""Saved calibration checks must agree with the isolated SO-101 worker."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.operator.models import PreflightCheckOutcome
from src.api.operator.preflight import OperatorPreflightRunner
from src.api.operator.profiles import ArmProfile
from src.api.routers import operator
from src.api.services.operator_service import OperatorService

_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")


def _calibration() -> dict[str, dict[str, int]]:
    return {
        joint: {"id": index, "drive_mode": 0, "homing_offset": 0, "range_min": 100, "range_max": 4000}
        for index, joint in enumerate(_JOINTS, start=1)
    }


def _arm(path: Path) -> ArmProfile:
    return ArmProfile(
        port=path.parent / "so101_leader",
        logical_id=path.stem,
        usb_vendor_id="1a86",
        usb_product_id="55d3",
        usb_serial="test-leader",
        calibration_file=path,
    )


@pytest.fixture(scope="module")
def worker_calibration() -> ModuleType:
    path = Path(__file__).parents[2] / "operator-worker/src/operator_worker/calibration.py"
    spec = importlib.util.spec_from_file_location("worker_calibration_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCalibrationReadiness:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("id", 2),
            ("id", True),
            ("drive_mode", 2),
            ("drive_mode", False),
            ("homing_offset", 2048),
            ("homing_offset", -2048),
            ("homing_offset", "0"),
            ("range_min", -1),
            ("range_max", 4096),
            ("range_max", 100),
        ],
    )
    def test_invalid_joint_values_block_readiness(
        self, tmp_path: Path, worker_calibration: ModuleType, field: str, value: object
    ) -> None:
        payload = _calibration()
        payload["shoulder_pan"][field] = value
        path = tmp_path / "leader.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        evidence = {}

        check = OperatorPreflightRunner(data_root=tmp_path)._calibration_check("leader", _arm(path), evidence)

        assert check.outcome is PreflightCheckOutcome.BLOCKING
        assert "leader_calibration" not in evidence
        assert check.remediation
        with pytest.raises(worker_calibration.CalibrationError):
            worker_calibration.validate_calibration_file(path)

    @pytest.mark.parametrize("payload", [None, 0, "calibration", [], list(_JOINTS), dict.fromkeys(_JOINTS)])
    def test_invalid_json_shapes_fail_closed(
        self, tmp_path: Path, worker_calibration: ModuleType, payload: object
    ) -> None:
        path = tmp_path / "leader.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        check = OperatorPreflightRunner(data_root=tmp_path)._calibration_check("leader", _arm(path), {})

        assert check.outcome is PreflightCheckOutcome.BLOCKING
        with pytest.raises(worker_calibration.CalibrationError):
            worker_calibration.validate_calibration_file(path)

    @pytest.mark.parametrize("problem", ["missing_field", "extra_field", "swapped_ids", "duplicate_key", "encoding"])
    def test_malformed_calibration_is_rejected_consistently(
        self, tmp_path: Path, worker_calibration: ModuleType, problem: str
    ) -> None:
        payload = _calibration()
        if problem == "missing_field":
            del payload["gripper"]["homing_offset"]
        elif problem == "extra_field":
            payload["gripper"]["unknown"] = 1
        elif problem == "swapped_ids":
            payload["shoulder_pan"]["id"], payload["gripper"]["id"] = 6, 1
        content = json.dumps(payload).encode()
        if problem == "duplicate_key":
            content = content.replace(b'"id": 1', b'"id": 6, "id": 1', 1)
        elif problem == "encoding":
            content = b"\xff"
        path = tmp_path / "leader.json"
        path.write_bytes(content)

        check = OperatorPreflightRunner(data_root=tmp_path)._calibration_check("leader", _arm(path), {})

        assert check.outcome is PreflightCheckOutcome.BLOCKING
        with pytest.raises(worker_calibration.CalibrationError):
            worker_calibration.validate_calibration_file(path)

    def test_json_key_order_is_not_a_calibration_constraint(
        self, tmp_path: Path, worker_calibration: ModuleType
    ) -> None:
        payload = dict(reversed(list(_calibration().items())))
        path = tmp_path / "leader.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        evidence = {}

        check = OperatorPreflightRunner(data_root=tmp_path)._calibration_check("leader", _arm(path), evidence)

        assert check.outcome is PreflightCheckOutcome.PASSED
        assert evidence["leader_calibration"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert tuple(worker_calibration.validate_calibration_file(path)) == _JOINTS
        assert "not" in check.detail.lower() and "hardware" in check.detail.lower()

    @pytest.mark.parametrize("offset", [-2047, 2047])
    def test_supported_boundaries_pass(self, tmp_path: Path, worker_calibration: ModuleType, offset: int) -> None:
        payload = _calibration()
        payload["wrist_roll"].update(range_min=0, range_max=4095, homing_offset=offset)
        path = tmp_path / "leader.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        check = OperatorPreflightRunner(data_root=tmp_path)._calibration_check("leader", _arm(path), {})

        assert check.outcome is PreflightCheckOutcome.PASSED
        assert worker_calibration.validate_calibration_file(path) == payload

    @pytest.mark.parametrize("problem", ["missing", "directory", "oversized", "nested", "large_integer"])
    def test_unusable_calibration_sources_fail_closed(
        self, tmp_path: Path, worker_calibration: ModuleType, problem: str
    ) -> None:
        path = tmp_path / "leader.json"
        if problem == "directory":
            path.mkdir()
        elif problem == "oversized":
            path.write_bytes(json.dumps(_calibration()).encode() + b" " * 65_536)
        elif problem == "nested":
            path.write_bytes(b"[" * 30_000 + b"]" * 30_000)
        elif problem == "large_integer":
            path.write_bytes(b"9" * 5000)

        check = OperatorPreflightRunner(data_root=tmp_path)._calibration_check("leader", _arm(path), {})

        assert check.outcome is PreflightCheckOutcome.BLOCKING
        with pytest.raises(worker_calibration.CalibrationError):
            worker_calibration.validate_calibration_file(path)


class TestCalibrationApi:
    def test_operator_authorization_is_required(self) -> None:
        app = FastAPI()
        app.include_router(operator.router, prefix="/api/operator")

        def deny_operator_access() -> None:
            raise HTTPException(status_code=403, detail="Operator access denied")

        app.dependency_overrides[operator.require_operator_access] = deny_operator_access
        with TestClient(app) as client:
            response = client.get("/api/operator/calibration")

        assert response.status_code == 403
        assert response.json()["detail"] == "Operator access denied"

    def test_read_only_report_works_with_motion_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        root = tmp_path / ".cache/huggingface/lerobot/calibration"
        for relative in ("teleoperators/so_leader/my_leader_arm.json", "robots/so_follower/my_follower_arm.json"):
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(_calibration()), encoding="utf-8")
        app = FastAPI()
        service = OperatorService(adapter_mode="disabled")
        app.state.operator_service = service
        app.include_router(operator.router, prefix="/api/operator")
        app.dependency_overrides[operator.require_operator_access] = lambda: None
        before = service.status()

        with TestClient(app) as client:
            response = client.get("/api/operator/calibration")

        assert response.status_code == 200
        report = response.json()
        assert report["valid"] is True
        assert report["hardware_verified"] is False
        assert report["checked_at"]
        assert [item["role"] for item in report["arms"]] == ["leader", "follower"]
        assert all(len(item["joints"]) == 6 and item["sha256"] for item in report["arms"])
        assert str(tmp_path) not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert service.status() == before
        assert service.capabilities().session_start_enabled is False

    def test_missing_files_are_reported_without_starting_a_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        app = FastAPI()
        app.state.operator_service = OperatorService(adapter_mode="disabled")
        app.include_router(operator.router, prefix="/api/operator")
        app.dependency_overrides[operator.require_operator_access] = lambda: None

        with TestClient(app) as client:
            response = client.get("/api/operator/calibration")

        assert response.status_code == 200
        assert response.json()["valid"] is False
        assert all(not item["valid"] and item["issues"] for item in response.json()["arms"])
        assert str(tmp_path) not in response.text
