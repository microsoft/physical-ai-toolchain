#!/usr/bin/env python3
"""ROS2 node for ACT policy inference on a UR10E.

Subscribes to joint states and camera images, runs the trained ACT policy,
and publishes joint position commands at the control frequency (30 Hz).

ROS2 Topics:
    Subscriptions:
        /joint_states             (sensor_msgs/JointState)
        /camera/color/image_raw   (sensor_msgs/Image)

    Publications:
        /lerobot/joint_commands   (trajectory_msgs/JointTrajectory)
        /lerobot/status           (std_msgs/String)

Parameters:
    policy_repo    (str)  - HuggingFace repo ID or local path
    device         (str)  - Inference device: cuda, cpu, mps
    control_hz     (float) - Control loop frequency
    action_mode    (str)  - "absolute" or "delta"
    enable_control (bool) - Whether to publish commands (safety gate)

Usage:
    ros2 run lerobot_inference act_inference_node \\
        --ros-args -p policy_repo:=alizaidi/hve-robo-act-train \\
                   -p device:=cuda \\
                   -p enable_control:=false
"""

from __future__ import annotations

import numpy as np
import rclpy
from builtin_interfaces.msg import Duration
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import String
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

from inference.policy_runner import PolicyRunner
from inference.robot_types import (
    CONTROL_HZ,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    JOINT_ORDER,
    NUM_JOINTS,
    JointPositionCommand,
    RobotObservation,
    RobotState,
)


class ACTInferenceNode(Node):
    """ROS2 node that runs a trained LeRobot ACT policy in a control loop."""

    def __init__(self) -> None:
        super().__init__("act_inference_node")

        # Declare parameters
        self.declare_parameter("policy_repo", "alizaidi/hve-robo-act-train")
        self.declare_parameter("device", "cuda")
        self.declare_parameter("control_hz", float(CONTROL_HZ))
        self.declare_parameter("action_mode", "delta")
        self.declare_parameter("enable_control", False)
        self.declare_parameter("camera_topic", "/camera/color/image_raw")
        self.declare_parameter("joint_states_topic", "/joint_states")

        policy_repo = self.get_parameter("policy_repo").value
        device = self.get_parameter("device").value
        self._control_hz = self.get_parameter("control_hz").value
        self._action_mode = self.get_parameter("action_mode").value
        self._enable_control = self.get_parameter("enable_control").value
        camera_topic = self.get_parameter("camera_topic").value
        joint_states_topic = self.get_parameter("joint_states_topic").value

        self.get_logger().info(f"Loading policy: {policy_repo}")
        self._runner = PolicyRunner.from_pretrained(policy_repo, device=device)
        self.get_logger().info(f"Policy loaded on {self._runner.device}")

        self._state = RobotState()
        self._bridge = CvBridge()

        # Joint name → index mapping for reordering from /joint_states
        self._joint_name_to_idx: dict[str, int] = {j.value: i for i, j in enumerate(JOINT_ORDER)}

        # Subscribers
        sensor_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        self.create_subscription(
            JointState,
            joint_states_topic,
            self._on_joint_state,
            sensor_qos,
        )
        self.create_subscription(
            Image,
            camera_topic,
            self._on_image,
            sensor_qos,
        )

        # Publishers
        self._cmd_pub = self.create_publisher(
            JointTrajectory,
            "/lerobot/joint_commands",
            10,
        )
        self._status_pub = self.create_publisher(
            String,
            "/lerobot/status",
            10,
        )

        # Control timer
        period_s = 1.0 / self._control_hz
        self._timer = self.create_timer(period_s, self._control_tick)

        self._runner.reset()
        self._state.is_episode_active = True

        safety = "ENABLED" if self._enable_control else "DISABLED (dry run)"
        self.get_logger().info(
            f"Inference node ready at {self._control_hz} Hz, action_mode={self._action_mode}, control={safety}"
        )

    # -- Subscriber callbacks ------------------------------------------------

    def _on_joint_state(self, msg: JointState) -> None:
        """Reorder incoming joint state to match training feature order."""
        positions = np.zeros(NUM_JOINTS, dtype=np.float32)
        for i, name in enumerate(msg.name):
            if name in self._joint_name_to_idx:
                positions[self._joint_name_to_idx[name]] = msg.position[i]

        if self._state.observation is None:
            self._state.observation = RobotObservation(
                joint_positions=positions,
                timestamp_s=msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9,
            )
        else:
            self._state.observation.joint_positions = positions
            self._state.observation.timestamp_s = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9

    def _on_image(self, msg: Image) -> None:
        """Convert ROS Image to numpy array, resize if necessary."""
        img = self._bridge.imgmsg_to_cv2(msg, desired_encoding="rgb8")

        if img.shape[:2] != (IMAGE_HEIGHT, IMAGE_WIDTH):
            import cv2

            img = cv2.resize(img, (IMAGE_WIDTH, IMAGE_HEIGHT))

        if self._state.observation is None:
            self._state.observation = RobotObservation(
                joint_positions=np.zeros(NUM_JOINTS, dtype=np.float32),
                color_image=img,
            )
        else:
            self._state.observation.color_image = img

    # -- Control loop --------------------------------------------------------

    def _control_tick(self) -> None:
        """Run one policy step and publish the command."""
        obs = self._state.observation
        if obs is None or obs.color_image is None:
            return

        cmd = self._runner.step(obs)

        if self._action_mode == "absolute":
            cmd = cmd.as_absolute(obs.joint_positions)

        self._state.episode_step += 1

        # Publish command
        if self._enable_control:
            self._publish_command(cmd)

        # Publish status
        m = self._runner.metrics
        status = String()
        status.data = (
            f"step={self._state.episode_step} "
            f"inf={m.avg_inference_ms:.1f}ms "
            f"pre={m.avg_preprocess_ms:.1f}ms "
            f"cmd=[{', '.join(f'{p:.4f}' for p in cmd.positions)}]"
        )
        self._status_pub.publish(status)

        if self._state.episode_step % 30 == 0:
            self.get_logger().info(status.data)

    def _publish_command(self, cmd: JointPositionCommand) -> None:
        """Publish a JointTrajectory message to the UR driver."""
        traj = JointTrajectory()
        traj.header.stamp = self.get_clock().now().to_msg()
        traj.joint_names = [j.value for j in JOINT_ORDER]

        point = JointTrajectoryPoint()
        point.positions = cmd.positions.tolist()
        point.velocities = [0.0] * NUM_JOINTS
        point.time_from_start = Duration(
            sec=0,
            nanosec=int(1e9 / self._control_hz),
        )
        traj.points = [point]

        self._cmd_pub.publish(traj)


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = ACTInferenceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        m = node._runner.metrics
        node.get_logger().info(f"Shutting down. Steps={m.steps}, avg_inference={m.avg_inference_ms:.1f}ms")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
