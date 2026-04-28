"""Unit tests for `act_inference_node` covering init, callbacks, control loop, and main."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from enum import Enum
from unittest.mock import MagicMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Stub external ROS / cv / inference modules BEFORE importing the SUT.
# ---------------------------------------------------------------------------


def _install_stub(name: str, module: types.ModuleType) -> None:
    sys.modules[name] = module


# rclpy
_rclpy = types.ModuleType("rclpy")
_rclpy.init = MagicMock()
_rclpy.spin = MagicMock()
_rclpy.shutdown = MagicMock()
_install_stub("rclpy", _rclpy)


class _StubNode:
    def __init__(self, name: str) -> None:
        self._name = name
        self._params: dict[str, object] = {}
        self._logger = MagicMock()
        self._clock = MagicMock()
        self._clock.now.return_value.to_msg.return_value = "stamp"
        self.subscriptions: list[tuple] = []
        self.publishers: dict[str, MagicMock] = {}
        self.timers: list[tuple] = []

    def declare_parameter(self, key: str, default: object) -> None:
        self._params[key] = default

    def get_parameter(self, key: str):
        param = MagicMock()
        param.value = self._params[key]
        return param

    def create_subscription(self, msg_type, topic, cb, qos):
        self.subscriptions.append((msg_type, topic, cb, qos))
        return MagicMock()

    def create_publisher(self, msg_type, topic, depth):
        pub = MagicMock()
        self.publishers[topic] = pub
        return pub

    def create_timer(self, period, cb):
        self.timers.append((period, cb))
        return MagicMock()

    def get_logger(self):
        return self._logger

    def get_clock(self):
        return self._clock

    def destroy_node(self) -> None:
        pass


_rclpy_node = types.ModuleType("rclpy.node")
_rclpy_node.Node = _StubNode
_install_stub("rclpy.node", _rclpy_node)


_rclpy_qos = types.ModuleType("rclpy.qos")


class _ReliabilityPolicy(Enum):
    BEST_EFFORT = 1
    RELIABLE = 2


@dataclass
class _QoSProfile:
    depth: int
    reliability: _ReliabilityPolicy = _ReliabilityPolicy.RELIABLE


_rclpy_qos.QoSProfile = _QoSProfile
_rclpy_qos.ReliabilityPolicy = _ReliabilityPolicy
_install_stub("rclpy.qos", _rclpy_qos)


# sensor_msgs.msg
_sensor_msgs = types.ModuleType("sensor_msgs")
_sensor_msgs_msg = types.ModuleType("sensor_msgs.msg")


class _Image:
    pass


class _Header:
    def __init__(self, sec: int = 0, nanosec: int = 0) -> None:
        self.stamp = types.SimpleNamespace(sec=sec, nanosec=nanosec)


class _JointState:
    def __init__(self, names=None, positions=None, sec=0, nanosec=0) -> None:
        self.name = names or []
        self.position = positions or []
        self.header = _Header(sec, nanosec)


_sensor_msgs_msg.Image = _Image
_sensor_msgs_msg.JointState = _JointState
_install_stub("sensor_msgs", _sensor_msgs)
_install_stub("sensor_msgs.msg", _sensor_msgs_msg)


# std_msgs.msg
_std_msgs = types.ModuleType("std_msgs")
_std_msgs_msg = types.ModuleType("std_msgs.msg")


class _String:
    def __init__(self) -> None:
        self.data = ""


_std_msgs_msg.String = _String
_install_stub("std_msgs", _std_msgs)
_install_stub("std_msgs.msg", _std_msgs_msg)


# trajectory_msgs.msg
_trajectory_msgs = types.ModuleType("trajectory_msgs")
_trajectory_msgs_msg = types.ModuleType("trajectory_msgs.msg")


class _JointTrajectory:
    def __init__(self) -> None:
        self.header = types.SimpleNamespace(stamp=None)
        self.joint_names: list[str] = []
        self.points: list = []


class _JointTrajectoryPoint:
    def __init__(self) -> None:
        self.positions: list[float] = []
        self.velocities: list[float] = []
        self.time_from_start = None


_trajectory_msgs_msg.JointTrajectory = _JointTrajectory
_trajectory_msgs_msg.JointTrajectoryPoint = _JointTrajectoryPoint
_install_stub("trajectory_msgs", _trajectory_msgs)
_install_stub("trajectory_msgs.msg", _trajectory_msgs_msg)


# builtin_interfaces.msg
_builtin = types.ModuleType("builtin_interfaces")
_builtin_msg = types.ModuleType("builtin_interfaces.msg")


@dataclass
class _Duration:
    sec: int = 0
    nanosec: int = 0


_builtin_msg.Duration = _Duration
_install_stub("builtin_interfaces", _builtin)
_install_stub("builtin_interfaces.msg", _builtin_msg)


# cv_bridge
_cv_bridge = types.ModuleType("cv_bridge")


class _CvBridge:
    def __init__(self) -> None:
        self.last_encoding: str | None = None
        self.next_image: np.ndarray | None = None

    def imgmsg_to_cv2(self, msg, desired_encoding: str = "rgb8") -> np.ndarray:
        self.last_encoding = desired_encoding
        if self.next_image is not None:
            return self.next_image
        return np.zeros((480, 848, 3), dtype=np.uint8)


_cv_bridge.CvBridge = _CvBridge
_install_stub("cv_bridge", _cv_bridge)


# cv2 (lazy-imported inside _on_image)
_cv2 = types.ModuleType("cv2")
_cv2.resize = MagicMock(side_effect=lambda img, size: np.zeros((size[1], size[0], 3), dtype=np.uint8))
_install_stub("cv2", _cv2)


# inference.policy_runner / inference.robot_types
_inference_pkg = types.ModuleType("inference")
_install_stub("inference", _inference_pkg)


@dataclass
class _Metrics:
    steps: int = 0
    avg_inference_ms: float = 12.5
    avg_preprocess_ms: float = 1.5


@dataclass
class _JointPositionCommand:
    positions: np.ndarray
    is_delta: bool = False

    def as_absolute(self, current: np.ndarray) -> "_JointPositionCommand":
        return _JointPositionCommand(positions=current + self.positions, is_delta=False)


class _PolicyRunner:
    last_init_kwargs: dict = {}

    def __init__(self, device: str = "cuda") -> None:
        self.device = device
        self.metrics = _Metrics()
        self.reset = MagicMock()
        self.step = MagicMock(
            return_value=_JointPositionCommand(
                positions=np.zeros(6, dtype=np.float32),
                is_delta=True,
            )
        )

    @classmethod
    def from_pretrained(cls, repo: str, device: str = "cuda") -> "_PolicyRunner":
        cls.last_init_kwargs = {"repo": repo, "device": device}
        return cls(device=device)


_inference_policy_runner = types.ModuleType("inference.policy_runner")
_inference_policy_runner.PolicyRunner = _PolicyRunner
_install_stub("inference.policy_runner", _inference_policy_runner)


@dataclass
class _RobotObservation:
    joint_positions: np.ndarray
    color_image: np.ndarray | None = None
    timestamp_s: float = 0.0


@dataclass
class _RobotState:
    observation: _RobotObservation | None = None
    is_episode_active: bool = False
    episode_step: int = 0


class _JointName(Enum):
    SHOULDER_PAN = "shoulder_pan_joint"
    SHOULDER_LIFT = "shoulder_lift_joint"
    ELBOW = "elbow_joint"
    WRIST_1 = "wrist_1_joint"
    WRIST_2 = "wrist_2_joint"
    WRIST_3 = "wrist_3_joint"


_inference_robot_types = types.ModuleType("inference.robot_types")
_inference_robot_types.CONTROL_HZ = 30
_inference_robot_types.IMAGE_HEIGHT = 480
_inference_robot_types.IMAGE_WIDTH = 848
_inference_robot_types.NUM_JOINTS = 6
_inference_robot_types.JOINT_ORDER = list(_JointName)
_inference_robot_types.JointPositionCommand = _JointPositionCommand
_inference_robot_types.RobotObservation = _RobotObservation
_inference_robot_types.RobotState = _RobotState
_install_stub("inference.robot_types", _inference_robot_types)


# fleet-deployment is hyphenated so it cannot be imported as a normal package.
# Load act_inference_node by file path after stubs are installed.
import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

_ain_path = Path(__file__).resolve().parent.parent / "act_inference_node.py"
_ain_spec = importlib.util.spec_from_file_location("act_inference_node", _ain_path)
ain_module = importlib.util.module_from_spec(_ain_spec)
sys.modules["act_inference_node"] = ain_module
_ain_spec.loader.exec_module(ain_module)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def node():
    """Construct an ACTInferenceNode with all stubs in place."""
    return ain_module.ACTInferenceNode()


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_parameters_and_setup(self, node):
        assert node._control_hz == 30
        assert node._action_mode == "delta"
        assert node._enable_control is False
        assert node._state.is_episode_active is True
        assert len(node.subscriptions) == 2
        assert "/lerobot/joint_commands" in node.publishers
        assert "/lerobot/status" in node.publishers
        assert len(node.timers) == 1
        node._runner.reset.assert_called_once()


# ---------------------------------------------------------------------------
# _on_joint_state
# ---------------------------------------------------------------------------


class TestOnJointState:
    def test_first_message_creates_observation(self, node):
        msg = _JointState(
            names=["shoulder_pan_joint", "elbow_joint"],
            positions=[0.5, 1.5],
            sec=2,
            nanosec=500_000_000,
        )
        node._on_joint_state(msg)
        obs = node._state.observation
        assert obs is not None
        assert obs.joint_positions[0] == pytest.approx(0.5)
        assert obs.joint_positions[2] == pytest.approx(1.5)
        assert obs.timestamp_s == pytest.approx(2.5)

    def test_subsequent_message_updates_existing(self, node):
        node._state.observation = _RobotObservation(
            joint_positions=np.zeros(6, dtype=np.float32),
            timestamp_s=0.0,
        )
        msg = _JointState(
            names=["wrist_3_joint"],
            positions=[0.25],
            sec=1,
            nanosec=0,
        )
        node._on_joint_state(msg)
        assert node._state.observation.joint_positions[5] == pytest.approx(0.25)
        assert node._state.observation.timestamp_s == pytest.approx(1.0)

    def test_unknown_joint_is_ignored(self, node):
        msg = _JointState(names=["nonexistent_joint"], positions=[42.0])
        node._on_joint_state(msg)
        assert np.allclose(node._state.observation.joint_positions, 0.0)


# ---------------------------------------------------------------------------
# _on_image
# ---------------------------------------------------------------------------


class TestOnImage:
    def test_image_passes_through_when_correct_size(self, node):
        node._bridge.next_image = np.ones((480, 848, 3), dtype=np.uint8)
        node._on_image(MagicMock())
        assert node._state.observation is not None
        assert node._state.observation.color_image.shape == (480, 848, 3)

    def test_image_resized_when_wrong_size(self, node):
        node._bridge.next_image = np.ones((100, 100, 3), dtype=np.uint8)
        node._on_image(MagicMock())
        assert node._state.observation.color_image.shape == (480, 848, 3)
        _cv2.resize.assert_called()

    def test_updates_existing_observation(self, node):
        node._state.observation = _RobotObservation(
            joint_positions=np.full(6, 0.7, dtype=np.float32),
        )
        node._bridge.next_image = np.ones((480, 848, 3), dtype=np.uint8)
        node._on_image(MagicMock())
        assert np.allclose(node._state.observation.joint_positions, 0.7)
        assert node._state.observation.color_image is not None


# ---------------------------------------------------------------------------
# _control_tick
# ---------------------------------------------------------------------------


class TestControlTick:
    def test_returns_early_when_no_observation(self, node):
        node._control_tick()
        node._runner.step.assert_not_called()

    def test_returns_early_when_no_image(self, node):
        node._state.observation = _RobotObservation(
            joint_positions=np.zeros(6, dtype=np.float32),
            color_image=None,
        )
        node._control_tick()
        node._runner.step.assert_not_called()

    def test_delta_mode_publishes_status_only(self, node):
        node._state.observation = _RobotObservation(
            joint_positions=np.zeros(6, dtype=np.float32),
            color_image=np.zeros((480, 848, 3), dtype=np.uint8),
        )
        node._action_mode = "delta"
        node._control_tick()
        node._runner.step.assert_called_once()
        node.publishers["/lerobot/status"].publish.assert_called_once()
        node.publishers["/lerobot/joint_commands"].publish.assert_not_called()
        assert node._state.episode_step == 1

    def test_absolute_mode_calls_as_absolute(self, node):
        node._state.observation = _RobotObservation(
            joint_positions=np.full(6, 0.1, dtype=np.float32),
            color_image=np.zeros((480, 848, 3), dtype=np.uint8),
        )
        node._action_mode = "absolute"
        node._enable_control = True
        node._runner.step.return_value = _JointPositionCommand(
            positions=np.full(6, 0.2, dtype=np.float32),
            is_delta=True,
        )
        node._control_tick()
        node.publishers["/lerobot/joint_commands"].publish.assert_called_once()

    def test_logs_at_30_step_boundary(self, node):
        node._state.observation = _RobotObservation(
            joint_positions=np.zeros(6, dtype=np.float32),
            color_image=np.zeros((480, 848, 3), dtype=np.uint8),
        )
        node._state.episode_step = 29
        node._control_tick()
        node._logger.info.assert_called()


# ---------------------------------------------------------------------------
# _publish_command
# ---------------------------------------------------------------------------


class TestPublishCommand:
    def test_builds_trajectory_with_joint_names_and_velocities(self, node):
        cmd = _JointPositionCommand(
            positions=np.arange(6, dtype=np.float32),
            is_delta=False,
        )
        node._publish_command(cmd)
        node.publishers["/lerobot/joint_commands"].publish.assert_called_once()
        traj = node.publishers["/lerobot/joint_commands"].publish.call_args.args[0]
        assert traj.joint_names == [j.value for j in _inference_robot_types.JOINT_ORDER]
        assert traj.points[0].positions == list(range(6))
        assert traj.points[0].velocities == [0.0] * 6
        assert traj.points[0].time_from_start.nanosec == int(1e9 / 30)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    def test_normal_flow_initializes_and_shuts_down(self, monkeypatch):
        _rclpy.init.reset_mock()
        _rclpy.spin.reset_mock()
        _rclpy.shutdown.reset_mock()
        ain_module.main()
        _rclpy.init.assert_called_once()
        _rclpy.spin.assert_called_once()
        _rclpy.shutdown.assert_called_once()

    def test_keyboard_interrupt_logs_metrics(self, monkeypatch):
        _rclpy.init.reset_mock()
        _rclpy.shutdown.reset_mock()
        monkeypatch.setattr(_rclpy, "spin", MagicMock(side_effect=KeyboardInterrupt))
        ain_module.main()
        _rclpy.shutdown.assert_called_once()
