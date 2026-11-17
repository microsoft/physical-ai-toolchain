"""CARLA-free Cosmos world-scenario Parquet writing."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyarrow as pa


def scene_annotation_schema(element_name: str) -> pa.Schema:
    """Return the documented Cosmos schema for one annotation element."""
    import pyarrow as pa

    point = pa.struct([("x", pa.float64()), ("y", pa.float64()), ("z", pa.float64())])
    quat = pa.struct([("x", pa.float64()), ("y", pa.float64()), ("z", pa.float64()), ("w", pa.float64())])
    static_key = pa.struct(
        [
            ("clip_id", pa.string()),
            ("label_class_id", pa.string()),
            ("map_id", pa.string()),
            ("map_id_version", pa.string()),
        ]
    )
    timestamp_key = pa.struct([("clip_id", pa.string()), ("timestamp_micros", pa.int64())])
    dynamic_key = pa.struct(
        [
            ("clip_id", pa.string()),
            ("timestamp_micros", pa.int64()),
            ("label_class_id", pa.string()),
        ]
    )
    simple_location_element = pa.struct([("category", pa.string()), ("location", pa.list_(point))])
    traffic_light_element = pa.struct(
        [
            ("center", point),
            ("dimensions", point),
            ("orientation", quat),
            ("category", pa.string()),
            ("actor_id", pa.int64()),
            ("feature_id", pa.string()),
            ("state_sequence", pa.list_(pa.string())),
        ]
    )
    traffic_sign_element = pa.struct(
        [
            ("center", point),
            ("dimensions", point),
            ("orientation", quat),
            ("category", pa.string()),
            ("actor_id", pa.int64()),
            ("feature_id", pa.string()),
        ]
    )
    schemas = {
        "calibration_estimate": (
            timestamp_key,
            pa.struct([("name", pa.string()), ("rig_json", pa.string())]),
        ),
        "egomotion_estimate": (
            timestamp_key,
            pa.struct([("name", pa.string()), ("location", point), ("orientation", quat)]),
        ),
        "obstacle": (
            dynamic_key,
            pa.struct(
                [
                    ("trackline_id", pa.string()),
                    ("center", point),
                    ("size", point),
                    ("orientation", quat),
                    ("category", pa.string()),
                ]
            ),
        ),
        "lane": (
            static_key,
            pa.struct(
                [
                    ("left_rail", pa.list_(point)),
                    ("right_rail", pa.list_(point)),
                    ("left_edge_styles", pa.list_(pa.string())),
                    ("right_edge_styles", pa.list_(pa.string())),
                    ("left_edge_colors", pa.list_(pa.string())),
                    ("right_edge_colors", pa.list_(pa.string())),
                    ("lane_direction", pa.string()),
                    ("speed_limit", pa.float64()),
                    ("vehicle_types", pa.list_(pa.string())),
                ]
            ),
        ),
        "lane_line": (
            static_key,
            pa.struct(
                [
                    ("line_rail", pa.list_(point)),
                    ("styles", pa.list_(pa.string())),
                    ("colors", pa.list_(pa.string())),
                    ("left_driving_direction", pa.string()),
                    ("right_driving_direction", pa.string()),
                ]
            ),
        ),
        "road_boundary": (static_key, simple_location_element),
        "crosswalk": (static_key, simple_location_element),
        "pole": (static_key, simple_location_element),
        "road_marking": (static_key, simple_location_element),
        "wait_line": (
            static_key,
            pa.struct(
                [
                    ("category", pa.string()),
                    ("location", pa.list_(point)),
                    ("is_implicit", pa.bool_()),
                    ("intersection_subtype", pa.string()),
                ]
            ),
        ),
        "traffic_light": (static_key, traffic_light_element),
        "traffic_sign": (static_key, traffic_sign_element),
    }
    if element_name not in schemas:
        raise ValueError(f"Unsupported scene annotation element: {element_name}")
    key_type, element_type = schemas[element_name]
    return pa.schema([("key", key_type), (element_name, element_type), ("version", pa.uint64())])


def write_pylist_parquet(path: Path, rows: list[dict[str, Any]], schema_element: str) -> None:
    """Write nested dict/list rows to Parquet using the explicit Cosmos schema."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=scene_annotation_schema(schema_element))
    pq.write_table(table, path)
