"""
LeRobot exporter: writes edited episodes of a LeRobot v3.0 dataset as a new v3.0 dataset.

The source dataset is never modified. Recorded features are copied unchanged, and trajectory
adjustments become the derived ``adjusted.observation.state`` and ``adjusted.observation.state_mask``
features. Removing or inserting frames renumbers the timeline so every timestamp stays
``frame_index / fps``, and LeRobot language annotation rows move with it; ``dataviewer-export.json``
records which source frame each output frame came from, the applied edits and the remapped subtasks,
which are also written as LeRobot ``subtask`` language rows. A saved language instruction can be written
as ``task_aug`` and ``plan`` rows.
Videos are decoded and re-encoded with the source's recorded encoder settings, and per-episode and
dataset statistics are recomputed.
"""

from __future__ import annotations

import contextlib
import copy
import errno
import json
import logging
import math
import os
import shutil
import stat
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numpy.typing import NDArray

from .episode_edits import (
    EpisodeEditOperations,
    ExportError,
    ExportProgress,
    ExportResult,
    PlannedFrame,
    ProgressCallback,
    apply_trajectory_adjustments,
    output_indices,
    plan_frames,
    remap_subtasks,
)
from .frame_interpolation import interpolate_image
from .image_transform import ImageTransform, ImageTransformError, apply_transform, get_output_dimensions
from .lerobot_language import (
    LANGUAGE_COLUMNS,
    LANGUAGE_EVENTS,
    LANGUAGE_FEATURE,
    LANGUAGE_PERSISTENT,
    SUBTASK_STYLE,
    LanguageInstruction,
    episode_persistent_rows,
    instruction_rows,
    plan_events,
    subtask_rows,
)
from .lerobot_loader import LeRobotDatasetInfo, LeRobotLoader, LeRobotLoaderError

logger = logging.getLogger(__name__)

STATE_FEATURE = "observation.state"
ADJUSTED_STATE = "adjusted.observation.state"
ADJUSTED_STATE_MASK = "adjusted.observation.state_mask"
PROVENANCE_FILE = "dataviewer-export.json"
# An export locks its destination through this file before creating anything else there, and holds the lock
# until it's done. Only the lock holder creates the claim, so a claim found under the lock is a stopped export's.
LOCK_FILE = ".dataviewer-export.lock"
CLAIM_DIRECTORY = ".dataviewer-export.partial"
DATA_PATH = "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
VIDEO_PATH = "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"

_STAGING = "dataset"
_RECORD = "publication.json"
_LOCK_ATTEMPTS = 3
_SUPPORTED_VERSION = "v3.0"
_TIMELINE = ("timestamp", "frame_index", "episode_index", "index")
_STATS_DTYPES = {"float16", "float32", "float64", "int8", "int16", "int32", "int64", "uint8", "uint16", "bool"}
_ENCODERS = {
    "av1": "libsvtav1",
    "libsvtav1": "libsvtav1",
    "h264": "libx264",
    "libx264": "libx264",
    "hevc": "libx265",
    "libx265": "libx265",
}
_ENCODER_DEFAULTS = {"g": 2, "crf": 30}
_LIBSVTAV1_DEFAULT_PRESET = 12
_QUANTILES = (1, 10, 50, 90, 99)
_IMAGE_SAMPLES = 100
_IMAGE_STATS_SIZE = 150


class LeRobotExportError(ExportError):
    """Exception raised for LeRobot export failures."""


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        """Lock the file without waiting, returning ``False`` while another export holds it."""
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EDEADLOCK):
                return False
            raise
        return True

    def _release_lock(fd: int, path: Path) -> None:
        """Unlock and close, then remove the lock file unless another export has it open."""
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)
        with contextlib.suppress(OSError):
            path.unlink()

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        """Lock the file without waiting, returning ``False`` while another export holds it."""
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _release_lock(fd: int, path: Path) -> None:
        """Remove the lock file while still holding it, then close it."""
        try:
            path.unlink(missing_ok=True)
        finally:
            os.close(fd)


@dataclass
class _Episode:
    """One requested episode: its source rows, metadata record, edits and output frame plan."""

    source_index: int
    output_index: int
    table: pa.Table
    record: dict[str, Any]
    edits: EpisodeEditOperations | None
    plan: list[PlannedFrame]
    offset: int = 0
    windows: dict[str, tuple[float, float]] = field(default_factory=dict)
    image_samples: dict[str, list[NDArray[np.uint8]]] = field(default_factory=dict)
    language: LanguageInstruction | None = None

    @property
    def length(self) -> int:
        return len(self.plan)

    def transform(self, camera: str) -> ImageTransform | None:
        if self.edits is None:
            return None
        return (self.edits.camera_transforms or {}).get(camera, self.edits.global_transform)


