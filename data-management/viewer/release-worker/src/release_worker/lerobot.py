"""Native LeRobot dataset writing, conversion, and semantic readback."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Generator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import av
import numpy as np
from lerobot.configs.video import RGBEncoderConfig
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from lerobot.scripts.convert_dataset_v21_to_v30 import convert_dataset


@dataclass(frozen=True)
class WorkerEpisode:
    """One accepted episode decoded from a worker request."""

    source_episode_index: int
    decision_id: str
    task: str
    frames: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class WorkerEpisodeReference:
    """One accepted source episode to stream into a release."""

    source_episode_index: int
    decision_id: str
    task: str


@dataclass(frozen=True)
class WorkerVisualSample:
    """One visual sample decoded during package read-back."""

    release_episode_index: int
    feature_name: str
    frame_index: int


@dataclass(frozen=True)
class WorkerReadback:
    """Semantic evidence obtained by reopening a written package."""

    episode_count: int
    frame_count: int
    episode_frame_counts: dict[int, int]
    features: tuple[str, ...]
    sampled_visual_frames: int
    visual_samples: tuple[WorkerVisualSample, ...]


@dataclass(frozen=True)
class WorkerWriteResult:
    """Deterministic mapping and verification evidence for a write."""

    episode_index_mapping: dict[int, int]
    accepted_decision_ids: tuple[str, ...]
    readback: WorkerReadback


class LeRobotReleaseWorker:
    """Materialize approved episodes through LeRobot's public APIs."""

    def write_v3(
        self,
        *,
        episodes: tuple[WorkerEpisode, ...],
        target_root: Path,
        repo_id: str,
        fps: int,
        features: dict[str, dict[str, Any]],
    ) -> WorkerWriteResult:
        if not episodes:
            raise ValueError("At least one accepted episode is required")
        ordered = tuple(sorted(episodes, key=lambda episode: episode.source_episode_index))
        source_indices = [episode.source_episode_index for episode in ordered]
        if len(source_indices) != len(set(source_indices)):
            raise ValueError("Accepted episode source indices must be unique")
        if target_root.exists():
            raise FileExistsError(target_root)

        dataset = LeRobotDataset.create(
            repo_id=repo_id,
            fps=fps,
            features=features,
            root=target_root,
            use_videos=any(specification.get("dtype") in {"image", "video"} for specification in features.values()),
            rgb_encoder=_rgb_encoder(features),
        )
        for episode in ordered:
            if not episode.frames:
                raise ValueError(f"Episode {episode.source_episode_index} contains no frames")
            for source_frame in episode.frames:
                frame = dict(source_frame)
                frame["task"] = episode.task
                dataset.add_frame(frame)
            dataset.save_episode()
        dataset.finalize()

        mapping = {source_index: target_index for target_index, source_index in enumerate(source_indices)}
        return WorkerWriteResult(
            episode_index_mapping=mapping,
            accepted_decision_ids=tuple(episode.decision_id for episode in ordered),
            readback=self.readback(target_root=target_root, repo_id=repo_id, episode_count=len(ordered)),
        )

    def copy_v3(
        self,
        *,
        source_root: Path,
        source_repo_id: str,
        episodes: tuple[WorkerEpisodeReference, ...],
        target_root: Path,
        repo_id: str,
        fps: int,
        features: dict[str, dict[str, Any]],
    ) -> WorkerWriteResult:
        """Copy selected v3 episodes while retaining at most one decoded frame."""
        if not source_root.is_dir():
            raise FileNotFoundError(source_root)
        if not episodes:
            raise ValueError("At least one accepted episode is required")
        ordered = tuple(sorted(episodes, key=lambda episode: episode.source_episode_index))
        source_indices = [episode.source_episode_index for episode in ordered]
        if len(source_indices) != len(set(source_indices)):
            raise ValueError("Accepted episode source indices must be unique")
        if target_root.exists():
            raise FileExistsError(target_root)

        target = LeRobotDataset.create(
            repo_id=repo_id,
            fps=fps,
            features=features,
            root=target_root,
            use_videos=any(specification.get("dtype") in {"image", "video"} for specification in features.values()),
            rgb_encoder=_rgb_encoder(features),
        )
        for episode in ordered:
            source = LeRobotDataset(
                repo_id=source_repo_id,
                root=source_root,
                episodes=[episode.source_episode_index],
                download_videos=False,
                return_uint8=True,
            )
            if not source:
                raise ValueError(f"Episode {episode.source_episode_index} contains no frames")
            video_iterators = {
                name: _iter_episode_video(source, episode.source_episode_index, name)
                for name, specification in features.items()
                if specification.get("dtype") == "video"
            }
            try:
                for frame_index in range(len(source)):
                    source_frame = source.hf_dataset[frame_index]
                    expected_timestamp = float(source_frame["timestamp"].item())
                    frame = {
                        name: _normalize_frame_value(source_frame[name], specification)
                        for name, specification in features.items()
                        if name not in video_iterators
                    }
                    for name, iterator in video_iterators.items():
                        try:
                            timestamp, value = next(iterator)
                        except StopIteration as error:
                            raise ValueError(
                                f"Video feature {name} ended before frame {frame_index} "
                                f"of episode {episode.source_episode_index}"
                            ) from error
                        if abs(timestamp - expected_timestamp) > (0.5 / fps) + 1e-6:
                            raise ValueError(
                                f"Video feature {name} is not aligned at frame {frame_index} "
                                f"of episode {episode.source_episode_index}"
                            )
                        frame[name] = value
                    frame["task"] = episode.task
                    target.add_frame(frame)
            finally:
                for iterator in video_iterators.values():
                    iterator.close()
            target.save_episode()
        target.finalize()

        mapping = {source_index: target_index for target_index, source_index in enumerate(source_indices)}
        return WorkerWriteResult(
            episode_index_mapping=mapping,
            accepted_decision_ids=tuple(episode.decision_id for episode in ordered),
            readback=self.readback(target_root=target_root, repo_id=repo_id, episode_count=len(ordered)),
        )

    def readback(self, *, target_root: Path, repo_id: str, episode_count: int) -> WorkerReadback:
        dataset = LeRobotDataset(repo_id=repo_id, root=target_root, download_videos=False, return_uint8=True)
        if dataset.num_episodes != episode_count:
            raise ValueError("Written episode count does not match accepted episode count")
        feature_names = tuple(sorted(dataset.features))
        visual_features = {
            name for name, specification in dataset.features.items() if specification.get("dtype") in {"image", "video"}
        }
        nonvisual_features = set(feature_names).difference(visual_features)
        sampled_visual_frames = 0
        visual_samples: list[WorkerVisualSample] = []
        episode_frame_counts: dict[int, int] = {}
        total_frames = 0
        for episode_index in range(episode_count):
            episode = LeRobotDataset(
                repo_id=repo_id,
                root=target_root,
                episodes=[episode_index],
                download_videos=False,
                return_uint8=True,
            )
            frame_count = len(episode)
            if frame_count == 0:
                raise ValueError(f"Written episode {episode_index} is empty")
            episode_frame_counts[episode_index] = frame_count
            total_frames += frame_count
            for frame_index in range(frame_count):
                missing = nonvisual_features.difference(episode.hf_dataset[frame_index])
                if missing:
                    raise ValueError(f"Written episode {episode_index} is missing features: {sorted(missing)}")
            sample_indices = sorted({0, frame_count // 2, frame_count - 1})
            for frame_index in sample_indices:
                frame = episode[frame_index]
                missing = set(feature_names).difference(frame)
                if missing:
                    raise ValueError(f"Written episode {episode_index} is missing features: {sorted(missing)}")
                for feature_name in sorted(visual_features):
                    if frame[feature_name] is None:
                        raise ValueError(f"Visual feature {feature_name} could not be decoded")
                    sampled_visual_frames += 1
                    visual_samples.append(
                        WorkerVisualSample(
                            release_episode_index=episode_index,
                            feature_name=feature_name,
                            frame_index=frame_index,
                        )
                    )
        return WorkerReadback(
            episode_count=episode_count,
            frame_count=total_frames,
            episode_frame_counts=episode_frame_counts,
            features=feature_names,
            sampled_visual_frames=sampled_visual_frames,
            visual_samples=tuple(visual_samples),
        )

    def convert_v21(self, *, source_root: Path, workspace_root: Path, repo_id: str) -> Path:
        if not source_root.is_dir():
            raise FileNotFoundError(source_root)
        source_digest = _tree_digest(source_root)
        workspace_root.mkdir(parents=True, exist_ok=False)
        copied_root = workspace_root / source_root.name
        shutil.copytree(source_root, copied_root)
        convert_dataset(repo_id=repo_id, root=copied_root, push_to_hub=False, force_conversion=True)
        if _tree_digest(source_root) != source_digest:
            raise RuntimeError("Source dataset changed during conversion")
        return copied_root


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _normalize_frame_value(value: Any, specification: dict[str, Any]) -> Any:
    shape = tuple(specification.get("shape", ()))
    return value.reshape(1) if shape == (1,) and getattr(value, "ndim", None) == 0 else value


def _rgb_encoder(features: dict[str, dict[str, Any]]) -> RGBEncoderConfig | None:
    configurations = [
        RGBEncoderConfig.from_video_info(specification.get("info"))
        for specification in features.values()
        if specification.get("dtype") == "video"
    ]
    if not configurations:
        return None
    encoder = configurations[0]
    if any(configuration != encoder for configuration in configurations[1:]):
        raise ValueError("Video features must use one shared RGB encoder configuration")
    return encoder


def _iter_episode_video(
    source: LeRobotDataset,
    episode_index: int,
    feature_name: str,
) -> Generator[tuple[float, np.ndarray], None, None]:
    episode = source.meta.episodes[episode_index]
    start_timestamp = float(episode[f"videos/{feature_name}/from_timestamp"])
    end_timestamp = float(episode[f"videos/{feature_name}/to_timestamp"])
    video_path = source.root / source.meta.get_video_file_path(episode_index, feature_name)
    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        if stream.time_base is None:
            raise ValueError(f"Video feature {feature_name} has no time base")
        time_base = float(stream.time_base)
        container.seek(int(start_timestamp / time_base), stream=stream, backward=True)
        for video_frame in container.decode(stream):
            if video_frame.pts is None:
                continue
            timestamp = float(video_frame.pts * stream.time_base)
            if timestamp + 1e-6 < start_timestamp:
                continue
            if timestamp >= end_timestamp - 1e-6:
                return
            value = np.moveaxis(video_frame.to_ndarray(format="rgb24"), -1, 0)
            yield timestamp - start_timestamp, value
