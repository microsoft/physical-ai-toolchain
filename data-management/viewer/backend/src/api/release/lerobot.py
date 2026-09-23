"""Client boundary for isolated LeRobot dataset processing."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..models.reviews import SourceIdentity

WorkerRunner = Callable[[Path], dict[str, Any]]


@dataclass(frozen=True)
class ReleaseEpisode:
    """One accepted episode prepared for native package writing."""

    source: SourceIdentity
    decision_id: str
    task: str
    frames: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ReleaseEpisodeReference:
    """One accepted source episode to stream through the worker."""

    source: SourceIdentity
    decision_id: str
    task: str


@dataclass(frozen=True)
class ReleaseReadback:
    """Semantic evidence obtained by reopening a written package."""

    episode_count: int
    frame_count: int
    features: tuple[str, ...]
    sampled_visual_frames: int


@dataclass(frozen=True)
class ReleaseWriteResult:
    """Deterministic mapping and verification evidence for a write."""

    episode_index_mapping: dict[int, int]
    accepted_decision_ids: tuple[str, ...]
    readback: ReleaseReadback


class ReleaseWorkerError(RuntimeError):
    """The isolated release worker rejected or failed an operation."""


class LeRobotReleaseAdapter:
    """Delegate LeRobot operations to the isolated release worker."""

    supports_streaming_v3 = True

    def __init__(self, runner: WorkerRunner | None = None) -> None:
        self._runner = runner or _run_worker

    def write_v3(
        self,
        *,
        episodes: tuple[ReleaseEpisode, ...],
        target_root: Path,
        repo_id: str,
        fps: int,
        features: dict[str, dict[str, Any]],
    ) -> ReleaseWriteResult:
        if not episodes:
            raise ValueError("At least one accepted episode is required")
        ordered = tuple(sorted(episodes, key=lambda episode: episode.source.episode_index))
        source_indices = [episode.source.episode_index for episode in ordered]
        if len(source_indices) != len(set(source_indices)):
            raise ValueError("Accepted episode source indices must be unique")

        with tempfile.TemporaryDirectory(prefix="dataviewer-release-") as request_directory:
            request_root = Path(request_directory)
            serialized_episodes = [
                _serialize_episode(episode, request_root=request_root, episode_offset=episode_offset)
                for episode_offset, episode in enumerate(ordered)
            ]
            response = self._invoke(
                request_root,
                {
                    "operation": "write-v3",
                    "target_root": str(target_root.resolve()),
                    "repo_id": repo_id,
                    "fps": fps,
                    "features": features,
                    "episodes": serialized_episodes,
                },
            )

        return _write_result(response)

    def copy_v3(
        self,
        *,
        source_root: Path,
        episodes: tuple[ReleaseEpisodeReference, ...],
        target_root: Path,
        repo_id: str,
        fps: int,
        features: dict[str, dict[str, Any]],
    ) -> ReleaseWriteResult:
        """Stream selected v3 source episodes into a new v3 package."""
        if not episodes:
            raise ValueError("At least one accepted episode is required")
        ordered = tuple(sorted(episodes, key=lambda episode: episode.source.episode_index))
        source_indices = [episode.source.episode_index for episode in ordered]
        if len(source_indices) != len(set(source_indices)):
            raise ValueError("Accepted episode source indices must be unique")
        with tempfile.TemporaryDirectory(prefix="dataviewer-release-") as request_directory:
            response = self._invoke(
                Path(request_directory),
                {
                    "operation": "copy-v3",
                    "source_root": str(source_root.resolve()),
                    "source_repo_id": f"local/source-{repo_id.removeprefix('local/')}",
                    "target_root": str(target_root.resolve()),
                    "repo_id": repo_id,
                    "fps": fps,
                    "features": features,
                    "episodes": [
                        {
                            "source_episode_index": episode.source.episode_index,
                            "decision_id": episode.decision_id,
                            "task": episode.task,
                        }
                        for episode in ordered
                    ],
                },
            )
        return _write_result(response)

    def convert_v21(self, *, source_root: Path, workspace_root: Path, repo_id: str) -> Path:
        with tempfile.TemporaryDirectory(prefix="dataviewer-release-") as request_directory:
            response = self._invoke(
                Path(request_directory),
                {
                    "operation": "convert-v21",
                    "source_root": str(source_root.resolve()),
                    "workspace_root": str(workspace_root.resolve()),
                    "repo_id": repo_id,
                },
            )
        return Path(response["converted_root"])

    def _invoke(self, request_root: Path, request: dict[str, Any]) -> dict[str, Any]:
        request_path = request_root / "request.json"
        request_path.write_text(json.dumps(request, separators=(",", ":")), encoding="utf-8")
        response = self._runner(request_path)
        if error := response.get("error"):
            error_type = error.get("type", "WorkerError")
            error_message = error.get("message", "Unknown worker error")
            raise ReleaseWorkerError(f"{error_type}: {error_message}")
        return response


def _write_result(response: dict[str, Any]) -> ReleaseWriteResult:
    readback = response["readback"]
    return ReleaseWriteResult(
        episode_index_mapping={
            int(source): int(target) for source, target in response["episode_index_mapping"].items()
        },
        accepted_decision_ids=tuple(response["accepted_decision_ids"]),
        readback=ReleaseReadback(
            episode_count=int(readback["episode_count"]),
            frame_count=int(readback["frame_count"]),
            features=tuple(readback["features"]),
            sampled_visual_frames=int(readback["sampled_visual_frames"]),
        ),
    )


def _serialize_episode(episode: ReleaseEpisode, *, request_root: Path, episode_offset: int) -> dict[str, Any]:
    frames: list[dict[str, Any]] = []
    for frame_offset, frame in enumerate(episode.frames):
        values: list[dict[str, str]] = []
        for value_offset, (feature_name, value) in enumerate(sorted(frame.items())):
            relative_path = Path("values") / f"{episode_offset}-{frame_offset}-{value_offset}.npy"
            value_path = request_root / relative_path
            value_path.parent.mkdir(parents=True, exist_ok=True)
            np.save(value_path, np.asarray(value), allow_pickle=False)
            values.append({"feature": feature_name, "path": relative_path.as_posix()})
        frames.append({"values": values})
    return {
        "source_episode_index": episode.source.episode_index,
        "decision_id": episode.decision_id,
        "task": episode.task,
        "frames": frames,
    }


def _run_worker(request_path: Path) -> dict[str, Any]:
    worker_root = Path(__file__).resolve().parents[4] / "release-worker"
    command = ["uv", "run", "--frozen", "--project", str(worker_root), "release-worker", str(request_path)]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as error:
        raise ReleaseWorkerError(f"Could not start release worker: {error}") from error
    response_path = request_path.with_name("response.json")
    try:
        response = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        message = f"Release worker returned no valid response (exit code {completed.returncode})"
        raise ReleaseWorkerError(message) from error
    if not isinstance(response, dict):
        raise ReleaseWorkerError("Release worker response must be a JSON object")
    if completed.returncode != 0 and "error" not in response:
        raise ReleaseWorkerError(f"Release worker failed with exit code {completed.returncode}")
    return response