class _EpisodeFrames:
    """Decode one episode's frames in order from its time window of a source video."""

    def __init__(self, path: Path, window: tuple[float, float], fps: float) -> None:
        self._container = av.open(str(path))
        self._stream = self._container.streams.video[0]
        self._start, self._end = window
        self._tolerance = 0.5 / fps
        self._frames = self._decode()
        self._decoded = 0
        self._cache: dict[int, NDArray[np.uint8]] = {}

    def _decode(self) -> Iterator[NDArray[np.uint8]]:
        if self._start > 0 and self._stream.time_base is not None:
            self._container.seek(int(self._start / self._stream.time_base), stream=self._stream, backward=True)
        for frame in self._container.decode(self._stream):
            if frame.time is None or frame.time < self._start - self._tolerance:
                continue
            if frame.time >= self._end - self._tolerance:
                return
            yield frame.to_ndarray(format="rgb24")

    def get(self, index: int, keep_from: int) -> NDArray[np.uint8]:
        """Return source frame ``index``, keeping only frames at or after ``keep_from`` in memory."""
        self._cache = {key: value for key, value in self._cache.items() if key >= keep_from}
        while self._decoded <= index:
            frame = next(self._frames, None)
            if frame is None:
                raise LeRobotExportError(f"video ends after {self._decoded} frames, before frame {index}")
            if self._decoded == index:
                self._cache[index] = frame
            self._decoded += 1
        return self._cache[index]

    def finish(self, length: int) -> None:
        """Fail unless the window holds exactly ``length`` frames."""
        remaining = sum(1 for _ in self._frames)
        if self._decoded + remaining != length:
            raise LeRobotExportError(f"video window holds {self._decoded + remaining} frames for {length} data rows")

    def close(self) -> None:
        self._container.close()


class _VideoOutput:
    """Encode one camera's output video with the source feature's recorded encoder settings."""

    def __init__(self, path: Path, video_info: dict[str, Any], size: tuple[int, int], fps: float) -> None:
        codec = str(video_info.get("video.codec", ""))
        encoder = _ENCODERS.get(codec)
        if encoder is None:
            raise LeRobotExportError(f"video codec {codec!r} cannot be re-encoded")
        recorded = {key: video_info.get(f"video.{key}") for key in _ENCODER_DEFAULTS}
        # Settings the source did not record fall back to LeRobot's encoder defaults.
        self.settings: dict[str, Any] = {
            key: default if recorded[key] is None else recorded[key] for key, default in _ENCODER_DEFAULTS.items()
        }
        preset = video_info.get("video.preset")
        if preset is None and encoder == "libsvtav1":
            preset = _LIBSVTAV1_DEFAULT_PRESET
        if preset is not None and isinstance(preset, int) == (encoder == "libsvtav1"):
            self.settings["preset"] = preset
        options = {key: str(value) for key, value in self.settings.items()}
        rate = Fraction(fps).limit_denominator(1001)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._container = av.open(str(path), "w")
        self._stream = self._container.add_stream(encoder, rate=rate, options=options)
        self._stream.width, self._stream.height = size
        self._stream.pix_fmt = str(video_info.get("video.pix_fmt", "yuv420p"))
        self._time_base = 1 / rate
        self.frames = 0

    def write(self, image: NDArray[np.uint8]) -> None:
        frame = av.VideoFrame.from_ndarray(image, format="rgb24")
        frame.pts = self.frames
        frame.time_base = self._time_base
        for packet in self._stream.encode(frame):
            self._container.mux(packet)
        self.frames += 1

    def close(self) -> None:
        for packet in self._stream.encode():
            self._container.mux(packet)
        self._container.close()


def _numeric_stats(values: NDArray[np.float64]) -> dict[str, Any]:
    matrix = values.reshape(len(values), -1)
    stats: dict[str, Any] = {
        "min": matrix.min(axis=0).tolist(),
        "max": matrix.max(axis=0).tolist(),
        "mean": matrix.mean(axis=0).tolist(),
        "std": matrix.std(axis=0).tolist(),
        "count": [len(matrix)],
    }
    stats.update({f"q{q:02d}": np.quantile(matrix, q / 100, axis=0).tolist() for q in _QUANTILES})
    return stats


def _image_stats(samples: list[NDArray[np.uint8]]) -> dict[str, Any]:
    """Per-channel stats as ``(3, 1, 1)`` lists over ``[0, 1]`` pixels of the sampled frames."""
    pixels = np.concatenate([sample.reshape(-1, sample.shape[-1]) for sample in samples]).astype(np.float64) / 255

    def channels(vector: NDArray[np.float64]) -> list[list[list[float]]]:
        return [[[float(value)]] for value in vector]

    stats: dict[str, Any] = {
        "min": channels(pixels.min(axis=0)),
        "max": channels(pixels.max(axis=0)),
        "mean": channels(pixels.mean(axis=0)),
        "std": channels(pixels.std(axis=0)),
        "count": [len(samples)],
    }
    stats.update({f"q{q:02d}": channels(np.quantile(pixels, q / 100, axis=0)) for q in _QUANTILES})
    return stats


def _is_vector(data_type: pa.DataType) -> bool:
    return pa.types.is_fixed_size_list(data_type) or pa.types.is_list(data_type) or pa.types.is_large_list(data_type)


def _element_type(data_type: pa.DataType) -> pa.DataType:
    return data_type.value_type if _is_vector(data_type) else data_type


