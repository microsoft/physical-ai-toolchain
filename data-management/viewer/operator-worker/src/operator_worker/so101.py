"""LeRobot SO-101 construction and single-owner runtime behavior."""

from __future__ import annotations

import queue
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

from .calibration import JOINTS, validate_calibration_file
from .config import WorkerProfile
from .identity import verify_tty_identity
from .policy_client import GrootPolicyClient
from .policy_control import PolicyControlLoop
from .protocol import SessionSettings
from .recording import RecordingCommandResult, RecordingSession
from .resources import ArmResource, CameraResource, CleanupReport, ResourceTransaction
from .teleoperate import JointTelemetry, LoopMetrics, TeleoperationLoop


def _discard_dataset(dataset_root: Path | None, dataset_id: str | None) -> bool:
    if dataset_root is None:
        return False
    if dataset_id is None or dataset_root.name != dataset_id or dataset_root.is_symlink():
        raise RuntimeError("Recording dataset root does not match the session dataset ID")
    if not dataset_root.exists():
        return False
    if not dataset_root.is_dir():
        raise RuntimeError("Recording dataset root must be a real directory")
    shutil.rmtree(dataset_root)
    return True


def _configure_leader_torque_off(bus: Any) -> None:
    from lerobot.motors.feetech import OperatingMode  # type: ignore[import-untyped]

    bus.configure_motors()
    for motor in bus.motors:
        bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)


def _configure_follower_torque_off(bus: Any) -> None:
    from lerobot.motors.feetech import OperatingMode  # type: ignore[import-untyped]

    bus.configure_motors()
    for motor in bus.motors:
        bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)
        bus.write("P_Coefficient", motor, 16)
        bus.write("I_Coefficient", motor, 0)
        bus.write("D_Coefficient", motor, 32)
        if motor == "gripper":
            bus.write("Max_Torque_Limit", motor, 500)
            bus.write("Protection_Current", motor, 250)
            bus.write("Overload_Torque", motor, 25)


