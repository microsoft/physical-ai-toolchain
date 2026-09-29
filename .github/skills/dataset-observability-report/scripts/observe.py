# cspell:ignore flatnonzero lexsort
"""Compute the metrics bundle for a LeRobot v3.0 dataset observability report.

The dataset is read, never written. The bundle folder receives episodes.json, series.json,
metrics.json, and manifest.json.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2

INDEX_COLUMNS = ("timestamp", "frame_index", "episode_index", "index", "task_index")
NUMERIC_DTYPES = {"float16", "float32", "float64", "int8", "int16", "int32", "int64", "uint8", "uint16", "bool"}
GRIPPER_NAME = re.compile(r"grip|finger|claw|jaw|hand", re.IGNORECASE)
SIDE_NAME = re.compile(r"(?<![a-z])(left|right)(?![a-z])", re.IGNORECASE)
COMMAND_SUFFIX = re.compile(r"[._-](target|cmd|command|goal|desired)$", re.IGNORECASE)
CLOCK_NAME = re.compile(r"time|stamp|clock", re.IGNORECASE)
DERIVED_PARTS = (("meta", "videos"),)

EXPORT_GRID_TOLERANCE_S = 5e-4
GAP_FACTOR = 1.5
BURST_MIN_GAPS = 3
MAX_LAG_FRAMES = 15
SMOOTH_FRAMES = 5
IDLE_SPEED_FRACTION = 0.05
PAUSE_MIN_S = 0.5
STALL_MIN_S = 0.5
EVENT_FRACTION = 0.2
EVENT_MIN_FRAMES = 3
START_TOLERANCE = 0.02
REPEAT_MIN = 3
OUTLIER_MIN_EPISODES = 5
ROBUST_Z_LIMIT = 3.5
OUTLIER_MIN_EFFECT = 0.1


@dataclass
class Layout:
    fps: float
    state_key: str
    state_names: list[str]
    action_key: str | None
    action_names: list[str]
    paired: bool
    groups: dict[str, list[int]]
    motion: list[int]
    grippers: dict[str, int] = field(default_factory=dict)
    clock_key: str | None = None
    clock_scale: float = 1.0
    cameras: list[str] = field(default_factory=list)
    numeric: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def configure_logging(verbose: bool = False) -> None:
    """Configure logging based on verbosity level."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(levelname)s: %(message)s")


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(description="Compute the observability metrics bundle for a LeRobot v3.0 dataset")
    parser.add_argument("dataset", type=Path, help="Dataset root that contains meta/info.json")
    parser.add_argument("-o", "--output", type=Path, required=True, help="New bundle folder; must not exist")
    parser.add_argument("--state", help="State feature to analyze (default: observation.state)")
    parser.add_argument("--action", help="Action feature paired with the state (default: action)")
    parser.add_argument("--clock", help="Capture-clock feature (default: detected from feature names)")
    parser.add_argument("--no-clock", action="store_true", help="Skip capture-clock detection")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return parser


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            digest.update(block)
    return digest.hexdigest()