def _matrix(array: pa.Array) -> NDArray[np.float64]:
    """Return a numeric column, scalar or vector, as a ``(rows, width)`` float64 matrix."""
    if pa.types.is_fixed_size_list(array.type):
        width = array.type.list_size
    elif _is_vector(array.type):
        lengths = set(array.value_lengths().to_numpy(zero_copy_only=False).tolist())
        if len(lengths) > 1:
            raise LeRobotExportError("a vector column has rows of different lengths")
        width = lengths.pop() if lengths else 0
    else:
        return array.to_numpy(zero_copy_only=False).astype(np.float64).reshape(len(array), 1)
    return array.flatten().to_numpy(zero_copy_only=False).astype(np.float64).reshape(len(array), width)


def _from_matrix(matrix: NDArray[np.float64], data_type: pa.DataType) -> pa.Array:
    """Rebuild a column of ``data_type``, keeping its list layout, from a ``(rows, width)`` matrix."""
    element = _element_type(data_type)
    if not _is_vector(data_type):
        return pa.array(matrix[:, 0].astype(element.to_pandas_dtype()), type=data_type)
    values = pa.array(matrix.reshape(-1).astype(element.to_pandas_dtype()), type=element)
    if pa.types.is_fixed_size_list(data_type):
        return pa.FixedSizeListArray.from_arrays(values, type=data_type)
    large = pa.types.is_large_list(data_type)
    offsets = pa.array(np.arange(0, matrix.size + 1, matrix.shape[1]), type=pa.int64() if large else pa.int32())
    return (pa.LargeListArray if large else pa.ListArray).from_arrays(offsets, values, type=data_type)


def _is_floating(data_type: pa.DataType) -> bool:
    return pa.types.is_floating(_element_type(data_type))


def _inside(root: Path, relative: str) -> Path:
    """Return ``root / relative``, refusing any path that would land outside ``root``."""
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise LeRobotExportError(f"export path {relative!r} would leave the output directory")
    return path


def admits_export(directory: Path) -> bool:
    """Return whether a LeRobot export may go on to lock ``directory``.

    A missing or empty directory qualifies, and so does one holding only an export's lock file or holding a
    claim: the export that gets the lock then recovers what a stopped export left, or refuses. Anything else,
    without a claim, rules the directory out.
    """
    if not directory.exists():
        return True
    if not directory.is_dir():
        return False
    names = {entry.name for entry in directory.iterdir()}
    return CLAIM_DIRECTORY in names or names <= {LOCK_FILE}


def _make_destination(directory: Path) -> bool:
    """Create the destination if it's missing, returning whether this export created it."""
    if directory.is_dir():
        return False
    try:
        directory.mkdir(parents=True)
    except FileExistsError:
        if directory.is_dir():
            return False
        raise LeRobotExportError("the output directory must be new or empty") from None
    return True


def _lock(directory: Path) -> int:
    """Lock the destination through its lock file and return the descriptor that holds the lock.

    An export that doesn't get the lock closes the file without removing it, even one it created,
    because another export may hold the lock on that same file.
    """
    path = directory / LOCK_FILE
    for _ in range(_LOCK_ATTEMPTS):
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as error:
            raise LeRobotExportError(f"the output directory can't be locked: {error}") from error
        try:
            if not _try_lock(fd):
                raise LeRobotExportError("another export is writing to this directory")
            # The previous holder may have removed the file after this export opened it.
            try:
                same = os.path.samestat(os.stat(path), os.fstat(fd))
            except FileNotFoundError:
                same = False
        except OSError as error:
            os.close(fd)
            raise LeRobotExportError(f"the output directory can't be locked: {error}") from error
        except BaseException:
            os.close(fd)
            raise
        if same:
            return fd
        os.close(fd)
    raise LeRobotExportError("another export is writing to this directory")


@dataclass(frozen=True)
class _Recorded:
    """A staged file or directory, with the identity it keeps when publication moves it.

    Files also record their size and modification time, because a deleted file's inode number can be reused.
    """

    path: str
    directory: bool
    dev: int
    ino: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class _Publication:
    """What publishing a staged dataset moves: its top-level entries in move order, and everything beneath them."""

    moves: list[str]
    entries: list[_Recorded]


def _describe(staging: Path) -> _Publication:
    """Describe the staged dataset: its top-level entries with ``meta`` last, and every file and directory."""
    moves = sorted((entry.name for entry in staging.iterdir()), key=lambda name: (name == "meta", name))
    entries = []
    for root, directories, files in os.walk(staging):
        for name in (*directories, *files):
            path = Path(root, name)
            status = path.lstat()
            entries.append(
                _Recorded(
                    path.relative_to(staging).as_posix(),
                    stat.S_ISDIR(status.st_mode),
                    status.st_dev,
                    status.st_ino,
                    status.st_size,
                    status.st_mtime_ns,
                )
            )
    return _Publication(moves, entries)


def _write_record(claim: Path, publication: _Publication) -> None:
    """Write the publication record into the claim atomically, so a stopped export leaves it whole or absent."""
    temporary = claim / f"{_RECORD}.tmp"
    temporary.write_text(
        json.dumps({"moves": publication.moves, "entries": [vars(entry) for entry in publication.entries]})
    )
    os.replace(temporary, claim / _RECORD)