@dataclass
class SO101Runtime:
    """Own all hardware, recording, and policy resources for one session."""

    profile: WorkerProfile
    settings: SessionSettings
    leader: Any
    follower: Any
    wrist: Any
    front: Any
    leader_resource: ArmResource
    follower_resource: ArmResource
    transaction: ResourceTransaction
    stop_event: Event
    observation_processor: Any
    teleop_action_processor: Any
    robot_action_processor: Any
    rate_callback: Callable[[LoopMetrics], None] | None = None
    telemetry_callback: Callable[[JointTelemetry], None] | None = None
    preview_callback: Callable[[str, bytes, float], None] | None = None
    last_preview_at: dict[str, float] = field(default_factory=dict)
    recording_session: RecordingSession | None = None
    recording_commands: queue.Queue[tuple[str, Event, list[RecordingCommandResult | BaseException]]] = field(
        default_factory=queue.Queue
    )
    policy_client: GrootPolicyClient | None = None
    discard_requested: bool = False

    def acquire(self) -> None:
        self.transaction.acquire_all()
        if self.settings.mode == "record":
            self._initialize_recording()

    def enable_motion(self) -> None:
        self.follower_resource.enable_motion()

    def teleoperate(self) -> None:
        self._teleoperation_loop(on_step=lambda observation, _raw, _sent: self._publish_previews(observation)).run()

    def record(self) -> None:
        if self.recording_session is None:
            raise RuntimeError("Recording dataset is unavailable")
        from lerobot.utils.constants import ACTION, OBS_STR  # type: ignore[import-untyped]
        from lerobot.utils.feature_utils import build_dataset_frame  # type: ignore[import-untyped]

        features = self.recording_session.features

        def add_recording_frame(
            observation: dict[str, Any],
            _raw_action: dict[str, float],
            sent_action: dict[str, float],
        ) -> None:
            self._publish_previews(observation)
            if self.recording_session is None:
                return
            observation_frame = build_dataset_frame(features, observation, OBS_STR)
            action_frame = build_dataset_frame(features, sent_action, ACTION)
            self.recording_session.add_frame({**observation_frame, **action_frame, "task": self.settings.task})
            self._apply_recording_commands()

        self._teleoperation_loop(on_step=add_recording_frame).run()

    def policy(self) -> None:
        if self.policy_client is None or self.settings.max_relative_target is None:
            raise RuntimeError("Policy runtime is unavailable")

        def read_cameras() -> dict[str, Any]:
            frames = self._read_camera_frames()
            self._publish_previews(frames)
            return frames

        PolicyControlLoop(
            self.follower,
            self.policy_client,
            task=self.settings.task,
            fps=self.settings.control_fps,
            max_duration_s=float(self.settings.rollout_time_s),
            max_relative_target=self.settings.max_relative_target,
            stop_event=self.stop_event,
            read_cameras=read_cameras,
            on_metrics=self.rate_callback,
            on_telemetry=self.telemetry_callback,
        ).run()

    def command(self, action: str) -> RecordingCommandResult:
        if self.settings.mode != "record":
            raise RuntimeError("Episode commands require record mode")
        completed = Event()
        result: list[RecordingCommandResult | BaseException] = []
        self.recording_commands.put((action, completed, result))
        if not completed.wait(120.0):
            raise RuntimeError("Recording command timed out")
        outcome = result[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def request_stop(self) -> None:
        self.stop_event.set()

    def cleanup(self) -> CleanupReport:
        self.request_stop()
        recording_errors: list[str] = []
        if self.recording_session is not None:
            try:
                self.recording_session.finalize_for_cleanup()
            except Exception as error:
                recording_errors.append(f"dataset finalization failed: {error}")
        report = self.transaction.release_all()
        dataset_released: tuple[str, ...] = ()
        if self.discard_requested:
            try:
                root = Path(self.settings.dataset_root) if self.settings.dataset_root else None
                if _discard_dataset(root, self.settings.dataset_id):
                    dataset_released = ("dataset",)
            except Exception as error:
                recording_errors.append(f"dataset discard failed: {error}")
        torque_off = self.leader_resource.torque_verified_off and self.follower_resource.torque_verified_off
        return CleanupReport(
            cleanup_complete=report.cleanup_complete and torque_off and not recording_errors,
            released=(*report.released, *dataset_released),
            errors=(*report.errors, *recording_errors),
            torque_verified_off=torque_off,
        )

    def upload_after_cleanup(self) -> tuple[bool, bool, str | None]:
        session = self.recording_session
        if (
            self.discard_requested
            or session is None
            or self.settings.save_destination != "local_and_hub"
            or session.phase != "finalized"
        ):
            return False, False, None
        try:
            session.push_to_hub()
        except Exception:
            return True, False, "Hugging Face upload failed; local dataset is preserved"
        return True, True, None

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
            self.leader,
            self.follower,
            fps=self.settings.control_fps,
            max_duration_s=float(self.settings.episode_time_s),
            stop_event=self.stop_event,
            expected_action_keys=set(self.leader.action_features),
            observation_processor=self.observation_processor,
            teleop_action_processor=self.teleop_action_processor,
            robot_action_processor=self.robot_action_processor,
            on_metrics=self.rate_callback,
            on_telemetry=self.telemetry_callback,
            extra_observation=self._read_camera_frames,
            on_step=on_step,
        )

    def _read_camera_frames(self) -> dict[str, Any]:
        return {"wrist": self.wrist.read_latest(), "front": self.front.read_latest()}

    def _publish_previews(self, observation: dict[str, Any]) -> None:
        if self.preview_callback is None:
            return
        import cv2  # type: ignore[import-untyped]

        now = time.monotonic()
        for camera in ("wrist", "front"):
            if now - self.last_preview_at.get(camera, 0.0) < 0.2:
                continue
            frame = observation.get(camera)
            if frame is None:
                continue
            encoded, jpeg = cv2.imencode(
                ".jpg",
                cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 70],
            )
            payload = jpeg.tobytes() if encoded else b""
            if not payload or len(payload) > 300_000:
                continue
            self.last_preview_at[camera] = now
            self.preview_callback(camera, payload, time.time())

    def _initialize_recording(self) -> None:
        if self.settings.dataset_root is None or self.settings.dataset_id is None or self.settings.repo_id is None:
            raise RuntimeError("Record mode requires a resolved local dataset path")
        from lerobot.datasets.lerobot_dataset import LeRobotDataset  # type: ignore[import-untyped]
        from lerobot.datasets.pipeline_features import (  # type: ignore[import-untyped]
            aggregate_pipeline_dataset_features,
            create_initial_features,
        )
        from lerobot.utils.feature_utils import combine_feature_dicts  # type: ignore[import-untyped]

        observation_features = {
            **self.follower.observation_features,
            "wrist": (self.profile.wrist_camera.height, self.profile.wrist_camera.width, 3),
            "front": (self.profile.front_camera.height, self.profile.front_camera.width, 3),
        }
        features = combine_feature_dicts(
            aggregate_pipeline_dataset_features(
                pipeline=self.teleop_action_processor,
                initial_features=create_initial_features(action=self.follower.action_features),
                use_videos=True,
            ),
            aggregate_pipeline_dataset_features(
                pipeline=self.observation_processor,
                initial_features=create_initial_features(observation=observation_features),
                use_videos=True,
            ),
        )
        dataset_root = Path(self.settings.dataset_root)
        dataset = LeRobotDataset.create(
            self.settings.repo_id,
            self.settings.control_fps,
            root=dataset_root,
            robot_type="so101_follower",
            features=features,
            use_videos=True,
            image_writer_processes=0,
            image_writer_threads=8,
            streaming_encoding=True,
            encoder_threads=2,
            encoder_queue_maxsize=max(30, self.settings.control_fps * 2),
        )
        self.recording_session = RecordingSession(
            dataset,
            dataset_id=self.settings.dataset_id,
            dataset_root=dataset_root,
            num_episodes=self.settings.num_episodes,
        )

    def _apply_recording_commands(self) -> None:
        if self.recording_session is None:
            return
        while True:
            try:
                action, completed, result = self.recording_commands.get_nowait()
            except queue.Empty:
                return
            try:
                outcome = self.recording_session.command(action)  # type: ignore[arg-type]
                result.append(outcome)
                if outcome.should_stop:
                    self.stop_event.set()
            except BaseException as error:
                result.append(error)
            finally:
                completed.set()


