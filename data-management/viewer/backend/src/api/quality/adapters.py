"""Strict format-specific source adapters for quality validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numpy.typing import NDArray

from ..models.reviews import SourceFileIdentity, SourceIdentity
from .profiles import QualityProfile

try:
    import h5py
except ImportError:
    h5py = None


@dataclass(frozen=True)
class FeatureObservation:
    """Observed values and declared source type for one feature."""

    dtype: str
    shape: tuple[int, ...]
    values: NDArray[Any]


@dataclass(frozen=True)
class SourceSnapshot:
    """Format-neutral strict source inspection result."""

    source: SourceIdentity
    frame_count: int
    features: dict[str, FeatureObservation]
    timestamps: NDArray[np.float64] | None
    frame_indices: NDArray[np.int64] | None
    available_artifacts: frozenset[str]
    metadata: dict[str, Any]
    task_labels: tuple[str, ...]
    calibration: dict[str, Any] | None
    readback_errors: tuple[str, ...]


class SourceAdapter(Protocol):
    """Strict source inspection boundary shared by quality orchestration."""

    def inspect(
        self,
        dataset_root: Path,
        dataset_id: str,
        episode_index: int,
        profile: QualityProfile,
    ) -> SourceSnapshot: ...


class LeRobotSourceAdapter:
    """Inspect LeRobot Parquet sources without viewer fallback substitution."""

    def inspect(
        self,
        dataset_root: Path,
        dataset_id: str,
        episode_index: int,
        profile: QualityProfile,
    ) -> SourceSnapshot:
        info_path = dataset_root / "meta" / "info.json"
        info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
        tables = []
        readback_errors: list[str] = []
        for path in sorted((dataset_root / "data").rglob("*.parquet")):
            try:
                table = pq.read_table(path)
            except Exception as exc:
                readback_errors.append(f"parquet:{path.relative_to(dataset_root)}:{type(exc).__name__}")
                continue
            if "episode_index" in table.column_names:
                mask = pa.array([value == episode_index for value in table.column("episode_index").to_pylist()])
                table = table.filter(mask)
            if table.num_rows:
                tables.append(table)
        table = pa.concat_tables(tables, promote_options="default") if tables else pa.table({})
        if "frame_index" in table.column_names:
            order = pa.array(np.argsort(table.column("frame_index").to_numpy()).tolist())
            table = table.take(order)

        features = self._extract_features(table)
        timestamps = self._numeric_column(table, "timestamp", np.float64)
        frame_indices = self._numeric_column(table, "frame_index", np.int64)
        task_labels = self._read_task_labels(dataset_root / "meta" / "tasks.parquet")
        calibration = self._read_json(dataset_root / profile.calibration.relative_path) if profile.calibration else None
        readback_errors.extend(self._readback_videos(dataset_root, info))
        files = _source_files(dataset_root)
        metadata = dict(info)
        episode_frame_count = self._read_episode_frame_count(dataset_root, episode_index)
        if episode_frame_count is not None:
            metadata["episode_frame_count"] = episode_frame_count
        return SourceSnapshot(
            source=_source_identity(
                dataset_id,
                episode_index,
                "lerobot",
                str(info.get("codebase_version", "unknown")),
                files,
            ),
            frame_count=table.num_rows,
            features=features,
            timestamps=timestamps,
            frame_indices=frame_indices,
            available_artifacts=frozenset(file.relative_path for file in files),
            metadata=metadata,
            task_labels=task_labels,
            calibration=calibration,
            readback_errors=tuple(readback_errors),
        )

    @staticmethod
    def _extract_features(table: pa.Table) -> dict[str, FeatureObservation]:
        features: dict[str, FeatureObservation] = {}
        for name in table.column_names:
            column = table.column(name)
            arrow_type = column.type
            value_type = (
                arrow_type.value_type
                if pa.types.is_list(arrow_type)
                or pa.types.is_large_list(arrow_type)
                or pa.types.is_fixed_size_list(arrow_type)
                else arrow_type
            )
            dtype = _arrow_dtype(value_type)
            values = np.asarray(column.to_pylist(), dtype=np.dtype(dtype) if dtype != "object" else object)
            shape = tuple(values.shape[1:]) if values.ndim > 1 else (1,)
            features[name] = FeatureObservation(dtype=dtype, shape=shape, values=values)
        return features

    @staticmethod
    def _numeric_column(table: pa.Table, name: str, dtype: type[np.generic]) -> NDArray[Any] | None:
        if name not in table.column_names:
            return None
        return np.asarray(table.column(name).to_pylist(), dtype=dtype)

    @staticmethod
    def _read_task_labels(path: Path) -> tuple[str, ...]:
        if not path.is_file():
            return ()
        table = pq.read_table(path)
        if "task" not in table.column_names:
            return ()
        return tuple(str(value).strip() for value in table.column("task").to_pylist() if str(value).strip())

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _read_episode_frame_count(dataset_root: Path, episode_index: int) -> int | None:
        for path in sorted((dataset_root / "meta" / "episodes").rglob("*.parquet")):
            table = pq.read_table(path, columns=["episode_index", "length"])
            matches = table.filter(pa.compute.equal(table["episode_index"], episode_index))
            if matches.num_rows:
                return int(matches["length"][0].as_py())
        legacy_path = dataset_root / "meta" / "episodes.jsonl"
        if legacy_path.is_file():
            with legacy_path.open(encoding="utf-8") as stream:
                for line in stream:
                    episode = json.loads(line)
                    if int(episode.get("episode_index", -1)) == episode_index:
                        return int(episode["length"])
        return None

    @staticmethod
    def _readback_videos(dataset_root: Path, info: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for feature_name, declaration in info.get("features", {}).items():
            if declaration.get("dtype") != "video":
                continue
            paths = sorted((dataset_root / "videos" / feature_name).rglob("*.mp4"))
            if not paths:
                errors.append(f"video:{feature_name}:missing")
                continue
            try:
                import av

                with av.open(str(paths[0])) as container:
                    has_frames = any(True for _frame in container.decode(video=0))
                if not has_frames:
                    errors.append(f"video:{feature_name}:empty")
            except Exception as exc:
                errors.append(f"video:{feature_name}:{type(exc).__name__}")
        return errors


class HDF5SourceAdapter:
    """Inspect HDF5 sources without generated timestamps or zero arrays."""

    _FEATURE_PATHS: ClassVar[dict[str, tuple[str, ...]]] = {
        "observation.state": ("observation/state", "observations/qpos", "data/qpos", "qpos"),
        "observation.velocity": ("observations/qvel", "data/qvel", "qvel"),
        "action": ("data/action", "action", "actions"),
    }

    def inspect(
        self,
        dataset_root: Path,
        dataset_id: str,
        episode_index: int,
        profile: QualityProfile,
    ) -> SourceSnapshot:
        if h5py is None:
            raise ImportError("HDF5 quality validation requires h5py")
        path = self._find_episode(dataset_root, episode_index)
        features: dict[str, FeatureObservation] = {}
        metadata: dict[str, Any] = {}
        readback_errors: list[str] = []
        with h5py.File(path, "r") as source:
            for name, candidates in self._FEATURE_PATHS.items():
                dataset = next((source[candidate] for candidate in candidates if candidate in source), None)
                if dataset is not None:
                    values = np.asarray(dataset[:])
                    features[name] = FeatureObservation(
                        dtype=str(values.dtype),
                        shape=tuple(values.shape[1:]),
                        values=values,
                    )
            timestamps = self._array(source, ("data/timestamps", "timestamps", "timestamp", "time"), np.float64)
            frame_indices = self._array(source, ("frame_index", "data/frame_index"), np.int64)
            metadata = {key: _json_value(value) for key, value in source.attrs.items()}
            task = str(metadata.get("task", metadata.get("task_label", ""))).strip()
            for group_name in ("observations/images", "observation/images", "images", "data/images"):
                if group_name in source and isinstance(source[group_name], h5py.Group):
                    for camera_name, dataset in source[group_name].items():
                        try:
                            for index in {0, max(0, len(dataset) // 2), max(0, len(dataset) - 1)}:
                                dataset[index]
                        except Exception as exc:
                            readback_errors.append(f"image:{camera_name}:{type(exc).__name__}")
        calibration = (
            LeRobotSourceAdapter._read_json(dataset_root / profile.calibration.relative_path)
            if profile.calibration
            else None
        )
        files = _source_files(dataset_root)
        frame_count = max((len(feature.values) for feature in features.values()), default=0)
        return SourceSnapshot(
            source=_source_identity(
                dataset_id,
                episode_index,
                "hdf5",
                str(metadata.get("format_version", "1.0")),
                files,
            ),
            frame_count=frame_count,
            features=features,
            timestamps=timestamps,
            frame_indices=frame_indices,
            available_artifacts=frozenset(file.relative_path for file in files),
            metadata=metadata,
            task_labels=(task,) if task else (),
            calibration=calibration,
            readback_errors=tuple(readback_errors),
        )

    @staticmethod
    def _find_episode(root: Path, episode_index: int) -> Path:
        candidates = (
            root / f"episode_{episode_index:06d}.hdf5",
            root / f"episode_{episode_index}.hdf5",
            root / "data" / f"episode_{episode_index:06d}.hdf5",
        )
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            raise FileNotFoundError(f"No HDF5 source found for episode {episode_index}")
        return path

    @staticmethod
    def _array(source: Any, candidates: tuple[str, ...], dtype: type[np.generic]) -> NDArray[Any] | None:
        dataset = next((source[candidate] for candidate in candidates if candidate in source), None)
        return np.asarray(dataset[:], dtype=dtype) if dataset is not None else None


def _source_files(root: Path) -> tuple[SourceFileIdentity, ...]:
    files = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        payload = path.read_bytes()
        files.append(
            SourceFileIdentity(
                relative_path=path.relative_to(root).as_posix(),
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    return tuple(files)


def _source_identity(
    dataset_id: str,
    episode_index: int,
    source_format: str,
    format_version: str,
    files: tuple[SourceFileIdentity, ...],
) -> SourceIdentity:
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.relative_path.encode())
        digest.update(str(file.size_bytes).encode())
        digest.update(file.sha256.encode())
    return SourceIdentity(
        dataset_id=dataset_id,
        episode_index=episode_index,
        source_format=source_format,
        format_version=format_version,
        source_digest=digest.hexdigest(),
        files=files,
    )


def _arrow_dtype(data_type: pa.DataType) -> str:
    if pa.types.is_boolean(data_type):
        return "bool"
    if pa.types.is_float32(data_type):
        return "float32"
    if pa.types.is_float64(data_type):
        return "float64"
    if pa.types.is_int64(data_type):
        return "int64"
    if pa.types.is_int32(data_type):
        return "int32"
    if pa.types.is_string(data_type) or pa.types.is_large_string(data_type):
        return "string"
    return "object"


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value
