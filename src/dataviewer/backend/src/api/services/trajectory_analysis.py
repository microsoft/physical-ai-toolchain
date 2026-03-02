"""
Trajectory quality analysis service.

Computes automatic quality metrics from trajectory data using NumPy and SciPy.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass
class TrajectoryMetrics:
    """Computed trajectory quality metrics."""

    smoothness: float
    """Jerk minimization score (0-1, higher is smoother)."""

    efficiency: float
    """Path length ratio vs direct path (0-1, higher is more efficient)."""

    jitter: float
    """High-frequency oscillation amplitude (lower is better)."""

    hesitation_count: int
    """Number of segments where velocity was near zero."""

    correction_count: int
    """Number of direction reversal events."""

    overall_score: int
    """Suggested overall quality score (1-5)."""

    flags: list[str]
    """Detected quality flags."""


class TrajectoryAnalyzer:
    """
    Analyzes robot trajectories to compute quality metrics.

    Uses numerical differentiation and frequency analysis to detect
    issues like jitter, hesitation, and inefficient paths.

    Example:
        >>> analyzer = TrajectoryAnalyzer()
        >>> positions = np.array([[0, 0, 0], [1, 1, 1], [2, 2, 2]])
        >>> timestamps = np.array([0.0, 0.033, 0.066])
        >>> metrics = analyzer.analyze(positions, timestamps)
        >>> print(f"Smoothness: {metrics.smoothness:.2f}")
    """

    def __init__(
        self,
        velocity_threshold: float = 0.01,
        hesitation_min_frames: int = 5,
        jitter_frequency_threshold: float = 10.0,
    ) -> None:
        """
        Initialize the analyzer.

        Args:
            velocity_threshold: Velocity below this is considered stopped.
            hesitation_min_frames: Minimum frames to count as hesitation.
            jitter_frequency_threshold: Frequency above this indicates jitter.
        """
        self.velocity_threshold = velocity_threshold
        self.hesitation_min_frames = hesitation_min_frames
        self.jitter_frequency_threshold = jitter_frequency_threshold

    def analyze(
        self,
        positions: NDArray[np.float64],
        timestamps: NDArray[np.float64],
        gripper_states: NDArray[np.float64] | None = None,
    ) -> TrajectoryMetrics:
        """
        Analyze a trajectory and compute quality metrics.

        Args:
            positions: Joint positions array of shape (N, num_joints).
            timestamps: Timestamp array of shape (N,).
            gripper_states: Optional gripper state array of shape (N,).

        Returns:
            TrajectoryMetrics with computed values.
        """
        if len(positions) < 3:
            return TrajectoryMetrics(
                smoothness=1.0,
                efficiency=1.0,
                jitter=0.0,
                hesitation_count=0,
                correction_count=0,
                overall_score=3,
                flags=[],
            )

        # Compute time deltas
        dt = np.diff(timestamps)
        dt = np.where(dt > 0, dt, 1e-6)  # Avoid division by zero

        # Compute derivatives
        velocity = np.diff(positions, axis=0) / dt[:, np.newaxis]
        acceleration = np.diff(velocity, axis=0) / dt[1:, np.newaxis]
        jerk = np.diff(acceleration, axis=0) / dt[2:, np.newaxis]

        # Compute metrics
        smoothness = self._compute_smoothness(jerk)
        efficiency = self._compute_efficiency(positions)
        jitter = self._compute_jitter(velocity, timestamps)
        hesitation_count = self._count_hesitations(velocity)
        correction_count = self._count_corrections(velocity)

        # Determine flags
        flags = self._determine_flags(smoothness, jitter, hesitation_count, correction_count)

        # Compute overall score
        overall_score = self._compute_overall_score(smoothness, efficiency, jitter, hesitation_count, correction_count)

        return TrajectoryMetrics(
            smoothness=smoothness,
            efficiency=efficiency,
            jitter=jitter,
            hesitation_count=hesitation_count,
            correction_count=correction_count,
            overall_score=overall_score,
            flags=flags,
        )

    def _compute_smoothness(self, jerk: NDArray[np.float64]) -> float:
        """
        Compute smoothness from jerk (third derivative).

        Lower jerk = higher smoothness score.
        """
        if len(jerk) == 0:
            return 1.0

        # RMS jerk
        rms_jerk = np.sqrt(np.mean(jerk**2))

        # Normalize to 0-1 scale (assuming typical jerk range)
        # Using sigmoid-like transformation
        smoothness = 1.0 / (1.0 + rms_jerk)
        return float(np.clip(smoothness, 0.0, 1.0))

    def _compute_efficiency(self, positions: NDArray[np.float64]) -> float:
        """
        Compute path efficiency as ratio of direct distance to actual path length.
        """
        if len(positions) < 2:
            return 1.0

        # Direct distance from start to end
        direct_distance = np.linalg.norm(positions[-1] - positions[0])

        # Actual path length
        path_segments = np.diff(positions, axis=0)
        path_length = np.sum(np.linalg.norm(path_segments, axis=1))

        if path_length < 1e-6:
            return 1.0

        efficiency = direct_distance / path_length
        return float(np.clip(efficiency, 0.0, 1.0))

    def _compute_jitter(
        self,
        velocity: NDArray[np.float64],
        timestamps: NDArray[np.float64],
    ) -> float:
        """
        Compute jitter as high-frequency oscillation amplitude.

        Uses FFT to detect high-frequency components.
        """
        if len(velocity) < 10:
            return 0.0

        try:
            # Use scipy if available, otherwise basic numpy
            from scipy import fft as scipy_fft

            # Compute velocity magnitude
            vel_magnitude = np.linalg.norm(velocity, axis=1)

            # FFT of velocity
            n = len(vel_magnitude)
            freq_spectrum = np.abs(scipy_fft.fft(vel_magnitude))[: n // 2]

            # Estimate sample rate
            avg_dt = np.mean(np.diff(timestamps[: len(velocity) + 1]))
            if avg_dt <= 0:
                avg_dt = 1.0 / 30.0  # Default 30 FPS

            frequencies = scipy_fft.fftfreq(n, avg_dt)[: n // 2]

            # High frequency power (above threshold)
            high_freq_mask = np.abs(frequencies) > self.jitter_frequency_threshold
            high_freq_power = np.sum(freq_spectrum[high_freq_mask] ** 2)
            total_power = np.sum(freq_spectrum**2)

            if total_power < 1e-10:
                return 0.0

            jitter = high_freq_power / total_power
            return float(np.clip(jitter, 0.0, 1.0))

        except ImportError:
            # Fallback without scipy
            return 0.0

    def _count_hesitations(self, velocity: NDArray[np.float64]) -> int:
        """
        Count segments where velocity was near zero.
        """
        vel_magnitude = np.linalg.norm(velocity, axis=1)
        is_stopped = vel_magnitude < self.velocity_threshold

        # Find consecutive stopped segments
        hesitation_count = 0
        consecutive = 0

        for stopped in is_stopped.tolist():
            if stopped:
                consecutive += 1
            else:
                if consecutive >= self.hesitation_min_frames:
                    hesitation_count += 1
                consecutive = 0

        # Check final segment
        if consecutive >= self.hesitation_min_frames:
            hesitation_count += 1

        return hesitation_count

    def _count_corrections(self, velocity: NDArray[np.float64]) -> int:
        """
        Count direction reversal events.
        """
        if len(velocity) < 2:
            return 0

        # Compute velocity direction changes
        vel_normalized = velocity / (np.linalg.norm(velocity, axis=1, keepdims=True) + 1e-10)

        # Dot product between consecutive velocity directions
        dot_products = np.sum(vel_normalized[:-1] * vel_normalized[1:], axis=1)

        # Count significant reversals (dot product < 0)
        corrections = np.sum(dot_products < -0.5)
        return int(corrections)

    def _determine_flags(
        self,
        smoothness: float,
        jitter: float,
        hesitation_count: int,
        correction_count: int,
    ) -> list[str]:
        """
        Determine quality flags based on metrics.
        """
        flags = []

        if smoothness < 0.5:
            flags.append("jittery")

        if jitter > 0.3:
            flags.append("high_frequency_noise")

        if hesitation_count > 2:
            flags.append("hesitant")

        if correction_count > 5:
            flags.append("excessive_corrections")

        return flags

    def _compute_overall_score(
        self,
        smoothness: float,
        efficiency: float,
        jitter: float,
        hesitation_count: int,
        correction_count: int,
    ) -> int:
        """
        Compute overall quality score (1-5) from metrics.
        """
        # Weighted combination
        score = (
            smoothness * 0.3
            + efficiency * 0.3
            + (1.0 - jitter) * 0.2
            + max(0, 1.0 - hesitation_count * 0.1) * 0.1
            + max(0, 1.0 - correction_count * 0.05) * 0.1
        )

        # Map to 1-5 scale
        if score >= 0.9:
            return 5
        elif score >= 0.7:
            return 4
        elif score >= 0.5:
            return 3
        elif score >= 0.3:
            return 2
        else:
            return 1