def build_so101_runtime(
    profile: WorkerProfile,
    settings: SessionSettings,
    stop_event: Event | None = None,
) -> SO101Runtime:
    """Construct LeRobot resources without connecting or enabling torque."""
    from lerobot.cameras.opencv import OpenCVCamera, OpenCVCameraConfig  # type: ignore[import-untyped]
    from lerobot.cameras.realsense import RealSenseCamera, RealSenseCameraConfig  # type: ignore[import-untyped]
    from lerobot.processor import make_default_processors  # type: ignore[import-untyped]
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig  # type: ignore[import-untyped]
    from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig  # type: ignore[import-untyped]

    wrist = OpenCVCamera(
        OpenCVCameraConfig(
            index_or_path=profile.wrist_camera.path,
            fps=settings.camera_fps.get("wrist", profile.wrist_camera.fps),
            width=profile.wrist_camera.width,
            height=profile.wrist_camera.height,
            warmup_s=1,
        )
    )
    front = RealSenseCamera(
        RealSenseCameraConfig(
            serial_number_or_name=profile.front_camera.usb_serial,
            fps=settings.camera_fps.get("front", profile.front_camera.fps),
            width=profile.front_camera.width,
            height=profile.front_camera.height,
            warmup_s=1,
        )
    )
    leader = SO101Leader(
        SO101LeaderConfig(
            port=str(profile.leader.port),
            id=profile.leader.logical_id,
            calibration_dir=profile.leader.calibration_file.parent,
        )
    )
    follower = SO101Follower(
        SO101FollowerConfig(
            port=str(profile.follower.port),
            id=profile.follower.logical_id,
            calibration_dir=profile.follower.calibration_file.parent,
            cameras={},
            max_relative_target=settings.max_relative_target,
        )
    )
    follower_resource = _arm_resource("follower", follower, profile, motion_capable=True)
    leader_resource = _arm_resource("leader", leader, profile, motion_capable=False)
    active_stop_event = stop_event or Event()
    policy_client = _policy_client(settings)
    resources = [
        *([policy_client] if policy_client is not None else []),
        CameraResource("wrist", wrist),
        CameraResource("front", front),
        *([leader_resource] if settings.mode != "policy" else []),
        follower_resource,
    ]
    transaction = ResourceTransaction(resources, cancel_requested=active_stop_event.is_set)
    teleop_processor, robot_processor, observation_processor = make_default_processors()
    return SO101Runtime(
        profile=profile,
        settings=settings,
        leader=leader,
        follower=follower,
        wrist=wrist,
        front=front,
        leader_resource=leader_resource,
        follower_resource=follower_resource,
        transaction=transaction,
        stop_event=active_stop_event,
        observation_processor=observation_processor,
        teleop_action_processor=teleop_processor,
        robot_action_processor=robot_processor,
        policy_client=policy_client,
    )


