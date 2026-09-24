"""Simulation-default runtime selection and physical readiness gates."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any, ClassVar, Protocol

from .calibration import JOINTS, validate_calibration_file
from .camera_check import check_profile_cameras
from .config import WorkerProfile
from .policy_control import PolicyControlLoop
from .protocol import SessionSettings
from .recording import RecordingCommandResult, RecordingSession
from .resources import CleanupReport, ManagedResource, ResourceTransaction
from .teleoperate import JointTelemetry, LoopMetrics, TeleoperationLoop


class Runtime(Protocol):
    def acquire(self) -> None: ...

    def enable_motion(self) -> None: ...

    def teleoperate(self) -> None: ...

    def record(self) -> None: ...

    def policy(self) -> None: ...

    def command(self, action: str) -> Any: ...

    def request_stop(self) -> None: ...

    def cleanup(self) -> CleanupReport: ...


@dataclass
class _SimulationResource(ManagedResource):
    name: str
    acquired: bool = False

    def acquire(self) -> None:
        self.acquired = True

    def release(self) -> None:
        self.acquired = False


class _SimulationArm:
    action_features: ClassVar[dict[str, type[float]]] = {f"{joint}.pos": float for joint in JOINTS}

    def __init__(self) -> None:
        self.state = {name: 0.0 for name in self.action_features}

    def get_action(self) -> dict[str, float]:
        return dict(self.state)

    def get_observation(self) -> dict[str, float]:
        return dict(self.state)

    def send_action(self, action: dict[str, float]) -> dict[str, float]:
        self.state.update(action)
        return dict(action)


class _SimulationPolicy:
    def predict(self, observation: dict[str, Any], task: str) -> list[list[float]]:
        return [[float(value) for value in observation["state"]]]

    def close(self) -> None:
        return None


class SimulationRuntime:
    """Deterministic resource owner used by default and by automated validation."""

    def __init__(self, settings: SessionSettings, *, stop_event: Event | None = None) -> None:
        self.settings = settings
        self.stop_event = stop_event or Event()
        self.arm = _SimulationArm()
        self.transaction = ResourceTransaction([_SimulationResource("simulation")])
        self.rate_callback: Callable[[LoopMetrics], None] | None = None
        self.telemetry_callback: Callable[[JointTelemetry], None] | None = None
        self.preview_callback: Callable[[str, bytes, float], None] | None = None
        self.recording_session: RecordingSession | None = None
        self._recording_lock = Lock()

    def acquire(self) -> None:
        self.transaction.acquire_all()
        if self.settings.mode == "record":
            self.recording_session = self._create_recording_session()

    def enable_motion(self) -> None:
        return None

    def teleoperate(self) -> None:
        self._teleoperation_loop(on_step=lambda *_args: self._publish_previews()).run()

    def record(self) -> None:
        if self.recording_session is None:
            raise RuntimeError("Simulation recording requires dataset settings")

        def add_frame(
            _observation: dict[str, Any],
            raw_action: dict[str, float],
            sent_action: dict[str, float],
        ) -> None:
            import numpy as np

            with self._recording_lock:
                if self.recording_session is not None:
                    self.recording_session.add_frame(
                        {
                            "observation.state": np.asarray(list(raw_action.values()), dtype=np.float32),
                            "action": np.asarray(list(sent_action.values()), dtype=np.float32),
                            "task": self.settings.task,
                        }
                    )
            self._publish_previews()

        self._teleoperation_loop(on_step=add_frame).run()

    def policy(self) -> None:
        if self.settings.max_relative_target is None:
            raise RuntimeError("Policy mode requires max_relative_target")
        PolicyControlLoop(
            self.arm,
            _SimulationPolicy(),
            task=self.settings.task,
            fps=self.settings.control_fps,
            max_duration_s=float(self.settings.rollout_time_s),
            max_relative_target=self.settings.max_relative_target,
            stop_event=self.stop_event,
            read_cameras=self._read_simulation_cameras,
            on_metrics=self.rate_callback,
            on_telemetry=self.telemetry_callback,
        ).run()

    def command(self, action: str) -> RecordingCommandResult:
        if self.recording_session is None:
            raise RuntimeError("Episode commands require record mode")
        with self._recording_lock:
            result = self.recording_session.command(action)  # type: ignore[arg-type]
        if result.should_stop:
            self.request_stop()
        return result

    def request_stop(self) -> None:
        self.stop_event.set()

    def cleanup(self) -> CleanupReport:
        self.request_stop()
        errors: list[str] = []
        if self.recording_session is not None:
            try:
                with self._recording_lock:
                    self.recording_session.finalize_for_cleanup()
            except Exception as error:
                errors.append(f"dataset finalization failed: {error}")
        report = self.transaction.release_all()
        errors.extend(report.errors)
        return CleanupReport(not errors and report.cleanup_complete, report.released, tuple(errors), True)

    def upload_after_cleanup(self) -> tuple[bool, bool, str | None]:
        return False, False, None

    def set_rate_callback(self, callback: Callable[[LoopMetrics], None]) -> None:
        self.rate_callback = callback

    def set_telemetry_callback(self, callback: Callable[[JointTelemetry], None]) -> None:
        self.telemetry_callback = callback

    def set_preview_callback(self, callback: Callable[[str, bytes, float], None]) -> None:
        self.preview_callback = callback

    def _teleoperation_loop(
        self,
        *,
        on_step: Callable[[dict[str, Any], dict[str, float], dict[str, float]], None],
    ) -> TeleoperationLoop:
        return TeleoperationLoop(
            self.arm,
            self.arm,
            fps=self.settings.control_fps,
            max_duration_s=float(self.settings.episode_time_s),
            stop_event=self.stop_event,
            expected_action_keys=set(self.arm.action_features),
            on_metrics=self.rate_callback,
            on_telemetry=self.telemetry_callback,
            on_step=on_step,
        )

    def _read_simulation_cameras(self) -> dict[str, bytes]:
        self._publish_previews()
        return {"wrist": b"simulation", "front": b"simulation"}

    def _publish_previews(self) -> None:
        if self.preview_callback is None:
            return
        payload = b"\xff\xd8\xff\xd9"
        captured_at = time.time()
        self.preview_callback("wrist", payload, captured_at)
        self.preview_callback("front", payload, captured_at)

    def _create_recording_session(self) -> RecordingSession:
        if self.settings.dataset_root is None or self.settings.dataset_id is None or self.settings.repo_id is None:
            raise RuntimeError("Record mode requires dataset_root, dataset_id, and repo_id")
        from lerobot.datasets.lerobot_dataset import LeRobotDataset  # type: ignore[import-untyped]

        names = list(self.arm.action_features)
        features = {
            "observation.state": {"dtype": "float32", "shape": (len(names),), "names": names},
            "action": {"dtype": "float32", "shape": (len(names),), "names": names},
        }
        dataset_root = Path(self.settings.dataset_root)
        dataset = LeRobotDataset.create(
            self.settings.repo_id,
            self.settings.control_fps,
            features=features,
            root=dataset_root,
            robot_type="so101_simulation",
            use_videos=False,
        )
        return RecordingSession(
            dataset,
            dataset_id=self.settings.dataset_id,
            dataset_root=dataset_root,
            num_episodes=self.settings.num_episodes,
        )


def create_runtime(
    profile: Mapping[str, Any],
    settings: SessionSettings,
    stop_event: Event | None = None,
    *,
    calibration_validator: Callable[[Path], Any] = validate_calibration_file,
    camera_checker: Callable[[WorkerProfile], CleanupReport] = check_profile_cameras,
    physical_builder: Callable[[WorkerProfile, SessionSettings, Event | None], Runtime] | None = None,
) -> Runtime:
    """Create simulation by default or gate physical construction on independent checks."""
    if settings.execution_mode == "simulation":
        return SimulationRuntime(settings, stop_event=stop_event)

    validated_profile = WorkerProfile.model_validate(profile)
    calibration_validator(validated_profile.leader.calibration_file)
    calibration_validator(validated_profile.follower.calibration_file)
    camera_report = camera_checker(validated_profile)
    if not camera_report.cleanup_complete:
        raise RuntimeError("Physical camera readiness check failed")
    if physical_builder is None:
        from .so101 import build_so101_runtime

        physical_builder = build_so101_runtime
    return physical_builder(validated_profile, settings, stop_event)
