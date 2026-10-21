# cspell:ignore prec
"""
Trajectory quality analysis service.

Computes automatic quality metrics from trajectory data using NumPy and SciPy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from .trajectory_numerics import (
    MetricArray,
    decimal_array,
    decimal_row_norms,
    differentiate,
    float_row_norms,
    row_norms,
    time_deltas,
)

SmoothnessMode = Literal["log-scaled", "radian-based"]
SMOOTHNESS_MODES: tuple[SmoothnessMode, ...] = ("log-scaled", "radian-based")
DEFAULT_SMOOTHNESS_MODE: SmoothnessMode = "log-scaled"

_MIN_PATH_LENGTH = Decimal("0.000001")


def _derivatives(positions: NDArray[np.float64], timestamps: NDArray[np.float64]) -> tuple[MetricArray, MetricArray]:
    """Compute velocity and jerk with overflow-safe numerical primitives."""
    dt = time_deltas(timestamps)
    velocity = differentiate(positions, dt)
    acceleration = differentiate(velocity, dt[1:])
    return velocity, differentiate(acceleration, dt[2:])


def _path_efficiency(direct_distance: float | Decimal, path_length: float | Decimal) -> float:
    if path_length < _MIN_PATH_LENGTH:
        return 1.0
    return float(min(1, max(0, direct_distance / path_length)))


def _decimal_path_efficiency(positions: NDArray[np.float64]) -> float:
    with localcontext() as context:
        context.prec = 34
        decimal_positions = decimal_array(positions)
        path_segments = np.diff(decimal_positions, axis=0)
        displacement = decimal_positions[-1:] - decimal_positions[:1]
    direct_distance = decimal_row_norms(displacement)[0]
    path_length = sum(decimal_row_norms(path_segments))
    return _path_efficiency(direct_distance, path_length)


def _rms(values: MetricArray) -> Decimal:
    scale = max(abs(value) for value in values.flat)
    if scale == 0:
        return Decimal(0)
    scaled = np.asarray(values / scale, dtype=np.float64)
    return Decimal(scale) * Decimal(float(np.sqrt(np.mean(scaled**2))))


@dataclass
class TrajectoryMetrics:
    """Computed trajectory quality metrics."""

    smoothness: float
    """Jerk minimization score (0-1, higher is smoother)."""

    normalized_smoothness: float
    """Rescaled smoothness (0-1) that discriminates across degree-scale
    trajectories where raw ``smoothness`` saturates near zero."""

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
        smoothness_mode: SmoothnessMode = DEFAULT_SMOOTHNESS_MODE,
    ) -> None:
        """
        Initialize the analyzer.

        Args:
            velocity_threshold: Velocity below this is considered stopped.
            hesitation_min_frames: Minimum frames to count as hesitation.
            jitter_frequency_threshold: Frequency above this indicates jitter.
            smoothness_mode: Normalization for ``normalized_smoothness`` —
                ``log-scaled`` (default) or ``radian-based``.
        """
        if smoothness_mode not in SMOOTHNESS_MODES:
            raise ValueError(f"smoothness_mode must be one of {SMOOTHNESS_MODES}, got {smoothness_mode!r}")
        self.velocity_threshold = velocity_threshold
        self.hesitation_min_frames = hesitation_min_frames
        self.jitter_frequency_threshold = jitter_frequency_threshold
        self.smoothness_mode = smoothness_mode

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
                normalized_smoothness=1.0,
                efficiency=1.0,
                jitter=0.0,
                hesitation_count=0,
                correction_count=0,
                overall_score=3,
                flags=[],
            )

        velocity, jerk = _derivatives(positions, timestamps)

        # Compute metrics
        smoothness = self._compute_smoothness(jerk)
        normalized_smoothness = self._compute_normalized_smoothness(jerk)
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
            normalized_smoothness=normalized_smoothness,
            efficiency=efficiency,
            jitter=jitter,
            hesitation_count=hesitation_count,
            correction_count=correction_count,
            overall_score=overall_score,
            flags=flags,
        )

    def _compute_smoothness(self, jerk: MetricArray) -> float:
        """
        Compute smoothness from jerk (third derivative).

        Lower jerk = higher smoothness score.
        """
        if len(jerk) == 0:
            return 1.0

        # RMS jerk
        rms_jerk = _rms(jerk)

        # Normalize to 0-1 scale (assuming typical jerk range)
        # Using sigmoid-like transformation
        return float(1 / (1 + rms_jerk))

    def _compute_normalized_smoothness(self, jerk: MetricArray) -> float:
        """
        Compute a rescaled smoothness that discriminates across episodes.

        Raw ``smoothness`` (``1 / (1 + rms_jerk)``) saturates near zero for real
        degree-scale joint trajectories because jerk divides by ``dt**3``. Two
        normalization methods are offered via ``smoothness_mode``:

        * ``log-scaled`` (default): ``1 / (1 + log10(1 + rms_jerk))`` compresses
          the wide jerk dynamic range so scores spread across (0, 1].
        * ``radian-based``: assumes input positions are in degrees (as robot
          joint encoders report) and converts jerk to physical radian units
          before the reciprocal ``1 / (1 + rms_jerk_rad)``.
        """
        if len(jerk) == 0:
            return 1.0

        rms_jerk = _rms(jerk)
        if self.smoothness_mode == "radian-based":
            rms_jerk_rad = rms_jerk * Decimal(np.pi / 180.0)
            normalized = 1 / (1 + rms_jerk_rad)
        else:  # log-scaled
            normalized = 1 / (1 + (1 + rms_jerk).log10())
        return float(normalized)

    def _compute_efficiency(self, positions: NDArray[np.float64]) -> float:
        """
        Compute path efficiency as ratio of direct distance to actual path length.
        """
        if len(positions) < 2:
            return 1.0

        # Subtract before scaling norms so constant offsets cannot erase small movements.
        with np.errstate(over="ignore", invalid="ignore"):
            path_segments = np.diff(positions, axis=0)
            displacement = positions[-1:] - positions[:1]
            direct_norms = float_row_norms(displacement)
            segment_norms = float_row_norms(path_segments)
            if direct_norms is not None and segment_norms is not None:
                path_length = float(np.sum(segment_norms))
                if np.isfinite(path_length):
                    return _path_efficiency(float(direct_norms[0]), path_length)
        return _decimal_path_efficiency(positions)

    def _compute_jitter(
        self,
        velocity: MetricArray,
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
            scale = max(1, max(abs(value) for value in velocity.flat))
            vel_magnitude = np.linalg.norm(np.asarray(velocity / scale, dtype=np.float64), axis=1)

            # FFT of velocity
            n = len(vel_magnitude)
            freq_spectrum = np.abs(scipy_fft.fft(vel_magnitude))[: n // 2]

            # Estimate sample rate
            avg_dt = float((Decimal(float(timestamps[len(velocity)])) - Decimal(float(timestamps[0]))) / len(velocity))
            if avg_dt <= 0:
                avg_dt = 1.0 / 30.0  # Default 30 FPS

            frequencies = scipy_fft.fftfreq(n, avg_dt)[: n // 2]

            # High frequency power (above threshold)
            high_freq_mask = np.abs(frequencies) > self.jitter_frequency_threshold
            high_freq_power = np.sum(freq_spectrum[high_freq_mask] ** 2)
            total_power = np.sum(freq_spectrum**2)

            if total_power == 0 or np.sqrt(total_power) < float(Decimal("0.00001") / Decimal(scale)):
                return 0.0

            jitter = high_freq_power / total_power
            return float(np.clip(jitter, 0.0, 1.0))

        except ImportError:
            # Fallback without scipy
            return 0.0

    def _count_hesitations(self, velocity: MetricArray) -> int:
        """
        Count segments where velocity was near zero.
        """
        vel_magnitude = row_norms(velocity)
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

    def _count_corrections(self, velocity: MetricArray) -> int:
        """
        Count direction reversal events.
        """
        if len(velocity) < 2:
            return 0

        # Compute velocity direction changes
        norms = row_norms(velocity)
        if norms.dtype == object:
            vel_normalized = np.asarray(
                decimal_array(velocity) / (norms[:, np.newaxis] + Decimal("1e-10")), dtype=np.float64
            )
        else:
            vel_normalized = velocity / (norms[:, np.newaxis] + 1e-10)

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
