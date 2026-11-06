#!/usr/bin/env python3
"""Dependency-light smoke checks for the three-stage pipeline helpers."""

from __future__ import annotations

import json
import tarfile
import tempfile
from pathlib import Path

from ..utils.carla.camera_views import CAMERA_VIEWS, resolve_views
from ..utils.cosmos.cosmos_patches import (
    QHULL_SENTINEL,
    TRAFFIC_LIGHT_STATE_SENTINEL,
    apply_clipgt_traffic_light_state_patch,
    apply_robust_triangulation_patch,
)
from ..utils.cosmos.cosmos_rdshq import write_rdshq_annotations
from ..utils.cosmos.gpu_utils import choose_context_parallel_size
from ..utils.cosmos.multiview_spec import build_multiview_spec, write_prompt_and_spec
from ..utils.cosmos.scene_rows import stabilize_static_obstacle_rows
from ..utils.pipeline_io import capture_dir, rgb_dir
from .prepare_cosmos_inputs import copy_rgb_videos
from .run_cosmos_inference import active_views_from_spec, subset_spec


def main() -> None:
    assert resolve_views(["all"]) == [
        "front_wide",
        "front_tele",
        "cross_left",
        "cross_right",
        "rear_left",
        "rear_right",
        "rear",
    ]
    assert resolve_views(["camera_front_wide_120fov"]) == ["front_wide"]
    assert choose_context_parallel_size(1) == 1
    assert choose_context_parallel_size(2) == 2
    assert choose_context_parallel_size(4) == 4
    assert choose_context_parallel_size(6) == 8
    assert choose_context_parallel_size(7) == 8
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        spec = build_multiview_spec(
            clip_id="carla_test",
            view_keys=["front_wide", "rear"],
            control_subdir="control_10fps",
            fps=10,
            guidance=3,
            control_weight=1.0,
            num_steps=35,
            seed=2026,
            num_chunks=2,
            chunk_overlap=1,
            save_combined_views=False,
        )
        _, spec_path = write_prompt_and_spec(out, "prompt", spec)
        assert spec_path.exists()
        assert spec["fps"] == 10
        assert spec["front_wide"]["control_path"] == "control_10fps/front_wide_control.mp4"
        assert "rear" in spec
        assert active_views_from_spec(spec) == ["front_wide", "rear"]
        subset = subset_spec(spec, ["front_wide"])
        assert "front_wide" in subset and "rear" not in subset
        assert CAMERA_VIEWS["rear"].control_name == "rear_control.mp4"
        bundle_rgb = capture_dir(out, "carla_test") / "rgb_videos"
        bundle_rgb.mkdir(parents=True)
        (bundle_rgb / "camera_front_wide_120fov.mp4").write_bytes(b"not-a-real-video")
        copy_rgb_videos(
            out,
            "carla_test",
            {"rgb_dir": "/old/machine/path/that/should/not/be/used"},
            ["front_wide"],
        )
        assert (rgb_dir(out, "carla_test") / "camera_front_wide_120fov.mp4").read_bytes() == b"not-a-real-video"
        rdshq_root = out / "rdshq"
        rows_by_element = {
            "egomotion_estimate": [
                {
                    "key": {"clip_id": "carla_test", "timestamp_micros": 0},
                    "egomotion_estimate": {
                        "location": {"x": 0.0, "y": 0.0, "z": 0.0},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                    },
                },
                {
                    "key": {"clip_id": "carla_test", "timestamp_micros": 100000},
                    "egomotion_estimate": {
                        "location": {"x": 1.0, "y": 0.0, "z": 0.0},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                    },
                },
            ],
            "obstacle": [
                {
                    "key": {"clip_id": "carla_test", "timestamp_micros": 0},
                    "obstacle": {
                        "trackline_id": "static_vehicle:1",
                        "center": {"x": 5.0, "y": 0.0, "z": 0.8},
                        "size": {"x": 4.0, "y": 2.0, "z": 1.6},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        "category": "automobile",
                    },
                },
                {
                    "key": {"clip_id": "carla_test", "timestamp_micros": 100000},
                    "obstacle": {
                        "trackline_id": "static_vehicle:1",
                        "center": {"x": 5.0, "y": 0.0, "z": 0.8},
                        "size": {"x": 4.0, "y": 2.0, "z": 1.6},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        "category": "automobile",
                    },
                },
            ],
            "traffic_light": [
                {
                    "key": {
                        "clip_id": "carla_test",
                        "label_class_id": "carla",
                        "map_id": "TownTest",
                        "map_id_version": "1",
                    },
                    "traffic_light": {
                        "center": {"x": 3.0, "y": 1.0, "z": 4.0},
                        "dimensions": {"x": 0.6, "y": 0.35, "z": 0.9},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        "category": "traffic_light",
                        "actor_id": 12,
                        "feature_id": "carla_traffic_light_12_0",
                        "state_sequence": ["GREEN", "YELLOW"],
                    },
                    "version": 1,
                }
            ],
            "lane_line": [],
        }
        stabilization = stabilize_static_obstacle_rows(rows_by_element, static_threshold_m=0.2)
        assert stabilization["static_tracks"] == 1
        assert stabilization["rows_stabilized"] == 2
        assert rows_by_element["obstacle"][0]["obstacle"]["center"]["x"] == 5.0
        write_rdshq_annotations(
            rdshq_root,
            "carla_test",
            rows_by_element,
            {
                "cameras": [
                    {
                        "view_key": "front_wide",
                        "cosmos_name": "camera_front_wide_120fov",
                        "location_xyz": [1.7, 0.0, 1.55],
                        "pitch_deg": 0.0,
                        "yaw_deg": 0.0,
                        "roll_deg": 0.0,
                        "fov_deg": 120.0,
                    }
                ]
            },
            ["front_wide"],
            annotation_fps=10,
            width=1280,
            height=720,
        )
        with tarfile.open(rdshq_root / "vehicle_pose" / "carla_test.tar") as archive:
            assert "sample.000003.vehicle_pose.npy" in archive.getnames()
        with tarfile.open(rdshq_root / "all_object_info" / "carla_test.tar") as archive:
            member = archive.extractfile("sample.000000.all_object_info.json")
            assert member is not None and b'"object_is_moving":false' in member.read()
        with tarfile.open(rdshq_root / "3d_traffic_lights_status" / "carla_test.tar") as archive:
            member = archive.extractfile("sample.aggregated_states.json")
            assert member is not None
            states = json.loads(member.read())["traffic_light_states"]["carla_traffic_light_12_0"]["state"]
            assert states == ["GREEN", "YELLOW"]
        pcd_utils = out / "cosmos_transfer2/_src/imaginaire/auxiliary/world_scenario/utils/pcd_utils.py"
        pcd_utils.parent.mkdir(parents=True)
        pcd_utils.write_text(
            "import numpy as np\n"
            "from scipy.spatial import Delaunay\n\n"
            "def triangulate_polygon_3d(vertices3d: np.ndarray) -> np.ndarray:\n"
            "    vertices2d = vertices3d[:, :2]\n"
            "    tri = Delaunay(vertices2d)\n"
            "    return vertices3d[tri.simplices]\n\n"
            "def filter_by_height_relative_to_ego():\n"
            "    pass\n"
        )
        apply_robust_triangulation_patch(out)
        assert QHULL_SENTINEL in pcd_utils.read_text()
        assert pcd_utils.with_suffix(".py.carla_cosmos_backup").exists()
        clipgt_loader = out / "cosmos_transfer2/_src/imaginaire/auxiliary/world_scenario/dataloaders/clipgt_loader.py"
        clipgt_loader.parent.mkdir(parents=True)
        clipgt_loader.write_text(
            "from data_types import (\n"
            "    TrafficLight,\n"
            ")\n"
            "from data_utils import (\n"
            "    normalize_quaternions,\n"
            ")\n\n"
            "def load(scene_data, idx, center, dimensions, orientation):\n"
            "            scene_data.traffic_lights.append(\n"
            "                TrafficLight(\n"
            '                    element_id=f"traffic_light_{idx}",\n'
            "                    center=center,\n"
            "                    dimensions=dimensions,\n"
            "                    orientation=orientation,\n"
            "                )\n"
            "            )\n"
        )
        apply_clipgt_traffic_light_state_patch(out)
        assert TRAFFIC_LIGHT_STATE_SENTINEL in clipgt_loader.read_text()
        assert "normalize_traffic_light_state_sequence" in clipgt_loader.read_text()
    print("Smoke checks passed.")


if __name__ == "__main__":
    main()
