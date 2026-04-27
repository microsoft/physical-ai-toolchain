"""Property-based tests for robot_types shape validation."""

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra.numpy import array_shapes, arrays

from evaluation.sil.robot_types import JointPositionCommand, RobotObservation

joint_array = arrays(
    dtype=np.float64,
    shape=(6,),
    elements=st.floats(-2 * np.pi, 2 * np.pi, allow_nan=False, allow_infinity=False),
)


@given(positions=joint_array)
def test_joint_positions_valid_shape(positions: np.ndarray) -> None:
    """Valid (6,) arrays are accepted by both dataclasses."""
    obs = RobotObservation(joint_positions=positions)
    assert obs.joint_positions.shape == (6,)

    cmd = JointPositionCommand(positions=positions)
    assert cmd.positions.shape == (6,)


@given(
    data=array_shapes(min_dims=1, max_dims=3, min_side=1, max_side=10)
    .filter(lambda s: s != (6,))
    .flatmap(
        lambda s: arrays(
            dtype=np.float64,
            shape=s,
            elements=st.floats(-1e3, 1e3, allow_nan=False, allow_infinity=False),
        )
    )
)
def test_joint_positions_invalid_shape_rejected(data: np.ndarray) -> None:
    """Non-(6,) arrays are rejected with ValueError."""
    with pytest.raises(ValueError, match="joint_positions shape must be"):
        RobotObservation(joint_positions=data)

    with pytest.raises(ValueError, match="positions shape must be"):
        JointPositionCommand(positions=data)


@given(delta=joint_array, current=joint_array)
def test_as_absolute_additivity(delta: np.ndarray, current: np.ndarray) -> None:
    """as_absolute returns current + delta."""
    cmd = JointPositionCommand(positions=delta)
    result = cmd.as_absolute(current)
    np.testing.assert_allclose(result.positions, current + delta)


@given(
    delta=joint_array,
    current=joint_array,
    timestamp=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
)
def test_as_absolute_preserves_timestamp(delta: np.ndarray, current: np.ndarray, timestamp: float) -> None:
    """Timestamp is preserved through as_absolute."""
    cmd = JointPositionCommand(positions=delta, timestamp_s=timestamp)
    result = cmd.as_absolute(current)
    assert result.timestamp_s == cmd.timestamp_s


@given(positions=joint_array)
def test_none_image_always_valid(positions: np.ndarray) -> None:
    """color_image=None is accepted with any valid joint array."""
    obs = RobotObservation(joint_positions=positions, color_image=None)
    assert obs.color_image is None
