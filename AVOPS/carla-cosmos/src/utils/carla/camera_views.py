"""Canonical CARLA/Cosmos camera view definitions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class CameraView:
    """One supported camera/view in the CARLA to Cosmos pipeline."""

    key: str
    cosmos_name: str
    inference_key: str
    control_name: str
    yaw_deg: float
    fov_deg: float
    location_xyz: tuple[float, float, float]
    pitch_deg: float = 0.0
    roll_deg: float = 0.0

    @property
    def rig_name(self) -> str:
        return self.cosmos_name.replace("_", ":")


VIEW_ORDER: tuple[str, ...] = (
    "front_wide",
    "front_tele",
    "cross_left",
    "cross_right",
    "rear_left",
    "rear_right",
    "rear",
)

COSMOS_MULTIVIEW_ORDER: tuple[str, ...] = (
    "front_wide",
    "cross_right",
    "rear_right",
    "rear",
    "rear_left",
    "cross_left",
    "front_tele",
)

CAMERA_VIEWS: dict[str, CameraView] = {
    "front_wide": CameraView(
        key="front_wide",
        cosmos_name="camera_front_wide_120fov",
        inference_key="front_wide",
        control_name="front_wide_control.mp4",
        yaw_deg=0.0,
        fov_deg=120.0,
        location_xyz=(1.70, 0.00, 1.55),
    ),
    "front_tele": CameraView(
        key="front_tele",
        cosmos_name="camera_front_tele_30fov",
        inference_key="front_tele",
        control_name="front_tele_control.mp4",
        yaw_deg=0.0,
        fov_deg=30.0,
        location_xyz=(1.75, 0.00, 1.60),
    ),
    "cross_left": CameraView(
        key="cross_left",
        cosmos_name="camera_cross_left_120fov",
        inference_key="cross_left",
        control_name="cross_left_control.mp4",
        yaw_deg=-60.0,
        fov_deg=120.0,
        location_xyz=(1.55, -0.35, 1.55),
    ),
    "cross_right": CameraView(
        key="cross_right",
        cosmos_name="camera_cross_right_120fov",
        inference_key="cross_right",
        control_name="cross_right_control.mp4",
        yaw_deg=60.0,
        fov_deg=120.0,
        location_xyz=(1.55, 0.35, 1.55),
    ),
    "rear_left": CameraView(
        key="rear_left",
        cosmos_name="camera_rear_left_70fov",
        inference_key="rear_left",
        control_name="rear_left_control.mp4",
        yaw_deg=-135.0,
        fov_deg=70.0,
        location_xyz=(-0.75, -0.35, 1.50),
    ),
    "rear_right": CameraView(
        key="rear_right",
        cosmos_name="camera_rear_right_70fov",
        inference_key="rear_right",
        control_name="rear_right_control.mp4",
        yaw_deg=135.0,
        fov_deg=70.0,
        location_xyz=(-0.75, 0.35, 1.50),
    ),
    "rear": CameraView(
        key="rear",
        cosmos_name="camera_rear_tele_30fov",
        inference_key="rear",
        control_name="rear_control.mp4",
        yaw_deg=180.0,
        fov_deg=30.0,
        location_xyz=(-0.90, 0.00, 1.50),
    ),
}

COSMOS_NAME_TO_VIEW_KEY = {view.cosmos_name: key for key, view in CAMERA_VIEWS.items()}


def resolve_views(values: Iterable[str] | None, *, available: Iterable[str] | None = None) -> list[str]:
    """Resolve CLI view tokens to canonical keys in pipeline order."""
    available_set = set(available) if available is not None else set(VIEW_ORDER)
    raw_values = list(values or ["all"])
    if not raw_values or any(value.lower() == "all" for value in raw_values):
        selected = [key for key in VIEW_ORDER if key in available_set]
    else:
        selected = []
        for value in raw_values:
            key = value.strip()
            if key in COSMOS_NAME_TO_VIEW_KEY:
                key = COSMOS_NAME_TO_VIEW_KEY[key]
            if key not in CAMERA_VIEWS:
                raise ValueError(f"Unknown view '{value}'. Use one or more of: all, {', '.join(VIEW_ORDER)}")
            if key not in available_set:
                raise ValueError(f"View '{key}' is not available. Available views: {', '.join(sorted(available_set))}")
            if key not in selected:
                selected.append(key)

    if not selected:
        raise ValueError("At least one camera view must be selected.")
    return selected


def views_to_cosmos_names(view_keys: Iterable[str]) -> list[str]:
    return [CAMERA_VIEWS[key].cosmos_name for key in view_keys]


def views_in_cosmos_order(view_keys: Iterable[str]) -> list[str]:
    selected = set(view_keys)
    return [key for key in COSMOS_MULTIVIEW_ORDER if key in selected]