def _read_record(claim: Path) -> _Publication | None:
    """Return a claim's publication record, or ``None`` when it's missing or can't be read."""
    try:
        raw = json.loads((claim / _RECORD).read_text())
        return _Publication(
            [str(name) for name in raw["moves"]],
            [
                _Recorded(
                    str(entry["path"]),
                    bool(entry["directory"]),
                    int(entry["dev"]),
                    int(entry["ino"]),
                    int(entry["size"]),
                    int(entry["mtime_ns"]),
                )
                for entry in raw["entries"]
            ],
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _matches(status: os.stat_result, entry: _Recorded) -> bool:
    """Return whether a file system entry is the recorded one rather than a replacement that reused its inode."""
    if stat.S_ISDIR(status.st_mode) != entry.directory or (status.st_dev, status.st_ino) != (entry.dev, entry.ino):
        return False
    return entry.directory or (status.st_size, status.st_mtime_ns) == (entry.size, entry.mtime_ns)


def _owned(directory: Path, entry: _Recorded, recorded: dict[str, _Recorded]) -> bool:
    """Return whether ``entry`` and every recorded directory above it are still the recorded ones."""
    parts = PurePosixPath(entry.path).parts
    for depth in range(1, len(parts) + 1):
        relative = "/".join(parts[:depth])
        expected = recorded.get(relative)
        if expected is None:
            return False
        try:
            status = (directory / relative).lstat()
        except OSError:
            return False
        if not _matches(status, expected):
            return False
    return True


def _remove_recorded(directory: Path, publication: _Publication) -> None:
    """Remove recorded files still in ``directory`` with their recorded identities, then the directories that empties.

    A file someone added or replaced keeps its directory, and with it the directories above, in place. Any other
    error propagates, so the caller keeps the claim and its record for another attempt.
    """
    recorded = {entry.path: entry for entry in publication.entries}
    for entry in publication.entries:
        if not entry.directory and _owned(directory, entry, recorded):
            (directory / entry.path).unlink()
    for entry in sorted(publication.entries, key=lambda entry: entry.path.count("/"), reverse=True):
        if entry.directory and _owned(directory, entry, recorded):
            try:
                (directory / entry.path).rmdir()
            except OSError as error:
                if error.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
                    raise


def _left_in_place(directory: Path, publication: _Publication) -> bool:
    """Return whether anything the publication moved may still be in ``directory`` with its recorded identity.

    An entry that can't be checked counts as still in place, so the claim and its record stay.
    """
    recorded = {entry.path: entry for entry in publication.entries}
    for name in publication.moves:
        if name not in recorded:
            continue
        try:
            status = (directory / name).lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        if _matches(status, recorded[name]):
            return True
    return False


def _published(directory: Path, publication: _Publication) -> bool:
    """Return whether the publication's last move, ``meta``, reached the directory, so it completed."""
    recorded = {entry.path: entry for entry in publication.entries}
    last = recorded.get(publication.moves[-1]) if publication.moves else None
    return last is not None and _owned(directory, last, recorded)


def _log_cleanup_error(function: Callable[..., object], path: str, error: BaseException) -> None:
    """Log what the export's cleanup couldn't remove, so ``shutil.rmtree`` carries on with the rest."""
    if not isinstance(error, FileNotFoundError):
        logger.warning("Couldn't remove %s after an export", path, exc_info=error)


def _recover(directory: Path) -> None:
    """Remove what an export that stopped partway left in a directory this export has locked.

    Without a readable record, only the claim goes. When the record's last move is in place, the publication
    completed and its dataset stays. Otherwise the stopped export's moved files go wherever their recorded
    identities still match, along with the directories that empties; everything else stays.
    """
    claim = directory / CLAIM_DIRECTORY
    if not claim.is_dir() or claim.is_symlink():
        return
    publication = _read_record(claim)
    if publication is not None and not _published(directory, publication):
        _remove_recorded(directory, publication)
    shutil.rmtree(claim)


def _move_into(staging: Path, directory: Path, publication: _Publication) -> None:
    """Move the staged entries into the directory in the recorded order, without replacing any.

    If a move fails, what this export already moved is removed with the identity checks recovery uses.
    """
    try:
        for name in publication.moves:
            target = directory / name
            if target.exists() or target.is_symlink():
                raise LeRobotExportError(f"{name!r} already exists in the output directory")
            (staging / name).rename(target)
    except Exception:
        _remove_recorded(directory, publication)
        raise
    staging.rmdir()


def _interpolate(matrix: NDArray[np.float64], plan: list[PlannedFrame]) -> NDArray[np.float64]:
    """Lay out source rows in plan order, blending inserted rows between their kept neighbors."""
    rows = matrix[[frame.source for frame in plan]].copy()
    for position, frame in enumerate(plan):
        if frame.following is not None:
            rows[position] = (1 - frame.factor) * matrix[frame.source] + frame.factor * matrix[frame.following]
    return rows


def _planned_column(array: pa.Array, plan: list[PlannedFrame]) -> pa.Array:
    """Interpolate floating-point columns and hold every other column from the earlier kept frame."""
    if _is_floating(array.type) and any(frame.following is not None for frame in plan):
        return _from_matrix(_interpolate(_matrix(array), plan), array.type)
    return array.take(pa.array([frame.source for frame in plan], type=pa.int64()))


def _timestamps(table: pa.Table, fps: float) -> list[float]:
    """Return a table's per-frame timestamps, or ``frame_index / fps`` when it records none."""
    if "timestamp" in table.column_names:
        return [float(value) for value in table.column("timestamp").to_pylist()]
    return [index / fps for index in range(table.num_rows)]


def _recorded_language(source: pa.Table, recorded: set[str]) -> tuple[list[dict[str, Any]], list[Any]]:
    """Return a source episode's persistent rows and per-frame events, empty where it records none."""
    rows = (source.column(LANGUAGE_PERSISTENT)[0].as_py() or []) if LANGUAGE_PERSISTENT in recorded else []
    events = source.column(LANGUAGE_EVENTS).to_pylist() if LANGUAGE_EVENTS in recorded else [None] * source.num_rows
    return rows, events


def _transform_record(transform: ImageTransform | None) -> dict[str, Any] | None:
    if transform is None:
        return None
    return {
        "crop": vars(transform.crop) if transform.crop else None,
        "resize": vars(transform.resize) if transform.resize else None,
    }


class LeRobotExporter:
    """
    Exports episodes of a LeRobot v3.0 dataset, with edits applied, as a new LeRobot v3.0 dataset.

    The output directory must be new or empty. The export locks it before writing, stages the dataset in a
    hidden claim inside it and then moves the dataset into place, so an existing directory, such as a mount
    point, is kept and nothing is written beside it. A second export to the directory fails while the lock is
    held, and a failed export removes what it created. Before moving anything, the export records every staged
    file's identity and moves ``meta`` last. The next export to the directory uses that record to clean up after
    one that stopped partway, removing only what that export had moved, and keeps a dataset whose publication
    completed.

    Example:
        >>> exporter = LeRobotExporter("/data/capture/lerobot", "/data/capture-edited", dataset_id="capture--lerobot")
        >>> result = exporter.export_episodes([0], edits_map={0: edits})
    """

    def __init__(self, src_path: str | Path, dst_path: str | Path, dataset_id: str = "") -> None:
        self.src_path = Path(src_path)
        self.dst_path = Path(dst_path)
        self.dataset_id = dataset_id
        self.loader = LeRobotLoader(self.src_path)

    def export_episodes(
        self,
        episode_indices: list[int],
        edits_map: dict[int, EpisodeEditOperations] | None = None,
        progress_callback: ProgressCallback | None = None,
        language: dict[int, LanguageInstruction] | None = None,
    ) -> ExportResult:
        """
        Export the requested episodes, renumbered from 0, into one derived dataset.

        Args:
            episode_indices: Source episode indices, in output order.
            edits_map: Edit operations by source episode index.
            progress_callback: Optional callback for progress updates.
            language: Saved language instructions by source episode index, written as ``task_aug`` and ``plan`` rows.

        Returns:
            ExportResult with the dataset directory and aggregate statistics.
        """
        started = datetime.now(UTC)
        lock: int | None = None
        claim: Path | None = None
        publication: _Publication | None = None
        created = published = False
        try:
            info = self.loader.get_dataset_info()
            if info.codebase_version != _SUPPORTED_VERSION:
                raise LeRobotExportError(
                    f"LeRobot export supports {_SUPPORTED_VERSION} datasets, not {info.codebase_version}"
                )
            if len(set(episode_indices)) != len(episode_indices):
                raise LeRobotExportError("each episode can be exported only once")
            if not admits_export(self.dst_path):
                raise LeRobotExportError("the output directory must be new or empty")
            episodes = self._episodes(info, episode_indices, edits_map or {}, language or {})
            sizes = self._video_sizes(info, episodes)
            created = _make_destination(self.dst_path)
            lock = _lock(self.dst_path)
            _recover(self.dst_path)
            if any(entry.name != LOCK_FILE for entry in self.dst_path.iterdir()):
                raise LeRobotExportError("the output directory must be new or empty")
            # A plain mkdir applies the process umask, unlike mkdtemp's fixed 0700.
            (self.dst_path / CLAIM_DIRECTORY).mkdir()
            claim = self.dst_path / CLAIM_DIRECTORY
            staging = claim / _STAGING
            staging.mkdir()
            self._write(staging, info, episodes, sizes, started, progress_callback)
            publication = _describe(staging)
            _write_record(claim, publication)
            _move_into(staging, self.dst_path, publication)
            published = True
            removed = sum(
                len({frame for frame in (episode.edits.removed_frames or set()) if 0 <= frame < episode.table.num_rows})
                for episode in episodes
                if episode.edits
            )
            return ExportResult(
                success=True,
                output_files=[str(self.dst_path)],
                stats={
                    "total_episodes": len(episodes),
                    "total_frames": sum(episode.length for episode in episodes),
                    "removed_frames": removed,
                    "duration_ms": (datetime.now(UTC) - started).total_seconds() * 1000,
                },
            )
        except (ExportError, LeRobotLoaderError, ImageTransformError) as error:
            return ExportResult(success=False, output_files=[], error=str(error))
        except Exception as error:
            return ExportResult(success=False, output_files=[], error=f"Unexpected error: {error}")
        finally:
            # Only the lock holder removes anything, and the claim stays while entries this export moved do.
            if lock is not None:
                if claim is not None and (
                    published or publication is None or not _left_in_place(self.dst_path, publication)
                ):
                    shutil.rmtree(claim, onexc=_log_cleanup_error)
                # _release_lock closes the descriptor even when it raises, so the export keeps its own result.
                try:
                    _release_lock(lock, self.dst_path / LOCK_FILE)
                except OSError:
                    logger.warning("Cleaning up the export lock in %s failed", self.dst_path, exc_info=True)
                if created and not published:
                    with contextlib.suppress(OSError):
                        self.dst_path.rmdir()

    def _episodes(
        self,
        info: LeRobotDatasetInfo,
        episode_indices: list[int],
        edits_map: dict[int, EpisodeEditOperations],
        language: dict[int, LanguageInstruction],
    ) -> list[_Episode]:
        unsupported = sorted(name for name, feature in info.features.items() if feature.get("dtype") == "image")
        if unsupported:
            raise LeRobotExportError(f"LeRobot export supports video features only, not image features {unsupported}")
        episodes = []
        offset = 0
        for output_index, source_index in enumerate(episode_indices):
            record = self.loader.episode_record(source_index)
            if record is None:
                raise LeRobotExportError(f"episode {source_index} has no meta/episodes record")
            table = self.loader.load_episode_table(source_index)
            edits = edits_map.get(source_index)
            plan = plan_frames(
                table.num_rows,
                edits.removed_frames if edits else None,
                edits.inserted_frames if edits else None,
            )
            if not plan:
                raise LeRobotExportError(f"episode {source_index} has no frames left after its edits")
            episodes.append(
                _Episode(
                    source_index, output_index, table, record, edits, plan, offset, language=language.get(source_index)
                )
            )
            offset += len(plan)
        return episodes

    def _video_sizes(self, info: LeRobotDatasetInfo, episodes: list[_Episode]) -> dict[str, tuple[int, int]]:
        """Return each camera's output (width, height), identical across the exported episodes."""
        sizes = {}
        for camera, feature in info.features.items():
            if feature.get("dtype") != "video":
                continue
            if camera in {"", ".", ".."} or "/" in camera or "\\" in camera:
                raise LeRobotExportError(f"video feature key {camera!r} is not a plain name")
            video_info = feature.get("info", {})
            if video_info.get("video.is_depth_map") or video_info.get("is_depth_map"):
                raise LeRobotExportError(f"depth video {camera} cannot be exported")
            height, width = int(feature["shape"][0]), int(feature["shape"][1])
            outputs = set()
            for episode in episodes:
                transform = episode.transform(camera)
                outputs.add(get_output_dimensions((width, height), transform) if transform else (width, height))
            if len(outputs) != 1:
                raise LeRobotExportError(f"camera {camera} would have different sizes across the exported episodes")
            size = outputs.pop()
            if str(video_info.get("video.pix_fmt", "yuv420p")).startswith("yuv420") and (size[0] % 2 or size[1] % 2):
                raise LeRobotExportError(
                    f"camera {camera} output {size[0]}x{size[1]} must have an even width and height"
                )
            sizes[camera] = size
        return sizes

    def _write(
        self,
        root: Path,
        info: LeRobotDatasetInfo,
        episodes: list[_Episode],
        sizes: dict[str, tuple[int, int]],
        started: datetime,
        progress_callback: ProgressCallback | None,
    ) -> None:
        adjusted = any(episode.edits and episode.edits.trajectory_adjustments for episode in episodes)
        if adjusted and STATE_FEATURE not in info.features:
            raise LeRobotExportError(f"trajectory adjustments need an {STATE_FEATURE} feature")
        tables = [self._episode_table(info, episode, adjusted) for episode in episodes]
        encoders = self._write_videos(root, info, episodes, sizes, progress_callback)

        language = self._language_columns(info, episodes, tables)
        data = pa.concat_tables(tables)
        for name, column in language.items():
            data = data.append_column(name, column)
        data_path = _inside(root, DATA_PATH.format(chunk_index=0, file_index=0))
        data_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(data, data_path)

        features = self._features(info, sizes, encoders, adjusted, list(language))
        stats_features = [
            name
            for name, feature in features.items()
            if feature.get("dtype") in _STATS_DTYPES and name in data.column_names
        ]
        rows = [
            self._episode_row(info, episode, table, stats_features)
            for episode, table in zip(episodes, tables, strict=True)
        ]
        episodes_path = root / "meta/episodes/chunk-000/file-000.parquet"
        episodes_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(rows), episodes_path)

        stats: dict[str, Any] = {
            name: _numeric_stats(_matrix(data.column(name).combine_chunks())) for name in stats_features
        }
        for camera in sizes:
            stats[camera] = _image_stats([sample for episode in episodes for sample in episode.image_samples[camera]])
        (root / "meta/stats.json").write_text(json.dumps(stats, indent=4))

        raw = copy.deepcopy(info.raw_info)
        raw.update(
            features=features,
            total_episodes=len(episodes),
            total_frames=data.num_rows,
            splits={"train": f"0:{len(episodes)}"},
            data_path=DATA_PATH,
            video_path=VIDEO_PATH,
        )
        (root / "meta/info.json").write_text(json.dumps(raw, indent=4))
        shutil.copyfile(self.src_path / "meta/tasks.parquet", root / "meta/tasks.parquet")
        (root / PROVENANCE_FILE).write_text(json.dumps(self._provenance(info, episodes, started), indent=2))

    def _episode_table(self, info: LeRobotDatasetInfo, episode: _Episode, adjusted: bool) -> pa.Table:
        """Lay out one episode's output rows with a renumbered timeline and optional derived state."""
        source = episode.table
        plan = episode.plan
        length = len(plan)
        regenerated = {
            "timestamp": np.arange(length) / info.fps,
            "frame_index": np.arange(length),
            "episode_index": np.full(length, episode.output_index),
            "index": episode.offset + np.arange(length),
        }
        columns = {}
        for name in source.column_names:
            if name in LANGUAGE_COLUMNS:
                continue
            column = source.column(name).combine_chunks()
            if name in regenerated:
                columns[name] = pa.array(regenerated[name].astype(column.type.to_pandas_dtype()), type=column.type)
            else:
                columns[name] = _planned_column(column, plan)
        if adjusted:
            state = source.column(STATE_FEATURE).combine_chunks()
            dtype = _element_type(state.type).to_pandas_dtype()
            recorded = _matrix(state)
            adjustments = episode.edits.trajectory_adjustments if episode.edits else None
            changed = apply_trajectory_adjustments(recorded, adjustments) if adjustments else recorded
            planned_recorded = _interpolate(recorded, plan).astype(dtype)
            planned_adjusted = _interpolate(changed, plan).astype(dtype)
            columns[ADJUSTED_STATE] = _from_matrix(planned_adjusted, state.type)
            columns[ADJUSTED_STATE_MASK] = pa.array(np.any(planned_adjusted != planned_recorded, axis=1))
        return pa.table(columns)

    @staticmethod
    def _language_columns(
        info: LeRobotDatasetInfo, episodes: list[_Episode], tables: list[pa.Table]
    ) -> dict[str, pa.Array]:
        """Return language columns on the output frames: the source's rows moved with the edits, plus added rows."""
        recorded = {name for name in LANGUAGE_COLUMNS if all(name in e.table.column_names for e in episodes)}
        written, persistent, events = set(recorded), [], []
        for episode, table in zip(episodes, tables, strict=True):
            output_times = _timestamps(table, info.fps)
            # A subtask list, even an empty one, replaces the recorded subtasks; no list keeps them.
            edited = episode.edits.subtasks if episode.edits else None
            added = subtask_rows(edited or [], episode.plan, output_times)
            replaced = {SUBTASK_STYLE} if edited is not None else set()
            if episode.language is not None:
                instruction = instruction_rows(episode.language, output_times[0])
                added += instruction
                replaced |= {row["style"] for row in instruction}
            if added:
                written.update(LANGUAGE_COLUMNS)
            rows, frame_events = _recorded_language(episode.table, recorded)
            source_times = _timestamps(episode.table, info.fps)
            planned = episode_persistent_rows(rows, added, replaced, source_times, episode.plan, output_times)
            persistent.extend([planned] * episode.length)
            events.extend(plan_events(frame_events, episode.plan))
        columns = {LANGUAGE_PERSISTENT: persistent, LANGUAGE_EVENTS: events}
        # Types are inferred from the rows, as lerobot's writer does; its JSON element type can't be built from Python.
        return {name: pa.array(columns[name]) for name in LANGUAGE_COLUMNS if name in written}

    def _write_videos(
        self,
        root: Path,
        info: LeRobotDatasetInfo,
        episodes: list[_Episode],
        sizes: dict[str, tuple[int, int]],
        progress_callback: ProgressCallback | None,
    ) -> dict[str, dict[str, Any]]:
        """Encode every camera's video and return the encoder settings used for each."""
        total = max(1, sum(episode.length for episode in episodes) * len(sizes))
        written = 0
        encoders = {}
        for camera, size in sizes.items():
            video_info = info.features[camera].get("info", {})
            path = _inside(root, VIDEO_PATH.format(video_key=camera, chunk_index=0, file_index=0))
            output = _VideoOutput(path, video_info, size, info.fps)
            encoders[camera] = output.settings
            try:
                for episode in episodes:
                    source = self.loader.get_video_path(episode.source_index, camera)
                    window = (
                        float(episode.record.get(f"videos/{camera}/from_timestamp", 0.0)),
                        float(episode.record.get(f"videos/{camera}/to_timestamp", math.inf)),
                    )
                    if source is None:
                        raise LeRobotExportError(f"episode {episode.source_index} has no {camera} video")
                    start = output.frames / info.fps
                    self._write_episode_video(output, source, window, info.fps, episode, camera)
                    episode.windows[camera] = (start, output.frames / info.fps)
                    written += episode.length
                    if progress_callback:
                        progress_callback(
                            ExportProgress(
                                current_episode=episode.source_index,
                                total_episodes=len(episodes),
                                current_frame=episode.length,
                                total_frames=episode.length,
                                percentage=5 + 90 * written / total,
                                status=f"Encoded {camera} for episode {episode.source_index}",
                            )
                        )
            finally:
                output.close()
        return encoders

    @staticmethod
    def _write_episode_video(
        output: _VideoOutput, source: Path, window: tuple[float, float], fps: float, episode: _Episode, camera: str
    ) -> None:
        transform = episode.transform(camera)
        sampled = set(np.linspace(0, episode.length - 1, num=min(episode.length, _IMAGE_SAMPLES)).round().astype(int))
        samples = episode.image_samples.setdefault(camera, [])
        frames = _EpisodeFrames(source, window, fps)
        try:
            for position, frame in enumerate(episode.plan):
                image = frames.get(frame.source, keep_from=frame.source)
                if frame.following is not None:
                    image = interpolate_image(image, frames.get(frame.following, keep_from=frame.source), frame.factor)
                if transform is not None:
                    image = apply_transform(image, transform)
                output.write(image)
                if position in sampled:
                    step = max(1, math.ceil(max(image.shape[:2]) / _IMAGE_STATS_SIZE))
                    samples.append(image[::step, ::step].copy())
            frames.finish(episode.table.num_rows)
        finally:
            frames.close()

    @staticmethod
    def _features(
        info: LeRobotDatasetInfo,
        sizes: dict[str, tuple[int, int]],
        encoders: dict[str, dict[str, Any]],
        adjusted: bool,
        language: list[str],
    ) -> dict[str, dict[str, Any]]:
        features = copy.deepcopy(info.features)
        for camera, (width, height) in sizes.items():
            feature = features[camera]
            feature["shape"] = [height, width, *feature["shape"][2:]]
            feature.setdefault("info", {}).update(
                {"video.height": height, "video.width": width}
                | {f"video.{key}": value for key, value in encoders[camera].items()}
            )
        if adjusted:
            state = features[STATE_FEATURE]
            features[ADJUSTED_STATE] = {"dtype": state["dtype"], "shape": state["shape"], "names": state.get("names")}
            features[ADJUSTED_STATE_MASK] = {"dtype": "bool", "shape": [1], "names": None}
        for name in language:
            features.setdefault(name, copy.deepcopy(LANGUAGE_FEATURE))
        return features

    @staticmethod
    def _episode_row(
        info: LeRobotDatasetInfo, episode: _Episode, table: pa.Table, stats_features: list[str]
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "episode_index": episode.output_index,
            "tasks": list(episode.record.get("tasks") or []),
            "length": episode.length,
            "data/chunk_index": 0,
            "data/file_index": 0,
            "dataset_from_index": episode.offset,
            "dataset_to_index": episode.offset + episode.length,
        }
        for camera, (start, end) in episode.windows.items():
            row.update(
                {
                    f"videos/{camera}/chunk_index": 0,
                    f"videos/{camera}/file_index": 0,
                    f"videos/{camera}/from_timestamp": start,
                    f"videos/{camera}/to_timestamp": end,
                }
            )
        row.update({"meta/episodes/chunk_index": 0, "meta/episodes/file_index": 0})
        for name in stats_features:
            for statistic, value in _numeric_stats(_matrix(table.column(name).combine_chunks())).items():
                row[f"stats/{name}/{statistic}"] = value
        for camera, samples in episode.image_samples.items():
            for statistic, value in _image_stats(samples).items():
                row[f"stats/{camera}/{statistic}"] = value
        return row

    def _provenance(self, info: LeRobotDatasetInfo, episodes: list[_Episode], started: datetime) -> dict[str, Any]:
        records = []
        for episode in episodes:
            edits = episode.edits
            length = episode.table.num_rows
            records.append(
                {
                    "episode_index": episode.output_index,
                    "source_episode_index": episode.source_index,
                    "source_frames": length,
                    "output_frames": episode.length,
                    "frame_sources": [frame.source if frame.following is None else None for frame in episode.plan],
                    "edits": {
                        "removed_frames": sorted(
                            frame for frame in (edits.removed_frames or set()) if 0 <= frame < length
                        )
                        if edits
                        else [],
                        "inserted_frames": [
                            {"after_frame_index": frame.source, "interpolation_factor": frame.factor}
                            for frame in episode.plan
                            if frame.following is not None
                        ],
                        "global_transform": _transform_record(edits.global_transform) if edits else None,
                        "camera_transforms": {
                            camera: _transform_record(transform)
                            for camera, transform in ((edits.camera_transforms or {}) if edits else {}).items()
                        },
                        "trajectory_adjustments": [
                            {
                                "frame_index": adjustment.frame_index,
                                "channel_deltas": adjustment.channel_deltas,
                                "channel_values": adjustment.channel_values,
                            }
                            for adjustment in ((edits.trajectory_adjustments or []) if edits else [])
                        ],
                        "subtasks": remap_subtasks(edits.subtasks, output_indices(episode.plan))
                        if edits and edits.subtasks is not None
                        else None,
                    },
                    "language_instruction": {
                        "annotator_id": episode.language.annotator_id,
                        "saved_at": episode.language.saved_at,
                    }
                    if episode.language
                    else None,
                }
            )
        return {
            "schema_version": 1,
            "exported_at": started.isoformat(),
            "source": {"dataset_id": self.dataset_id, "codebase_version": info.codebase_version, "fps": info.fps},
            "episodes": records,
        }