def inputs_digest(inputs: dict[str, str]) -> str:
    """Return one digest over the sorted input inventory."""
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def finite(value: object) -> object:
    """Replace non-finite floats with None so the bundle stays valid JSON."""
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, dict):
        return {key: finite(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [finite(item) for item in value]
    return value


def write_json(path: Path, value: object, compact: bool = False) -> None:
    data = finite(value)
    if compact:
        text = json.dumps(data, allow_nan=False, separators=(",", ":"))
    else:
        text = json.dumps(data, allow_nan=False, indent=1)
    path.write_text(text, encoding="utf-8")


def dataset_files(root: Path) -> list[Path]:
    """List dataset files, skipping hidden entries and derived viewer caches."""
    files = []
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if not path.is_file() or any(part.startswith(".") for part in parts):
            continue
        if any(parts[: len(prefix)] == prefix for prefix in DERIVED_PARTS):
            continue
        files.append(path)
    return files


def feature_width(feature: dict) -> int:
    shape = feature.get("shape") or [1]
    return int(np.prod(shape))


def feature_names(feature: dict, width: int, prefix: str) -> list[str]:
    """Return the per-dimension names of a vector feature, generating them when absent."""
    names = feature.get("names")
    if isinstance(names, dict):
        names = next((value for value in names.values() if isinstance(value, list)), None)
    if isinstance(names, list) and len(names) == width and all(isinstance(name, str) for name in names):
        return names
    return [f"{prefix}_{k}" for k in range(width)]


def base_name(name: str) -> str:
    return COMMAND_SUFFIX.sub("", name)


def group_label(name: str) -> str:
    side = SIDE_NAME.search(name)
    kind = "gripper" if GRIPPER_NAME.search(name) else "joints"
    return f"{side.group(1).lower()} {kind}" if side else kind


def resolve_layout(info: dict, args: argparse.Namespace) -> Layout:
    """Resolve state, action, groups, cameras, and clock candidates from meta/info.json."""
    features = info["features"]
    notes = []
    state_key = args.state or ("observation.state" if "observation.state" in features else None)
    if state_key is None:
        candidates = [
            key
            for key, feature in features.items()
            if key.startswith("observation.") and feature.get("dtype") in NUMERIC_DTYPES and feature_width(feature) > 1
        ]
        if not candidates:
            raise ValueError("no state feature found; pass --state")
        state_key = candidates[0]
        notes.append(f"State feature {state_key} chosen because observation.state is absent.")
    if state_key not in features:
        raise ValueError(f"state feature {state_key} is not in meta/info.json")
    state_width = feature_width(features[state_key])
    state_names = feature_names(features[state_key], state_width, "state")
    action_key = args.action or ("action" if "action" in features else None)
    action_names: list[str] = []
    paired = False
    if action_key:
        if action_key not in features:
            raise ValueError(f"action feature {action_key} is not in meta/info.json")
        action_width = feature_width(features[action_key])
        action_names = feature_names(features[action_key], action_width, "action")
        if action_width == state_width:
            same = [base_name(a) == base_name(s) for a, s in zip(action_names, state_names, strict=True)]
            generated = action_names[0] == "action_0" or state_names[0] == "state_0"
            paired = all(same) or generated
            if paired and generated:
                notes.append("State and action are paired by index because their names are missing.")
        if not paired:
            notes.append("Action names do not match state names, so command tracking is not computed.")
    groups: dict[str, list[int]] = collections.OrderedDict()
    for k, name in enumerate(state_names):
        groups.setdefault(group_label(name), []).append(k)
    motion = [k for label, idx in groups.items() if not label.endswith("gripper") for k in idx] or list(
        range(state_width)
    )
    cameras = [key for key, feature in features.items() if feature.get("dtype") == "video"]
    images = [key for key, feature in features.items() if feature.get("dtype") == "image"]
    if images:
        notes.append(f"Image features stored in the tables are not previewed: {', '.join(images)}.")
    numeric = [
        key for key, feature in features.items() if feature.get("dtype") in NUMERIC_DTYPES and key not in INDEX_COLUMNS
    ]
    return Layout(
        fps=float(info["fps"]),
        state_key=state_key,
        state_names=state_names,
        action_key=action_key,
        action_names=action_names,
        paired=paired,
        groups=dict(groups),
        motion=motion,
        cameras=cameras,
        numeric=numeric,
        notes=notes,
    )


def column_array(table: pa.Table, name: str) -> np.ndarray:
    """Convert a scalar or fixed-width list column to a NumPy array."""
    column = table.column(name).combine_chunks()
    if pa.types.is_list(column.type) or pa.types.is_large_list(column.type) or pa.types.is_fixed_size_list(column.type):
        values = column.flatten().to_numpy(zero_copy_only=False).astype(np.float64)
        return values.reshape(len(column), -1)
    return column.to_numpy(zero_copy_only=False)


def load_data(root: Path, columns: list[str]) -> dict[str, np.ndarray]:
    """Load the data tables sorted by episode and frame."""
    files = sorted((root / "data").rglob("*.parquet"))
    if not files:
        raise ValueError("no parquet files under data/")
    parts: dict[str, list[np.ndarray]] = {name: [] for name in columns}
    for path in files:
        table = pq.read_table(path, columns=columns)
        for name in columns:
            parts[name].append(column_array(table, name))
    data = {name: np.concatenate(chunks) for name, chunks in parts.items()}
    order = np.lexsort((data["frame_index"], data["episode_index"]))
    return {name: values[order] for name, values in data.items()}


def load_episode_meta(root: Path, info: dict, cameras: list[str]) -> dict[int, dict]:
    """Read per-episode metadata, task text, and video windows from meta/episodes."""
    files = sorted((root / "meta" / "episodes").rglob("*.parquet"))
    if not files:
        raise ValueError("meta/episodes is missing; convert LeRobot v2.1 datasets to v3.0 first")
    episodes: dict[int, dict] = {}
    for path in files:
        table = pq.read_table(path)
        keep = [name for name in table.column_names if not name.startswith("stats/")]
        for row in table.select(keep).to_pylist():
            ep = int(row["episode_index"])
            videos = {}
            for camera in cameras:
                chunk = row.get(f"videos/{camera}/chunk_index")
                index = row.get(f"videos/{camera}/file_index")
                if chunk is None or index is None:
                    videos[camera] = None
                    continue
                relative = info["video_path"].format(video_key=camera, chunk_index=int(chunk), file_index=int(index))
                resolved = (root / relative).resolve()
                inside = resolved.is_relative_to(root.resolve())
                videos[camera] = {
                    "file": relative,
                    "from": float(row.get(f"videos/{camera}/from_timestamp") or 0.0),
                    "to": float(row.get(f"videos/{camera}/to_timestamp") or 0.0),
                    "exists": inside and resolved.is_file(),
                }
            tasks = row.get("tasks") or []
            episodes[ep] = {
                "length": int(row.get("length") or 0),
                "tasks": [str(task) for task in tasks],
                "videos": videos,
            }
    return episodes


def detect_clock(
    layout: Layout, info: dict, data: dict[str, np.ndarray], starts: np.ndarray, forced: str | None
) -> None:
    """Pick a monotonic capture-clock column and its unit scale."""
    features = info["features"]
    if forced:
        if forced not in data:
            raise ValueError(f"clock feature {forced} is not in the data tables")
        candidates = [forced]
    else:
        candidates = [
            key
            for key, feature in features.items()
            if key in data and key not in INDEX_COLUMNS and CLOCK_NAME.search(key) and feature_width(feature) == 1
        ]
    period = 1.0 / layout.fps
    for key in candidates:
        values = data[key].reshape(-1).astype(np.int64 if np.issubdtype(data[key].dtype, np.integer) else np.float64)
        steps = np.diff(values).astype(np.float64)
        steps[starts[1:] - 1] = np.nan
        steps = steps[np.isfinite(steps)]
        if steps.size < 2 or np.mean(steps > 0) < 0.99:
            continue
        ratio = float(np.median(steps)) / period
        if ratio <= 0:
            continue
        exponent = round(np.log10(ratio) / 3) * 3
        layout.clock_key = key
        layout.clock_scale = 10.0**-exponent
        unit = {0: "s", 3: "ms", 6: "us", 9: "ns"}.get(exponent, f"1e-{exponent} s")
        layout.notes.append(f"Capture clock {key} detected, read as {unit}.")
        return
    if forced:
        raise ValueError(f"clock feature {forced} is not monotonic within episodes")


def choose_grippers(layout: Layout, state: np.ndarray) -> None:
    """Keep the widest-ranging channel of each gripper group."""
    span = np.ptp(state, axis=0)
    for label, idx in layout.groups.items():
        if label.endswith("gripper"):
            best = max(idx, key=lambda k: span[k])
            if span[best] > 0:
                layout.grippers[label] = best


def runs(mask: np.ndarray, min_len: int) -> list[list[int]]:
    """Return [start, end] pairs of True runs of at least min_len samples."""
    out, start = [], None
    for k, flag in enumerate(np.append(mask, False)):
        if flag and start is None:
            start = k
        elif not flag and start is not None:
            if k - start >= min_len:
                out.append([start, k - 1])
            start = None
    return out


def smooth(values: np.ndarray, width: int = SMOOTH_FRAMES) -> np.ndarray:
    if values.size < width:
        return values
    kernel = np.ones(width) / width
    return np.convolve(values, kernel, mode="same")


def robust_z(values: np.ndarray) -> np.ndarray:
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    return np.zeros_like(values) if mad == 0 else 0.6745 * (values - median) / mad


def best_lag(state: np.ndarray, action: np.ndarray) -> dict:
    """Shift the command against the measured state and keep the shift with the lowest RMS error."""
    limit = min(MAX_LAG_FRAMES, len(state) - 2)
    errors = [float(np.sqrt(np.mean((state[lag:] - action[: len(action) - lag]) ** 2))) for lag in range(limit + 1)]
    k = int(np.argmin(errors))
    return {"lag": k, "rmse0": round(errors[0], 6), "rmse_lag": round(errors[k], 6)}


def gripper_events(values: np.ndarray, span: float) -> dict:
    """Detect when a gripper leaves and returns to its start value."""
    n = len(values)
    start = float(np.median(values[: min(EVENT_MIN_FRAMES, n)]))
    away = np.abs(values - start) > EVENT_FRACTION * span
    closes = runs(away, EVENT_MIN_FRAMES)
    close = closes[0][0] if closes else None
    opened = None
    if close is not None:
        back = runs(~away[close:], EVENT_MIN_FRAMES)
        opened = close + back[0][0] if back else None
    closure = np.clip(np.abs(values - start) / span, 0, 1)
    return {"close": close, "open": opened, "cycles": len(closes), "series": np.round(closure, 3).tolist()}


def greedy_clusters(points: np.ndarray, tolerance: np.ndarray) -> list[int]:
    centers: list[np.ndarray] = []
    labels = []
    for point in points:
        for k, center in enumerate(centers):
            if np.all(np.abs(point - center) <= tolerance):
                labels.append(k)
                break
        else:
            centers.append(point)
            labels.append(len(centers) - 1)
    return labels


def analyze_episode(
    ep: int, rows: slice, data: dict[str, np.ndarray], layout: Layout, context: dict
) -> tuple[dict, dict]:
    """Compute one episode record and its per-frame series."""
    fps = layout.fps
    frame_index = data["frame_index"][rows]
    n = len(frame_index)
    state = data[layout.state_key][rows]
    meta = context["meta"].get(ep, {"length": 0, "tasks": [], "videos": {}})
    timestamps = data["timestamp"][rows].reshape(-1).astype(np.float64)
    export_error = float(np.max(np.abs(timestamps - frame_index / fps))) if n else 0.0
    step = np.diff(state[:, layout.motion], axis=0)
    speed = np.concatenate([[0.0], np.linalg.norm(step, axis=1) * fps]) if n > 1 else np.zeros(n)
    smoothed = smooth(speed)
    moving = smoothed > context["idle_speed"]
    first = int(np.argmax(moving)) if moving.any() else None
    last = int(n - 1 - np.argmax(moving[::-1])) if moving.any() else None
    pauses = []
    if first is not None and last is not None:
        pauses = [[first + a, first + b] for a, b in runs(~moving[first : last + 1], round(PAUSE_MIN_S * fps))]
    changed = np.any(np.diff(state, axis=0) != 0, axis=1) if n > 1 else np.zeros(0, dtype=bool)
    stalls: list[list[int]] = []
    record_tracking = {}
    if layout.action_key:
        action = data[layout.action_key][rows]
        commanded = np.any(np.diff(action, axis=0) != 0, axis=1) if n > 1 else np.zeros(0, dtype=bool)
        stalls = [[a + 1, b + 1] for a, b in runs(~changed & commanded, round(STALL_MIN_S * fps))]
        if layout.paired and n > 3:
            for label, idx in layout.groups.items():
                if np.ptp(action[:, idx], axis=0).max() > 1e-6:
                    record_tracking[label] = best_lag(state[:, idx], action[:, idx])
    capture = None
    if layout.clock_key:
        clock = data[layout.clock_key][rows].reshape(-1)
        dt = np.diff(clock).astype(np.float64) * layout.clock_scale
        period = 1.0 / fps
        normal = dt[(dt > 0.5 * period) & (dt < GAP_FACTOR * period)]
        gaps = [
            [int(k + 1), round(float(dt[k]) * 1000, 3), max(0, round(float(dt[k]) / period) - 1)]
            for k in np.flatnonzero(dt > GAP_FACTOR * period)
        ]
        first_s = float(clock[0]) * layout.clock_scale if n else 0.0
        capture = {
            "period_ms": round(float(np.median(normal)) * 1000, 4) if normal.size else None,
            "jitter_ms": round(float(np.std(normal)) * 1000, 4) if normal.size else None,
            "gaps": gaps,
            "missing_frames": int(sum(g[2] for g in gaps)),
            "catch_up": int(np.sum((dt > 0) & (dt < 0.5 * period))),
            "non_increasing": int(np.sum(dt <= 0)),
            "start_utc": datetime.fromtimestamp(first_s, UTC).strftime("%Y-%m-%d %H:%M:%S") if first_s > 1e9 else None,
        }
    grippers = {}
    for label, j in layout.grippers.items():
        grippers[label] = gripper_events(state[:, j], context["gripper_span"][label])
    nonfinite = {}
    for key in layout.numeric:
        values = data[key][rows]
        if np.issubdtype(values.dtype, np.floating):
            count = int(np.size(values) - np.count_nonzero(np.isfinite(values)))
            if count:
                nonfinite[key] = count
    videos = {}
    for camera in layout.cameras:
        window = meta["videos"].get(camera)
        if window is None:
            videos[camera] = None
            continue
        videos[camera] = {**window, "window_frames": round((window["to"] - window["from"]) * fps)}
    record = {
        "episode": ep,
        "frames": n,
        "duration_s": round(n / fps, 4),
        "tasks": meta["tasks"],
        "meta_length": meta["length"],
        "frame_index_contiguous": bool(np.array_equal(frame_index, np.arange(n))),
        "export_error_ms": round(export_error * 1000, 4),
        "first_row_repeated": bool(n > 1 and not changed[0]),
        "state_update_ratio": round(float(np.mean(changed)), 4) if changed.size else None,
        "motion": {
            "path": round(float(np.linalg.norm(step, axis=1).sum()), 5) if n > 1 else 0.0,
            "peak_speed": round(float(smoothed.max()), 5) if n else 0.0,
            "median_speed": round(float(np.median(smoothed[moving])), 5) if moving.any() else 0.0,
            "idle_fraction": round(float(1 - moving.mean()), 4) if n else 1.0,
            "idle_start_s": None if first is None else round(first / fps, 3),
            "idle_end_s": None if last is None else round((n - 1 - last) / fps, 3),
            "pauses": pauses,
        },
        "stalls": stalls,
        "grippers": {label: {k: v for k, v in g.items() if k != "series"} for label, g in grippers.items()},
        "tracking": record_tracking,
        "capture": capture,
        "nonfinite": nonfinite,
        "videos": videos,
        "start": np.round(state[0], 6).tolist() if n else [],
        "end": np.round(state[-1], 6).tolist() if n else [],
    }
    series = {
        "speed": np.round(smoothed, 5).tolist(),
        "grippers": {label: g["series"] for label, g in grippers.items()},
    }
    return record, series


def start_conditions(records: list[dict], span: np.ndarray) -> dict:
    """Cluster start states and project them onto two principal components."""
    starts = np.array([r["start"] for r in records], dtype=np.float64)
    tolerance = np.where(span > 0, START_TOLERANCE * span, 0.0)
    labels = greedy_clusters(starts, tolerance)
    for record, label in zip(records, labels, strict=True):
        record["start_condition"] = int(label)
    counts = collections.Counter(labels)
    scaled = (starts - starts.mean(axis=0)) / np.where(span > 0, span, 1.0)
    variance = [0.0, 0.0]
    projection = np.zeros((len(records), 2))
    if len(records) >= 3 and np.any(scaled):
        _, singular, vt = np.linalg.svd(scaled, full_matrices=False)
        components = vt[:2]
        projection[:, : components.shape[0]] = scaled @ components.T
        total = float(np.sum(singular**2)) or 1.0
        variance = [round(float(s**2) / total, 4) for s in singular[:2]] + [0.0] * (2 - min(2, singular.size))
    for record, point in zip(records, projection, strict=True):
        record["start_pc"] = [round(float(point[0]), 5), round(float(point[1]), 5)]
    repeats = sorted(
        (
            {"condition": k, "size": v, "episodes": [r["episode"] for r in records if r["start_condition"] == k]}
            for k, v in counts.items()
            if v >= REPEAT_MIN
        ),
        key=lambda item: -item["size"],
    )
    spread = np.std(starts, axis=0) if len(records) > 1 else np.zeros(starts.shape[1])
    return {
        "conditions": len(counts),
        "repeats": repeats,
        "tolerance_fraction": START_TOLERANCE,
        "pc_variance": variance,
        "spread": np.round(spread, 6).tolist(),
    }


def flag_outliers(records: list[dict]) -> dict[int, list[dict]]:
    """Flag episodes whose robust z-score exceeds the limit on any summary metric."""
    for record in records:
        record["outliers"] = []
    if len(records) < OUTLIER_MIN_EPISODES:
        return {}

    def tracking_error(record: dict) -> float:
        values = [t["rmse0"] for label, t in record["tracking"].items() if not label.endswith("gripper")]
        return float(np.mean(values)) if values else np.nan

    metrics = {
        "duration": [r["duration_s"] for r in records],
        "joint path": [r["motion"]["path"] for r in records],
        "peak speed": [r["motion"]["peak_speed"] for r in records],
        "idle share": [r["motion"]["idle_fraction"] for r in records],
        "tracking error": [tracking_error(r) for r in records],
    }
    for name, values in metrics.items():
        array = np.array(values, dtype=np.float64)
        valid = np.isfinite(array)
        if valid.sum() < OUTLIER_MIN_EPISODES:
            continue
        z = np.zeros_like(array)
        z[valid] = robust_z(array[valid])
        median = float(np.median(array[valid]))
        for record, value, score in zip(records, array, z, strict=True):
            effect = abs(value - median) / max(abs(median), 1e-9)
            if abs(score) > ROBUST_Z_LIMIT and effect >= OUTLIER_MIN_EFFECT:
                record["outliers"].append(
                    {"metric": name, "value": round(float(value), 5), "z": round(float(score), 2)}
                )
    return {r["episode"]: r["outliers"] for r in records if r["outliers"]}


def constant_channels(data: dict[str, np.ndarray], layout: Layout, starts: np.ndarray) -> dict[str, list[str]]:
    """List numeric channels that never change, globally or within every episode."""
    everywhere, within = [], []
    features = [key for key in layout.numeric if key in data]
    for key in features:
        values = data[key]
        matrix = values.reshape(len(values), -1).astype(np.float64)
        names = (
            layout.state_names
            if key == layout.state_key
            else layout.action_names
            if key == layout.action_key
            else [str(k) for k in range(matrix.shape[1])]
        )
        low = np.minimum.reduceat(matrix, starts, axis=0)
        high = np.maximum.reduceat(matrix, starts, axis=0)
        for d in range(matrix.shape[1]):
            label = key if matrix.shape[1] == 1 else f"{key}[{names[d] if d < len(names) else d}]"
            if np.all(high[:, d] == low[:, d]):
                if np.ptp(matrix[:, d]) == 0:
                    everywhere.append(label)
                else:
                    within.append(label)
    return {"everywhere": everywhere, "within_episodes": within}


def event_summary(records: list[dict], label: str, key: str, fps: float) -> dict:
    values = np.array(
        [
            r["grippers"][label][key]
            for r in records
            if label in r["grippers"] and r["grippers"][label][key] is not None
        ],
        dtype=np.float64,
    )
    if not values.size:
        return {"count": 0}
    values = values / fps
    return {
        "count": int(values.size),
        "median_s": round(float(np.median(values)), 3),
        "p5_s": round(float(np.percentile(values, 5)), 3),
        "p95_s": round(float(np.percentile(values, 95)), 3),
    }


def summarize(records: list[dict], layout: Layout, info: dict, extra: dict) -> dict:
    """Aggregate dataset-level metrics from the episode records."""
    n_eps = len(records)
    frames = sum(r["frames"] for r in records)
    fps = layout.fps
    tasks = collections.Counter(task for r in records for task in r["tasks"])
    features = info["features"]
    cameras = []
    for camera in layout.cameras:
        feature = features[camera]
        video_info = feature.get("info") or {}
        present = [r["episode"] for r in records if r["videos"].get(camera) and r["videos"][camera]["exists"]]
        mismatch = [
            r["episode"]
            for r in records
            if r["videos"].get(camera)
            and r["videos"][camera]["exists"]
            and r["videos"][camera]["window_frames"] != r["frames"]
        ]
        cameras.append(
            {
                "key": camera,
                "shape": feature.get("shape"),
                "codec": video_info.get("video.codec"),
                "episodes_with_video": len(present),
                "missing": [r["episode"] for r in records if r["episode"] not in set(present)],
                "window_mismatch": mismatch,
            }
        )
    tracking = {}
    for label in layout.groups:
        rows = [r["tracking"][label] for r in records if label in r["tracking"]]
        if rows:
            lags = collections.Counter(row["lag"] for row in rows)
            tracking[label] = {
                "episodes": len(rows),
                "lag_mode": lags.most_common(1)[0][0],
                "lags": {str(k): v for k, v in sorted(lags.items())},
                "rmse0_median": round(float(np.median([row["rmse0"] for row in rows])), 6),
                "rmse_lag_median": round(float(np.median([row["rmse_lag"] for row in rows])), 6),
            }
    capture = None
    if layout.clock_key:
        gaps = [(r["episode"], g) for r in records for g in r["capture"]["gaps"]]
        periods = [r["capture"]["period_ms"] for r in records if r["capture"]["period_ms"] is not None]
        jitters = [r["capture"]["jitter_ms"] for r in records if r["capture"]["jitter_ms"] is not None]
        per_episode = collections.Counter(ep for ep, _ in gaps)
        missing = sum(g[2] for _, g in gaps)
        capture = {
            "clock": layout.clock_key,
            "period_ms": round(float(np.median(periods)), 4) if periods else None,
            "jitter_ms": round(float(np.median(jitters)), 4) if jitters else None,
            "gaps": len(gaps),
            "gap_episodes": sorted(per_episode),
            "bursts": sorted(ep for ep, count in per_episode.items() if count >= BURST_MIN_GAPS),
            "missing_frames": int(missing),
            "missing_pct": round(100 * missing / max(1, frames + missing), 4),
            "max_gap_ms": max((g[1] for _, g in gaps), default=0.0),
            "catch_up": sum(r["capture"]["catch_up"] for r in records),
            "non_increasing": sum(r["capture"]["non_increasing"] for r in records),
        }
    grippers = {}
    for label in layout.grippers:
        grippers[label] = {
            "channel": layout.state_names[layout.grippers[label]],
            "close": event_summary(records, label, "close", fps),
            "open": event_summary(records, label, "open", fps),
            "never_closed": [r["episode"] for r in records if r["grippers"][label]["close"] is None],
            "multi_cycle": [r["episode"] for r in records if r["grippers"][label]["cycles"] > 1],
        }
    motion = {
        "idle_speed": round(extra["idle_speed"], 6),
        "speed_p99": round(extra["speed_p99"], 6),
        "duration_median_s": round(float(np.median([r["duration_s"] for r in records])), 4),
        "path_median": round(float(np.median([r["motion"]["path"] for r in records])), 5),
        "peak_speed_median": round(float(np.median([r["motion"]["peak_speed"] for r in records])), 5),
        "idle_fraction_median": round(float(np.median([r["motion"]["idle_fraction"] for r in records])), 4),
        "pause_episodes": [r["episode"] for r in records if r["motion"]["pauses"]],
    }
    return {
        "dataset": {
            "name": extra["name"],
            "codebase_version": info.get("codebase_version"),
            "robot_type": info.get("robot_type"),
            "fps": fps,
            "episodes": n_eps,
            "frames": frames,
            "duration_min": round(frames / fps / 60, 3),
            "tasks": dict(tasks.most_common()),
            "cameras": cameras,
            "state": {"key": layout.state_key, "names": layout.state_names},
            "action": {"key": layout.action_key, "names": layout.action_names, "paired": layout.paired},
            "groups": {label: [layout.state_names[k] for k in idx] for label, idx in layout.groups.items()},
            "motion_channels": [layout.state_names[k] for k in layout.motion],
            "files": extra["files"],
            "bytes": extra["bytes"],
        },
        "layout_notes": layout.notes,
        "integrity": {
            "frame_index_contiguous": sum(r["frame_index_contiguous"] for r in records),
            "export_on_grid": sum(r["export_error_ms"] <= EXPORT_GRID_TOLERANCE_S * 1000 for r in records),
            "export_error_max_ms": max(r["export_error_ms"] for r in records),
            "length_matches_meta": sum(r["frames"] == r["meta_length"] for r in records),
            "first_row_repeated": [r["episode"] for r in records if r["first_row_repeated"]],
            "state_update_median": round(
                float(np.median([r["state_update_ratio"] for r in records if r["state_update_ratio"] is not None])), 4
            )
            if any(r["state_update_ratio"] is not None for r in records)
            else None,
            "stall_episodes": [r["episode"] for r in records if r["stalls"]],
            "nonfinite": {str(r["episode"]): r["nonfinite"] for r in records if r["nonfinite"]},
            "constant": extra["constant"],
        },
        "capture": capture,
        "motion": motion,
        "grippers": grippers,
        "tracking": tracking,
        "starts": extra["starts"],
        "outliers": {str(k): v for k, v in extra["outliers"].items()},
        "thresholds": {
            "gap_factor": GAP_FACTOR,
            "idle_speed_fraction": IDLE_SPEED_FRACTION,
            "pause_min_s": PAUSE_MIN_S,
            "stall_min_s": STALL_MIN_S,
            "event_fraction": EVENT_FRACTION,
            "start_tolerance": START_TOLERANCE,
            "robust_z_limit": ROBUST_Z_LIMIT,
            "outlier_min_effect": OUTLIER_MIN_EFFECT,
            "max_lag_frames": MAX_LAG_FRAMES,
        },
    }


def run(args: argparse.Namespace) -> int:
    root = args.dataset
    if args.output.exists():
        logger.error("output %s exists; choose a new folder", args.output)
        return EXIT_ERROR
    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        logger.error("%s has no meta/info.json", root)
        return EXIT_ERROR
    info = json.loads(info_path.read_text(encoding="utf-8"))
    if not str(info.get("codebase_version", "")).startswith("v3"):
        logger.error("LeRobot v3.0 is required, found %s; convert the dataset first", info.get("codebase_version"))
        return EXIT_ERROR
    try:
        layout = resolve_layout(info, args)
    except ValueError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR
    tables = sorted((root / "data").rglob("*.parquet"))
    if not tables:
        logger.error("%s has no parquet tables under data/", root)
        return EXIT_ERROR
    schema = pq.read_schema(tables[0])
    columns = [name for name in (*INDEX_COLUMNS, *layout.numeric) if name in schema.names]
    missing = {"timestamp", "frame_index", "episode_index", layout.state_key} - set(columns)
    if missing:
        logger.error("data tables lack required columns: %s", ", ".join(sorted(missing)))
        return EXIT_FAILURE
    layout.numeric = [key for key in layout.numeric if key in columns]
    data = load_data(root, columns)
    episode_index = data["episode_index"].reshape(-1)
    starts = np.flatnonzero(np.r_[True, episode_index[1:] != episode_index[:-1]])
    ends = np.r_[starts[1:], len(episode_index)]
    if not args.no_clock:
        try:
            detect_clock(layout, info, data, starts, args.clock)
        except ValueError as exc:
            logger.error("%s", exc)
            return EXIT_ERROR
    state = data[layout.state_key]
    choose_grippers(layout, state)
    for label, j in layout.grippers.items():
        layout.notes.append(f"Gripper events for {label} use {layout.state_names[j]}.")
    meta = load_episode_meta(root, info, layout.cameras)
    speeds = []
    for a, b in zip(starts, ends, strict=True):
        step = np.diff(state[a:b][:, layout.motion], axis=0)
        speeds.append(smooth(np.linalg.norm(step, axis=1) * layout.fps) if len(step) else np.zeros(0))
    all_speed = np.concatenate(speeds) if speeds else np.zeros(1)
    moving_speed = all_speed[all_speed > 0]
    speed_p95 = float(np.percentile(moving_speed, 95)) if moving_speed.size else 0.0
    gripper_span = {}
    for label, j in layout.grippers.items():
        low, high = np.percentile(state[:, j], [1, 99])
        gripper_span[label] = float(high - low) or float(np.ptp(state[:, j])) or 1.0
    context = {"meta": meta, "idle_speed": IDLE_SPEED_FRACTION * speed_p95, "gripper_span": gripper_span}
    records, series = [], {}
    for a, b in zip(starts, ends, strict=True):
        ep = int(episode_index[a])
        record, episode_series = analyze_episode(ep, slice(int(a), int(b)), data, layout, context)
        records.append(record)
        series[str(ep)] = episode_series
    span = np.ptp(state, axis=0)
    starts_summary = start_conditions(records, span)
    outliers = flag_outliers(records)
    files = dataset_files(root)
    logger.info("hashing %d dataset files", len(files))
    inputs = {path.relative_to(root).as_posix(): sha256_file(path) for path in files}
    extra = {
        "name": root.name,
        "idle_speed": context["idle_speed"],
        "speed_p99": float(np.percentile(all_speed, 99)) if all_speed.size else 0.0,
        "constant": constant_channels(data, layout, starts),
        "starts": starts_summary,
        "outliers": outliers,
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
    }
    metrics = summarize(records, layout, info, extra)
    args.output.mkdir(parents=True)
    write_json(args.output / "episodes.json", records)
    write_json(args.output / "series.json", series, compact=True)
    write_json(args.output / "metrics.json", metrics)
    manifest = {
        "tool": Path(__file__).name,
        "tool_sha256": sha256_file(Path(__file__)),
        "dataset": root.name,
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "inputs": inputs,
        "inputs_digest": inputs_digest(inputs),
        "layout": {
            "state": layout.state_key,
            "action": layout.action_key,
            "paired": layout.paired,
            "clock": layout.clock_key,
            "clock_scale": layout.clock_scale,
            "grippers": {label: layout.state_names[j] for label, j in layout.grippers.items()},
            "cameras": layout.cameras,
        },
    }
    write_json(args.output / "manifest.json", manifest)
    logger.info("wrote %s (%d episodes, %d frames)", args.output, len(records), metrics["dataset"]["frames"])
    for note in layout.notes:
        logger.info("layout: %s", note)
    return EXIT_SUCCESS


def main() -> int:
    """Main entry point."""
    args = create_parser().parse_args()
    configure_logging(args.verbose)
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError) as exc:
        logger.error("%s", exc)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