def build_deenergize_resources(profile_value: Any) -> list[ArmResource]:
    """Construct arm-only resources for watchdog torque-off recovery."""
    profile = profile_value if isinstance(profile_value, WorkerProfile) else WorkerProfile.model_validate(profile_value)
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig  # type: ignore[import-untyped]
    from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig  # type: ignore[import-untyped]

    leader = SO101Leader(
        SO101LeaderConfig(
            port=str(profile.leader.port),
            id=profile.leader.logical_id,
            calibration_dir=profile.leader.calibration_file.parent,
        )
    )
    follower = SO101Follower(
        SO101FollowerConfig(
            port=str(profile.follower.port),
            id=profile.follower.logical_id,
            calibration_dir=profile.follower.calibration_file.parent,
            cameras={},
        )
    )
    return [
        _arm_resource("leader", leader, profile, motion_capable=False),
        _arm_resource("follower", follower, profile, motion_capable=False),
    ]


def _arm_resource(name: str, arm: Any, profile: WorkerProfile, *, motion_capable: bool) -> ArmResource:
    arm_profile = profile.follower if name == "follower" else profile.leader
    configure = _configure_follower_torque_off if motion_capable else _configure_leader_torque_off
    return ArmResource(
        name,
        arm,
        calibration_file=arm_profile.calibration_file,
        validate_calibration=validate_calibration_file,
        configure_torque_off=configure,
        motion_capable=motion_capable,
        verify_identity=lambda: verify_tty_identity(
            arm_profile.port,
            (arm_profile.usb_vendor_id, arm_profile.usb_product_id, arm_profile.usb_serial),
        ),
        expected_motor_names=set(JOINTS),
    )


def _policy_client(settings: SessionSettings) -> GrootPolicyClient | None:
    if settings.mode != "policy":
        return None
    if settings.policy_python is None or settings.policy_checkpoint is None:
        raise RuntimeError("Policy mode requires policy_python and policy_checkpoint")
    return GrootPolicyClient(
        python=Path(settings.policy_python),
        checkpoint=Path(settings.policy_checkpoint),
        cuda_visible_devices=settings.policy_cuda_visible_devices,
    )
