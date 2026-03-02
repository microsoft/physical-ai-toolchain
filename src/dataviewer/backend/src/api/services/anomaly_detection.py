"""
Anomaly detection service.

Automatically detects anomalies in robot trajectories using
statistical methods and threshold-based detection.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray


class AnomalyType(StrEnum):
    """Types of detectable anomalies."""

    VELOCITY_SPIKE = "velocity_spike"
    FORCE_SPIKE = "force_spike"
    TRAJECTORY_DEVIATION = "trajectory_deviation"
    UNEXPECTED_STOP = "unexpected_stop"
    GRIPPER_FAILURE = "gripper_failure"
    OSCILLATION = "oscillation"
    JOINT_LIMIT = "joint_limit"


class AnomalySeverity(StrEnum):
    """Anomaly severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class DetectedAnomaly:
    """A detected anomaly in the trajectory."""

    id: str
    """Unique identifier."""

    type: AnomalyType
    """Type of anomaly."""

    severity: AnomalySeverity
    """Severity level."""

    frame_range: tuple[int, int]
    """Start and end frame indices."""

    description: str
    """Human-readable description."""

    confidence: float
    """Detection confidence (0-1)."""

    auto_detected: bool = True
    """Whether this was auto-detected."""

    verified: bool = False
    """Whether a human has verified this."""


