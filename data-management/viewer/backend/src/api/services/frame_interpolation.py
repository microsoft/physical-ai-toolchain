"""
Frame interpolation service for generating synthetic frames.

Provides functions to interpolate trajectory data and images between
adjacent frames for frame insertion operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from ..models.datasources import TrajectoryPoint

__all__ = [
    "interpolate_frame_data",
    "interpolate_image",
    "interpolate_trajectory",
]


def interpolate_trajectory(
    point1: TrajectoryPoint,
    point2: TrajectoryPoint,
    t: float = 0.5,
) -> TrajectoryPoint:
    """Linearly interpolate between two trajectory points.

    Args:
        point1: First trajectory point (t=0).
        point2: Second trajectory point (t=1).
        t: Interpolation factor, 0.0 to 1.0. Default 0.5 for midpoint.

    Returns:
        Interpolated TrajectoryPoint with averaged values.
    """
    from ..models.datasources import TrajectoryPoint

    return TrajectoryPoint(
        timestamp=point1.timestamp + t * (point2.timestamp - point1.timestamp),
        frame=int((point1.frame + point2.frame) / 2),
        joint_positions=[
            p1 + t * (p2 - p1) for p1, p2 in zip(point1.joint_positions, point2.joint_positions, strict=True)
        ],
        joint_velocities=[
            v1 + t * (v2 - v1) for v1, v2 in zip(point1.joint_velocities, point2.joint_velocities, strict=True)
        ],
        end_effector_pose=[
            e1 + t * (e2 - e1) for e1, e2 in zip(point1.end_effector_pose, point2.end_effector_pose, strict=True)
        ],
        gripper_state=point1.gripper_state if t < 0.5 else point2.gripper_state,
    )


def interpolate_image(
    image1: NDArray[np.uint8],
    image2: NDArray[np.uint8],
    t: float = 0.5,
) -> NDArray[np.uint8]:
    """Pixel-wise linear interpolation between two images.

    Args:
        image1: First image array, shape (H, W, C), dtype uint8.
        image2: Second image array, shape (H, W, C), dtype uint8.
        t: Interpolation factor, 0.0 to 1.0. Default 0.5 for midpoint.

    Returns:
        Interpolated image as uint8 array with same shape.

    Raises:
        ValueError: If image shapes do not match.
    """
    if image1.shape != image2.shape:
        msg = f"Image shapes must match: {image1.shape} vs {image2.shape}"
        raise ValueError(msg)

    # Use float64 for precision during blending
    result = (1 - t) * image1.astype(np.float64) + t * image2.astype(np.float64)
    return np.clip(result, 0, 255).astype(np.uint8)


def interpolate_frame_data(
    data: NDArray,
    after_index: int,
    t: float = 0.5,
) -> NDArray:
    """Interpolate array data between two adjacent frames.

    Args:
        data: Array with shape (N, ...) where N is frame count.
        after_index: Index after which to compute interpolation.
        t: Interpolation factor, 0.0 to 1.0.

    Returns:
        Interpolated row with shape (...) matching data[0].shape.

    Raises:
        IndexError: If after_index is out of valid range.
    """
    if after_index < 0 or after_index >= len(data) - 1:
        msg = f"after_index {after_index} out of range for array length {len(data)}"
        raise IndexError(msg)

    frame1 = data[after_index]
    frame2 = data[after_index + 1]

    if np.issubdtype(data.dtype, np.integer):
        # For integer types, blend then round
        result = (1 - t) * frame1.astype(np.float64) + t * frame2.astype(np.float64)
        return np.round(result).astype(data.dtype)
    else:
        # For float types, direct interpolation
        return (1 - t) * frame1 + t * frame2
