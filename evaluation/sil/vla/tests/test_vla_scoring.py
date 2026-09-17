"""CPU-only geometry, event-ordering, and dynamics contracts for VLA placement scoring."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest
from sil.vla.scoring import consecutive, placement_margins, rotation_matrix, score_gear

_EVIDENCE_WIDTHS = {
    "gear_pose": 7,
    "ee_pose": 7,
    "contact_force": 2,
    "gear_velocity": 6,
    "bin_pose": 7,
    "bin_velocity": 6,
    "placement_margins": 6,
}


@pytest.fixture
def geometry() -> dict[str, Any]:
    return {
        "target_reference_pose": [0, 0, 0, 1, 0, 0, 0],
        "target_reference_bounds": [[-0.1, -0.1, 0], [0.1, 0.1, 0.2]],
        "object_local_corners": list(itertools.product((-0.02, 0.02), (-0.03, 0.03), (-0.01, 0.01))),
        "wall_margin_m": 0.01,
        "floor_tolerance_m": 0.005,
    }


@dataclass
class _Scenario:
    """A measured synthetic trajectory with fixed task thresholds and explicit initial targets."""

    data: dict[str, np.ndarray]
    initial: dict[str, np.ndarray]
    initial_command: np.ndarray
    config: dict[str, Any]
    geometry: dict[str, Any]

    def score(self, *, count: int | None = None) -> dict[str, Any]:
        data = self.data if count is None else {key: value[:count].copy() for key, value in self.data.items()}
        return score_gear(data, self.initial, self.initial_command, self.config)

    def update_geometry(self) -> None:
        self.data["evidence.placement_margins"] = np.array(
            [
                placement_margins(gear, target, self.geometry)
                for gear, target in zip(self.data["evidence.gear_pose"], self.data["evidence.bin_pose"], strict=True)
            ]
        )


@pytest.fixture
def scenario(geometry: dict[str, Any]) -> _Scenario:
    config = {
        "control_hz": 10,
        "contact_force_min_n": 0.1,
        "contact_consecutive_frames": 2,
        "micro_lift_m": 0.02,
        "required_lift_m": 0.1,
        "retention_drift_m": 0.025,
        "bin_settle_seconds": 0.3,
        "max_gear_speed_m_s": 1.5,
        "max_joint_speed_rad_s": 0.5,
        "max_joint_acceleration_rad_s2": 1.0,
        "max_joint_jerk_rad_s3": 10.0,
    }
    initial = {
        "gear_pose": np.array([0.2, 0, 0.02, 1, 0, 0, 0], dtype=np.float64),
        "bin_pose": np.array([0, 0, 0, 1, 0, 0, 0], dtype=np.float64),
    }
    gear = np.tile(initial["gear_pose"], (8, 1))
    gear[:, 0] = [0.2, 0.2, 0.12, 0.06, 0.02, 0.02, 0.02, 0.02]
    gear[:, 2] = [0.02, 0.04, 0.13, 0.13, 0.03, 0.03, 0.03, 0.03]
    ee = gear.copy()
    ee[:, 2] += 0.1
    ee[4:, 0] += 0.4
    velocity = np.zeros((8, 6))
    velocity[:, :3] = np.diff(np.vstack((initial["gear_pose"][:3], gear[:, :3])), axis=0) * config["control_hz"]
    contact = np.zeros((8, 2))
    contact[:4] = 0.2
    data = {
        "actions": np.zeros((8, 7)),
        "evidence.gear_pose": gear,
        "evidence.ee_pose": ee,
        "evidence.contact_force": contact,
        "evidence.gear_velocity": velocity,
        "evidence.bin_pose": np.tile(initial["bin_pose"], (8, 1)),
        "evidence.bin_velocity": np.zeros((8, 6)),
    }
    result = _Scenario(data, initial, np.zeros(7), config, geometry)
    result.update_geometry()
    return result


class TestRigidGeometry:
    """Containment uses WXYZ orientations and the live target's full rigid transform."""

    @pytest.mark.parametrize(
        ("quaternion", "expected"),
        [
            ([1, 0, 0, 0], [[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
            ([3, 0, 0, 3], [[0, -1, 0], [1, 0, 0], [0, 0, 1]]),
            ([-3, 0, 0, -3], [[0, -1, 0], [1, 0, 0], [0, 0, 1]]),
            ([2, 2, 0, 0], [[1, 0, 0], [0, 0, -1], [0, 1, 0]]),
            ([2, 0, 2, 0], [[0, 0, 1], [0, 1, 0], [-1, 0, 0]]),
        ],
    )
    def test_given_wxyz_quaternion_when_normalized_then_rotation_matches_known_basis(
        self,
        quaternion: list[float],
        expected: list[list[float]],
    ) -> None:
        rotation = rotation_matrix(np.asarray(quaternion))

        np.testing.assert_allclose(rotation, expected, atol=1e-14)
        np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-14)
        assert np.linalg.det(rotation) == pytest.approx(1)

    @pytest.mark.parametrize(
        "quaternion",
        [
            pytest.param(np.zeros(4), id="zero-norm"),
            pytest.param(np.array([np.nan, 0, 0, 1]), id="nan"),
            pytest.param(np.array([1, np.inf, 0, 0]), id="infinite"),
            pytest.param(np.ones(3), id="short"),
            pytest.param(np.ones((1, 4)), id="batched"),
            pytest.param(np.ones(4, dtype=bool), id="boolean"),
        ],
    )
    def test_given_invalid_quaternion_when_converted_then_geometry_is_rejected(self, quaternion: np.ndarray) -> None:
        with pytest.raises(ValueError, match="Quaternion"):
            rotation_matrix(quaternion)

    def test_given_authored_corners_when_rotated_then_envelope_not_center_controls_containment(
        self,
        geometry: dict[str, Any],
    ) -> None:
        target = np.array([0, 0, 0, 1, 0, 0, 0])
        rotated = np.array([0, 0, 0.03, np.sqrt(0.5), 0, 0, np.sqrt(0.5)])
        center_inside = np.array([0.085, 0, 0.03, 1, 0, 0, 0])

        margins = placement_margins(rotated, target, geometry)
        protruding = placement_margins(center_inside, target, geometry)

        np.testing.assert_allclose(margins, [0.06, 0.06, 0.07, 0.07, 0.025, 0.17], atol=1e-14)
        assert protruding[1] == pytest.approx(-0.015)

    def test_given_common_rigid_motion_when_target_and_object_move_then_margins_are_invariant(
        self,
        geometry: dict[str, Any],
    ) -> None:
        half = np.sqrt(0.5)
        target = np.array([3, -2, 1, half, 0, 0, half])
        gear = np.array([3, -1.96, 1.03, half, 0, 0, half])

        actual = placement_margins(gear, target, geometry)

        np.testing.assert_allclose(actual, [0.11, 0.03, 0.06, 0.06, 0.025, 0.17], atol=1e-14)

    def test_given_nonidentity_reference_when_live_target_rotates_on_another_axis_then_frames_compose_correctly(
        self,
        geometry: dict[str, Any],
    ) -> None:
        half = np.sqrt(0.5)
        geometry["target_reference_pose"] = [2, -1, 0.4, half, 0, 0, half]
        geometry["target_reference_bounds"] = [[1.9, -1.1, 0.4], [2.1, -0.9, 0.6]]
        live_target = np.array([3, 4, 1, half, half, 0, 0])
        gear = np.array([3.04, 3.97, 1, half, half, 0, 0])

        margins = placement_margins(gear, live_target, geometry)

        np.testing.assert_allclose(margins, [0.06, 0.06, 0.11, 0.03, 0.025, 0.17], atol=1e-14)

    def test_given_target_translation_without_object_motion_when_scored_then_authored_bounds_are_not_reused(
        self,
        geometry: dict[str, Any],
    ) -> None:
        target = np.array([0.4, 0, 0, 1, 0, 0, 0])
        gear = np.array([0.04, 0, 0.03, 1, 0, 0, 0])

        margins = placement_margins(gear, target, geometry)

        np.testing.assert_allclose(margins, [-0.29, 0.43, 0.06, 0.06, 0.025, 0.17], atol=1e-14)

    @pytest.mark.parametrize(("height", "floor_margin", "rim_margin"), [(0.005, 0, 0.195), (0.2, 0.195, 0)])
    def test_given_floor_or_rim_boundary_when_measured_then_declared_tolerances_are_inclusive(
        self,
        height: float,
        floor_margin: float,
        rim_margin: float,
        geometry: dict[str, Any],
    ) -> None:
        margins = placement_margins(np.array([0, 0, height, 1, 0, 0, 0]), np.array([0, 0, 0, 1, 0, 0, 0]), geometry)

        assert margins[4] == pytest.approx(floor_margin)
        assert margins[5] == pytest.approx(rim_margin)

    @pytest.mark.parametrize("pose_name", ["object", "target", "reference"])
    def test_given_zero_quaternion_in_any_geometry_frame_when_transformed_then_it_is_rejected(
        self,
        pose_name: str,
        geometry: dict[str, Any],
    ) -> None:
        gear = np.array([0, 0, 0.03, 1, 0, 0, 0])
        target = np.array([0, 0, 0, 1, 0, 0, 0])
        if pose_name == "reference":
            geometry["target_reference_pose"] = [0] * 7
        elif pose_name == "object":
            gear[3:] = 0
        else:
            target[3:] = 0

        with pytest.raises(ValueError, match="Quaternion"):
            placement_margins(gear, target, geometry)

    @pytest.mark.parametrize(
        ("key", "invalid"),
        [
            ("object_local_corners", np.zeros((7, 3))),
            ("object_local_corners", np.full((8, 3), np.nan)),
            ("target_reference_bounds", np.zeros((2, 3))),
            ("target_reference_bounds", [[1, 1, 1], [0, 0, 0]]),
            ("target_reference_bounds", [[0, 0, 0], [1, 1, np.inf]]),
            ("target_reference_pose", [0, 0, 0, 1, 0, 0]),
            ("wall_margin_m", -0.01),
            ("wall_margin_m", True),
            ("floor_tolerance_m", float("nan")),
            ("floor_tolerance_m", -0.01),
        ],
    )
    def test_given_invalid_authored_geometry_when_measured_then_it_is_rejected(
        self,
        key: str,
        invalid: Any,
        geometry: dict[str, Any],
    ) -> None:
        geometry[key] = invalid

        with pytest.raises(ValueError):
            placement_margins(np.array([0, 0, 0.03, 1, 0, 0, 0]), np.array([0, 0, 0, 1, 0, 0, 0]), geometry)


class TestEventOrdering:
    """Placement requires grasp-qualified lift, release, retained transport, and final dwell."""

    def test_given_ordered_grasp_lift_and_release_when_final_dwell_completes_then_all_gates_pass(
        self,
        scenario: _Scenario,
    ) -> None:
        result = scenario.score()

        assert result["success"] is True and result["placement_success"] is True
        assert result["gates"] == {
            "bilateral_contact": True,
            "micro_lift_retained": True,
            "qualified_lift": True,
            "retention_before_release": True,
            "contact_cleared_after_lift": True,
            "live_bin_final_dwell": True,
            "dynamics": True,
        }
        assert result["required_dwell_frames"] == result["final_dwell_frames"] == 3
        assert result["maximum_lift_m"] == pytest.approx(0.11)
        assert result["maximum_bin_displacement_m"] == 0
        np.testing.assert_allclose(result["final_placement_margins_m"], [0.09, 0.05, 0.06, 0.06, 0.025, 0.17])
        json.dumps(result, allow_nan=False)

    def test_given_lift_before_contact_when_later_contact_occurs_then_lift_is_not_qualified(
        self,
        scenario: _Scenario,
    ) -> None:
        scenario.data["evidence.contact_force"][:2] = 0
        gear = scenario.data["evidence.gear_pose"]
        gear[:4, 2] = scenario.initial["gear_pose"][2] + 0.11
        scenario.data["evidence.ee_pose"][:4, :3] = gear[:4, :3] + [0, 0, 0.1]
        scenario.data["evidence.gear_velocity"][:, :3] = (
            np.diff(np.vstack((scenario.initial["gear_pose"][:3], gear[:, :3])), axis=0) * scenario.config["control_hz"]
        )
        scenario.update_geometry()

        result = scenario.score()

        assert result["gates"]["bilateral_contact"] is True
        assert result["gates"]["qualified_lift"] is False, result
        assert result["success"] is False

    def test_given_ungrasped_ejection_when_object_lands_inside_bin_then_placement_cannot_pass(
        self,
        scenario: _Scenario,
    ) -> None:
        scenario.data["evidence.contact_force"].fill(0)

        result = scenario.score()

        assert result["maximum_lift_m"] >= scenario.config["required_lift_m"]
        assert min(result["final_placement_margins_m"]) >= 0
        assert result["gates"]["bilateral_contact"] is False
        assert result["gates"]["qualified_lift"] is False
        assert result["gates"]["contact_cleared_after_lift"] is False
        assert result["placement_success"] is False and result["success"] is False

    def test_given_alternating_contact_when_no_sustained_bilateral_grasp_exists_then_lift_cannot_qualify(
        self,
        scenario: _Scenario,
    ) -> None:
        scenario.data["evidence.contact_force"][:4] = [[0.2, 0], [0.2, 0.2], [0, 0.2], [0.2, 0.2]]

        result = scenario.score()

        assert result["gates"]["bilateral_contact"] is False
        assert result["gates"]["qualified_lift"] is False and result["success"] is False

    def test_given_contact_at_threshold_when_sustained_then_contact_is_inclusive(self, scenario: _Scenario) -> None:
        scenario.data["evidence.contact_force"][:4] = scenario.config["contact_force_min_n"]

        result = scenario.score()

        assert result["gates"]["bilateral_contact"] is True and result["success"] is True

    @pytest.mark.parametrize("clear_pattern", ["one-sided", "interrupted"])
    def test_given_incomplete_contact_clearing_when_released_then_final_dwell_is_not_credited_early(
        self,
        clear_pattern: str,
        scenario: _Scenario,
    ) -> None:
        if clear_pattern == "one-sided":
            scenario.data["evidence.contact_force"][4:, 0] = 0.2
        else:
            scenario.data["evidence.contact_force"][5] = 0.2

        result = scenario.score()

        assert result["gates"]["qualified_lift"] is True
        assert result["final_dwell_frames"] == (0 if clear_pattern == "one-sided" else 1)
        assert result["gates"]["live_bin_final_dwell"] is False and result["success"] is False

    def test_given_early_complete_dwell_when_final_frame_exits_bin_then_success_is_revoked(
        self,
        scenario: _Scenario,
    ) -> None:
        assert scenario.score()["success"] is True
        for key, values in scenario.data.items():
            scenario.data[key] = np.concatenate((values, values[-1:]))
        scenario.data["evidence.gear_pose"][-1, 0] = 0.08
        scenario.data["evidence.gear_velocity"][-1, 0] = 0.6
        scenario.update_geometry()

        result = scenario.score()

        assert result["gates"]["qualified_lift"] is True and result["gates"]["dynamics"] is True
        assert result["final_dwell_frames"] == 0 and result["gates"]["live_bin_final_dwell"] is False
        assert result["success"] is False

    def test_given_containment_gap_when_object_returns_then_final_dwell_restarts(self, scenario: _Scenario) -> None:
        scenario.data["evidence.gear_pose"][-2, 0] = 0.08
        scenario.update_geometry()

        result = scenario.score()

        assert min(result["final_placement_margins_m"]) >= 0
        assert result["final_dwell_frames"] == 1 and result["required_dwell_frames"] == 3
        assert result["placement_success"] is False

    def test_given_live_bin_translation_when_object_stays_put_then_containment_follows_live_bin(
        self,
        scenario: _Scenario,
    ) -> None:
        scenario.data["evidence.bin_pose"][4:, 0] += 0.3
        scenario.update_geometry()

        result = scenario.score()

        assert result["maximum_bin_displacement_m"] == pytest.approx(0.3)
        assert result["final_dwell_frames"] == 0 and result["gates"]["live_bin_final_dwell"] is False
        assert result["success"] is False

    def test_given_post_release_retreat_when_retention_is_measured_then_free_object_motion_is_excluded(
        self,
        scenario: _Scenario,
    ) -> None:
        relative = scenario.data["evidence.gear_pose"][:, :3] - scenario.data["evidence.ee_pose"][:, :3]
        assert np.linalg.norm(relative[-1] - relative[1]) > scenario.config["retention_drift_m"]

        result = scenario.score()

        assert result["maximum_transport_drift_m"] == pytest.approx(0, abs=1e-14)
        assert result["gates"]["retention_before_release"] is True and result["success"] is True

    def test_given_pre_release_slip_when_transport_is_measured_then_retention_fails(self, scenario: _Scenario) -> None:
        scenario.data["evidence.ee_pose"][3, 0] += 0.05

        result = scenario.score()

        assert result["maximum_transport_drift_m"] == pytest.approx(0.05)
        assert result["gates"]["qualified_lift"] is True and result["gates"]["live_bin_final_dwell"] is True
        assert result["gates"]["retention_before_release"] is False and result["success"] is False

    def test_given_rigid_grasp_rotation_when_scored_then_retention_is_end_effector_local(
        self,
        scenario: _Scenario,
    ) -> None:
        gear = scenario.data["evidence.gear_pose"]
        ee = scenario.data["evidence.ee_pose"]
        ee[3, 3:] = [np.sqrt(0.5), np.sqrt(0.5), 0, 0]
        ee[3, :3] = gear[3, :3] - np.array([0, 0, -0.1]) @ rotation_matrix(ee[3, 3:]).T

        result = scenario.score()

        assert result["maximum_transport_drift_m"] == pytest.approx(0, abs=1e-14)
        assert result["gates"]["retention_before_release"] is True


class TestCommandDynamics:
    """Finite differences include the initial target and never reduce empty higher derivatives."""

    @pytest.mark.parametrize("channel", [0, 6], ids=["arm", "gripper"])
    def test_given_initial_target_jump_when_derivatives_are_computed_then_first_transition_is_included(
        self,
        channel: int,
        scenario: _Scenario,
    ) -> None:
        scenario.initial_command[channel] = 0.02

        result = scenario.score()

        assert result["target_command_derivatives"] == pytest.approx(
            {
                "max_joint_speed_rad_s": 0.2,
                "max_joint_acceleration_rad_s2": 2,
                "max_joint_jerk_rad_s3": 20,
            }
        )
        assert result["placement_success"] is True
        assert result["gates"]["dynamics"] is False and result["success"] is False

    @pytest.mark.parametrize(
        ("count", "speed", "acceleration", "jerk"), [(1, 0.01, 0, 0), (2, 0.03, 0.2, 0), (3, 0.05, 0.2, 0)]
    )
    def test_given_short_trajectory_when_nth_difference_is_empty_then_its_maximum_is_zero(
        self,
        count: int,
        speed: float,
        acceleration: float,
        jerk: float,
        scenario: _Scenario,
    ) -> None:
        scenario.data["actions"][:3, 0] = [0.001, 0.004, 0.009]

        result = scenario.score(count=count)

        assert result["target_command_derivatives"] == pytest.approx(
            {
                "max_joint_speed_rad_s": speed,
                "max_joint_acceleration_rad_s2": acceleration,
                "max_joint_jerk_rad_s3": jerk,
            },
            abs=1e-12,
        )
        if count == 1:
            assert result["target_command_derivatives"]["max_joint_acceleration_rad_s2"] == 0
        if count < 3:
            assert result["target_command_derivatives"]["max_joint_jerk_rad_s3"] == 0
        json.dumps(result, allow_nan=False)

    def test_given_excessive_object_speed_when_placement_succeeds_then_dynamics_still_fails(
        self,
        scenario: _Scenario,
    ) -> None:
        scenario.data["evidence.gear_velocity"][3, 0] = 2

        result = scenario.score()

        assert result["maximum_gear_speed_m_s"] >= 2
        assert result["placement_success"] is True and result["gates"]["dynamics"] is False
        assert result["success"] is False

    @pytest.mark.parametrize("condition", ["success", "no-contact", "no-release", "short"])
    def test_given_valid_or_incomplete_evidence_when_scored_then_all_reported_numbers_are_finite(
        self,
        condition: str,
        scenario: _Scenario,
    ) -> None:
        if condition == "no-contact":
            scenario.data["evidence.contact_force"].fill(0)
        elif condition == "no-release":
            scenario.data["evidence.contact_force"].fill(0.2)

        result = scenario.score(count=1 if condition == "short" else None)

        json.dumps(result, allow_nan=False)
        assert type(result["success"]) is bool and all(type(value) is bool for value in result["gates"].values())
        assert np.isfinite(list(result["target_command_derivatives"].values())).all()
        assert np.isfinite(result["final_placement_margins_m"]).all()

    @pytest.mark.parametrize("field", ["actions", "evidence.gear_velocity", "evidence.bin_pose"])
    def test_given_finite_values_that_overflow_metrics_when_scored_then_infinite_scores_are_never_returned(
        self,
        field: str,
        scenario: _Scenario,
    ) -> None:
        scenario.data[field][-1, 0] = 1e308

        with np.errstate(over="ignore", invalid="ignore"):
            try:
                result = scenario.score()
            except ValueError:
                return

        json.dumps(result, allow_nan=False)


class TestEvidenceValidation:
    """Malformed frame evidence must fail rather than becoming a task success or a nonfinite score."""

    @pytest.mark.parametrize("field", ["actions", "initial_command", *(f"evidence.{key}" for key in _EVIDENCE_WIDTHS)])
    @pytest.mark.parametrize(
        "invalid",
        [np.nan, np.inf, -np.inf],
        ids=["nan", "positive-infinity", "negative-infinity"],
    )
    def test_given_nonfinite_value_in_any_array_when_scored_then_evidence_is_rejected(
        self,
        field: str,
        invalid: float,
        scenario: _Scenario,
    ) -> None:
        values = scenario.initial_command if field == "initial_command" else scenario.data[field]
        values.flat[-1] = invalid

        with pytest.raises(ValueError, match="non-finite"):
            scenario.score()

    @pytest.mark.parametrize("field", ["gear_pose", "bin_pose"])
    @pytest.mark.parametrize("invalid", [np.nan, np.inf], ids=["nan", "infinity"])
    def test_given_nonfinite_initial_pose_when_scored_then_baseline_is_rejected(
        self,
        field: str,
        invalid: float,
        scenario: _Scenario,
    ) -> None:
        scenario.initial[field][2 if field == "gear_pose" else 0] = invalid

        with pytest.raises(ValueError, match="finite"):
            scenario.score()

    @pytest.mark.parametrize("field", ["gear_pose", "ee_pose", "bin_pose"])
    def test_given_zero_pose_quaternion_when_scored_then_invalid_frame_is_rejected(
        self,
        field: str,
        scenario: _Scenario,
    ) -> None:
        scenario.data[f"evidence.{field}"][2, 3:] = 0

        with pytest.raises(ValueError, match=r"(?i)quaternion|pose"):
            scenario.score()

    @pytest.mark.parametrize("field", ["actions", *(f"evidence.{key}" for key in _EVIDENCE_WIDTHS)])
    @pytest.mark.parametrize("kind", ["missing-frame", "wrong-width", "rank-three", "boolean"])
    def test_given_malformed_array_when_scored_then_no_frame_padding_or_coercion_occurs(
        self,
        field: str,
        kind: str,
        scenario: _Scenario,
    ) -> None:
        values = scenario.data[field]
        if kind == "missing-frame":
            scenario.data[field] = values[:-1]
        elif kind == "wrong-width":
            scenario.data[field] = values[:, :-1]
        elif kind == "rank-three":
            scenario.data[field] = values[:, None, :]
        else:
            scenario.data[field] = values.astype(bool)

        with pytest.raises(ValueError, match="numeric shape"):
            scenario.score()

    @pytest.mark.parametrize("field", list(_EVIDENCE_WIDTHS))
    def test_given_missing_evidence_when_scored_then_missing_measurements_are_not_synthesized(
        self,
        field: str,
        scenario: _Scenario,
    ) -> None:
        scenario.data.pop(f"evidence.{field}")

        with pytest.raises((KeyError, ValueError)):
            scenario.score()

    def test_given_negative_contact_magnitude_when_scored_then_evidence_is_rejected(self, scenario: _Scenario) -> None:
        scenario.data["evidence.contact_force"][0, 0] = -0.1

        with pytest.raises(ValueError, match="cannot be negative"):
            scenario.score()

    def test_given_no_steps_when_scored_then_empty_trajectory_is_rejected(self, scenario: _Scenario) -> None:
        with pytest.raises(ValueError, match="empty trajectory"):
            scenario.score(count=0)


class TestConsecutiveFrames:
    """Consecutive counts describe the dwell ending at each individual frame."""

    @pytest.mark.parametrize(
        ("flags", "expected"),
        [
            ([], []),
            ([False, False], [0, 0]),
            ([True, True, True], [1, 2, 3]),
            ([False, True, True, False, True], [0, 1, 2, 0, 1]),
        ],
    )
    def test_given_frame_flags_when_counted_then_false_frames_reset_the_running_dwell(
        self,
        flags: list[bool],
        expected: list[int],
    ) -> None:
        np.testing.assert_array_equal(consecutive(np.asarray(flags, dtype=bool)), expected)

    @pytest.mark.parametrize("flags", [np.array(True), np.ones((2, 2), dtype=bool)])
    def test_given_nonvector_flags_when_counted_then_frame_axis_is_rejected(self, flags: np.ndarray) -> None:
        with pytest.raises(ValueError, match="one-dimensional"):
            consecutive(flags)