class AnomalyDetector:
    """
    Detects anomalies in robot trajectories.

    Uses statistical methods like z-scores, threshold detection,
    and pattern matching to identify issues.

    Example:
        >>> detector = AnomalyDetector()
        >>> positions = np.array([[0, 0, 0], [1, 1, 1], [10, 10, 10]])  # spike!
        >>> timestamps = np.array([0.0, 0.033, 0.066])
        >>> anomalies = detector.detect(positions, timestamps)
        >>> print(f"Found {len(anomalies)} anomalies")
    """

    def __init__(
        self,
        velocity_zscore_threshold: float = 3.0,
        stop_velocity_threshold: float = 0.01,
        stop_min_frames: int = 10,
        oscillation_min_cycles: int = 3,
        joint_limit_margin: float = 0.05,
    ) -> None:
        """
        Initialize the detector.

        Args:
            velocity_zscore_threshold: Z-score above this indicates velocity spike.
            stop_velocity_threshold: Velocity below this is considered stopped.
            stop_min_frames: Minimum frames to consider as unexpected stop.
            oscillation_min_cycles: Minimum cycles to detect oscillation.
            joint_limit_margin: Margin from joint limits to flag.
        """
        self.velocity_zscore_threshold = velocity_zscore_threshold
        self.stop_velocity_threshold = stop_velocity_threshold
        self.stop_min_frames = stop_min_frames
        self.oscillation_min_cycles = oscillation_min_cycles
        self.joint_limit_margin = joint_limit_margin

    def detect(
        self,
        positions: NDArray[np.float64],
        timestamps: NDArray[np.float64],
        forces: NDArray[np.float64] | None = None,
        gripper_states: NDArray[np.float64] | None = None,
        gripper_commands: NDArray[np.float64] | None = None,
        joint_limits: tuple[NDArray[np.float64], NDArray[np.float64]] | None = None,
    ) -> list[DetectedAnomaly]:
        """
        Detect anomalies in a trajectory.

        Args:
            positions: Joint positions array of shape (N, num_joints).
            timestamps: Timestamp array of shape (N,).
            forces: Optional force/torque array of shape (N, num_sensors).
            gripper_states: Optional gripper state array of shape (N,).
            gripper_commands: Optional gripper command array of shape (N,).
            joint_limits: Optional tuple of (lower_limits, upper_limits).

        Returns:
            List of detected anomalies.
        """
        anomalies: list[DetectedAnomaly] = []

        if len(positions) < 3:
            return anomalies

        # Compute velocity
        dt = np.diff(timestamps)
        dt = np.where(dt > 0, dt, 1e-6)
        velocity = np.diff(positions, axis=0) / dt[:, np.newaxis]

        # Detect velocity spikes
        anomalies.extend(self._detect_velocity_spikes(velocity))

        # Detect unexpected stops
        anomalies.extend(self._detect_unexpected_stops(velocity))

        # Detect oscillations
        anomalies.extend(self._detect_oscillations(positions))

        # Detect force spikes if available
        if forces is not None:
            anomalies.extend(self._detect_force_spikes(forces))

        # Detect gripper failures if available
        if gripper_states is not None and gripper_commands is not None:
            anomalies.extend(self._detect_gripper_failures(gripper_states, gripper_commands))

        # Detect joint limit approaches if available
        if joint_limits is not None:
            anomalies.extend(self._detect_joint_limits(positions, joint_limits))

        return anomalies

    def _detect_velocity_spikes(self, velocity: NDArray[np.float64]) -> list[DetectedAnomaly]:
        """
        Detect velocity spikes using z-score thresholding.
        """
        anomalies = []

        vel_magnitude = np.linalg.norm(velocity, axis=1)

        # Compute z-scores
        mean_vel = np.mean(vel_magnitude)
        std_vel = np.std(vel_magnitude)

        if std_vel < 1e-10:
            return anomalies

        z_scores = (vel_magnitude - mean_vel) / std_vel

        # Find spikes
        spike_mask = z_scores > self.velocity_zscore_threshold
        spike_indices = np.where(spike_mask)[0]

        # Group consecutive spikes
        if len(spike_indices) > 0:
            groups = self._group_consecutive(spike_indices)

            for group in groups:
                start_frame = int(group[0])
                end_frame = int(group[-1]) + 1
                max_zscore = float(np.max(z_scores[group]))

                severity = self._zscore_to_severity(max_zscore)

                anomalies.append(
                    DetectedAnomaly(
                        id=str(uuid.uuid4()),
                        type=AnomalyType.VELOCITY_SPIKE,
                        severity=severity,
                        frame_range=(start_frame, end_frame),
                        description=f"Velocity spike detected (z-score: {max_zscore:.1f})",
                        confidence=min(1.0, max_zscore / 5.0),
                    )
                )

        return anomalies

    def _detect_unexpected_stops(self, velocity: NDArray[np.float64]) -> list[DetectedAnomaly]:
        """
        Detect unexpected stops (velocity near zero for extended period).
        """
        anomalies = []

        vel_magnitude = np.linalg.norm(velocity, axis=1)
        is_stopped = vel_magnitude < self.stop_velocity_threshold

        # Find consecutive stopped segments
        groups = self._group_consecutive(np.where(is_stopped)[0])

        for group in groups:
            if len(group) >= self.stop_min_frames:
                # Exclude if at start or end (normal stops)
                if group[0] < 5 or group[-1] > len(velocity) - 5:
                    continue

                start_frame = int(group[0])
                end_frame = int(group[-1]) + 1
                duration = len(group)

                severity = (
                    AnomalySeverity.HIGH
                    if duration > 30
                    else AnomalySeverity.MEDIUM
                    if duration > 15
                    else AnomalySeverity.LOW
                )

                anomalies.append(
                    DetectedAnomaly(
                        id=str(uuid.uuid4()),
                        type=AnomalyType.UNEXPECTED_STOP,
                        severity=severity,
                        frame_range=(start_frame, end_frame),
                        description=f"Unexpected stop for {duration} frames",
                        confidence=min(1.0, duration / 30.0),
                    )
                )

        return anomalies

    def _detect_oscillations(self, positions: NDArray[np.float64]) -> list[DetectedAnomaly]:
        """
        Detect oscillatory motion patterns.
        """
        anomalies = []

        if len(positions) < 20:
            return anomalies

        # Detect sign changes in velocity for each joint
        velocity = np.diff(positions, axis=0)

        for joint_idx in range(positions.shape[1]):
            joint_vel = velocity[:, joint_idx]
            sign_changes = np.diff(np.sign(joint_vel))
            zero_crossings = np.where(sign_changes != 0)[0]

            # Look for rapid oscillations (multiple sign changes in short window)
            window_size = 20
            for i in range(0, len(zero_crossings) - self.oscillation_min_cycles):
                window_crossings = zero_crossings[
                    (zero_crossings >= zero_crossings[i]) & (zero_crossings < zero_crossings[i] + window_size)
                ]

                if len(window_crossings) >= self.oscillation_min_cycles * 2:
                    start_frame = int(window_crossings[0])
                    end_frame = int(window_crossings[-1]) + 1

                    # Check if we already have an overlapping anomaly
                    overlaps = any(
                        a.type == AnomalyType.OSCILLATION
                        and a.frame_range[0] <= end_frame
                        and a.frame_range[1] >= start_frame
                        for a in anomalies
                    )

                    if not overlaps:
                        anomalies.append(
                            DetectedAnomaly(
                                id=str(uuid.uuid4()),
                                type=AnomalyType.OSCILLATION,
                                severity=AnomalySeverity.MEDIUM,
                                frame_range=(start_frame, end_frame),
                                description=f"Oscillation detected in joint {joint_idx + 1}",
                                confidence=0.7,
                            )
                        )

        return anomalies

    def _detect_force_spikes(self, forces: NDArray[np.float64]) -> list[DetectedAnomaly]:
        """
        Detect force/torque spikes using z-score thresholding.
        """
        anomalies = []

        force_magnitude = np.linalg.norm(forces, axis=1)

        mean_force = np.mean(force_magnitude)
        std_force = np.std(force_magnitude)

        if std_force < 1e-10:
            return anomalies

        z_scores = (force_magnitude - mean_force) / std_force
        spike_mask = z_scores > self.velocity_zscore_threshold
        spike_indices = np.where(spike_mask)[0]

        groups = self._group_consecutive(spike_indices)

        for group in groups:
            start_frame = int(group[0])
            end_frame = int(group[-1]) + 1
            max_zscore = float(np.max(z_scores[group]))

            anomalies.append(
                DetectedAnomaly(
                    id=str(uuid.uuid4()),
                    type=AnomalyType.FORCE_SPIKE,
                    severity=self._zscore_to_severity(max_zscore),
                    frame_range=(start_frame, end_frame),
                    description=f"Force spike detected (z-score: {max_zscore:.1f})",
                    confidence=min(1.0, max_zscore / 5.0),
                )
            )

        return anomalies

    def _detect_gripper_failures(
        self,
        gripper_states: NDArray[np.float64],
        gripper_commands: NDArray[np.float64],
    ) -> list[DetectedAnomaly]:
        """
        Detect gripper state mismatches with commands.
        """
        anomalies = []

        # Simple threshold for mismatch detection
        mismatch = np.abs(gripper_states - gripper_commands) > 0.3
        mismatch_indices = np.where(mismatch)[0]

        groups = self._group_consecutive(mismatch_indices)

        for group in groups:
            if len(group) >= 5:  # Minimum duration
                start_frame = int(group[0])
                end_frame = int(group[-1]) + 1

                anomalies.append(
                    DetectedAnomaly(
                        id=str(uuid.uuid4()),
                        type=AnomalyType.GRIPPER_FAILURE,
                        severity=AnomalySeverity.HIGH,
                        frame_range=(start_frame, end_frame),
                        description="Gripper state does not match command",
                        confidence=0.9,
                    )
                )

        return anomalies

    def _detect_joint_limits(
        self,
        positions: NDArray[np.float64],
        joint_limits: tuple[NDArray[np.float64], NDArray[np.float64]],
    ) -> list[DetectedAnomaly]:
        """
        Detect when joints approach their limits.
        """
        anomalies = []
        lower_limits, upper_limits = joint_limits

        for joint_idx in range(positions.shape[1]):
            joint_positions = positions[:, joint_idx]
            lower = lower_limits[joint_idx]
            upper = upper_limits[joint_idx]
            margin = (upper - lower) * self.joint_limit_margin

            near_lower = joint_positions < (lower + margin)
            near_upper = joint_positions > (upper - margin)
            near_limit = near_lower | near_upper

            limit_indices = np.where(near_limit)[0]
            groups = self._group_consecutive(limit_indices)

            for group in groups:
                if len(group) >= 3:
                    start_frame = int(group[0])
                    end_frame = int(group[-1]) + 1

                    anomalies.append(
                        DetectedAnomaly(
                            id=str(uuid.uuid4()),
                            type=AnomalyType.JOINT_LIMIT,
                            severity=AnomalySeverity.MEDIUM,
                            frame_range=(start_frame, end_frame),
                            description=f"Joint {joint_idx + 1} near limit",
                            confidence=0.8,
                        )
                    )

        return anomalies

    def _group_consecutive(self, indices: NDArray[np.int64]) -> list[NDArray[np.int64]]:
        """
        Group consecutive indices into segments.
        """
        if len(indices) == 0:
            return []

        groups = []
        current_group = [indices[0]]

        for i in range(1, len(indices)):
            if indices[i] == indices[i - 1] + 1:
                current_group.append(indices[i])
            else:
                groups.append(np.array(current_group))
                current_group = [indices[i]]

        groups.append(np.array(current_group))
        return groups

    def _zscore_to_severity(self, zscore: float) -> AnomalySeverity:
        """
        Convert z-score to severity level.
        """
        if zscore > 5.0:
            return AnomalySeverity.HIGH
        elif zscore > 4.0:
            return AnomalySeverity.MEDIUM
        else:
            return AnomalySeverity.LOW
