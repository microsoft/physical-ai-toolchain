"""Behavior tests for isolated native LeRobot release processing."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset

from release_worker.lerobot import LeRobotReleaseWorker, WorkerEpisode, WorkerEpisodeReference


def _episode(episode_index: int) -> WorkerEpisode:
    frames = tuple(
        {
            "observation.state": np.asarray([index, index + 0.5], dtype=np.float32),
            "action": np.asarray([index + 1.0, index + 1.5], dtype=np.float32),
        }
        for index in range(3)
    )
    return WorkerEpisode(
        source_episode_index=episode_index,
        decision_id=f"decision-{episode_index}",
        task="pick plate",
        frames=frames,
    )


def test_write_v3_uses_native_lifecycle_and_reads_back_all_episodes(tmp_path: Path) -> None:
    # Arrange
    features = {
        "observation.state": {"dtype": "float32", "shape": (2,), "names": ["joint_0", "joint_1"]},
        "action": {"dtype": "float32", "shape": (2,), "names": ["joint_0", "joint_1"]},
    }

    # Act
    result = LeRobotReleaseWorker().write_v3(
        episodes=(_episode(9), _episode(5)),
        target_root=tmp_path / "release",
        repo_id="local/release",
        fps=10,
        features=features,
    )

    # Assert
    assert result.episode_index_mapping == {5: 0, 9: 1}
    assert result.accepted_decision_ids == ("decision-5", "decision-9")
    assert result.readback.episode_count == 2
    assert result.readback.frame_count == 6
    assert set(result.readback.features) >= {"observation.state", "action"}
    assert result.readback.sampled_visual_frames == 0


def test_copy_v3_streams_source_video_without_random_access(tmp_path: Path, monkeypatch) -> None:
    # Arrange
    source_root = tmp_path / "source"
    features = {
        "observation.state": {"dtype": "float32", "shape": (2,), "names": ["joint_0", "joint_1"]},
        "action": {"dtype": "float32", "shape": (2,), "names": ["joint_0", "joint_1"]},
        "observation.images.camera": {
            "dtype": "video",
            "shape": (3, 64, 64),
            "names": ["channels", "height", "width"],
            "info": {"video.codec": "h264"},
        },
    }
    source_frames = tuple(
        {
            "observation.state": np.asarray([index, index + 0.5], dtype=np.float32),
            "action": np.asarray([index + 1.0, index + 1.5], dtype=np.float32),
            "observation.images.camera": np.full((3, 64, 64), index * 20, dtype=np.uint8),
        }
        for index in range(10)
    )
    worker = LeRobotReleaseWorker()
    worker.write_v3(
        episodes=(
            WorkerEpisode(
                source_episode_index=7,
                decision_id="source-decision",
                task="pick plate",
                frames=source_frames,
            ),
        ),
        target_root=source_root,
        repo_id="local/source",
        fps=10,
        features=features,
    )
    original_getitem = LeRobotDataset.__getitem__
    target_video_reads = 0

    def reject_source_random_access(dataset: LeRobotDataset, item: int) -> dict:
        nonlocal target_video_reads
        if Path(dataset.root) == source_root:
            raise AssertionError("Source video must not use random-access decoding")
        target_video_reads += 1
        return original_getitem(dataset, item)

    monkeypatch.setattr(LeRobotDataset, "__getitem__", reject_source_random_access)

    # Act
    result = worker.copy_v3(
        source_root=source_root,
        source_repo_id="local/source",
        episodes=(WorkerEpisodeReference(0, "accepted-decision", "pick plate"),),
        target_root=tmp_path / "release",
        repo_id="local/release",
        fps=10,
        features=features,
    )

    # Assert
    assert result.episode_index_mapping == {0: 0}
    assert result.readback.frame_count == 10
    assert result.readback.sampled_visual_frames == 3
    assert target_video_reads == 3
    released = LeRobotDataset(repo_id="local/release", root=tmp_path / "release", download_videos=False)
    assert released.features["observation.images.camera"]["info"]["video.codec"] == "h264"


def test_convert_v21_copies_workspace_and_preserves_source(tmp_path: Path, monkeypatch) -> None:
    # Arrange
    source_root = tmp_path / "source"
    (source_root / "meta").mkdir(parents=True)
    source_file = source_root / "meta" / "info.json"
    source_file.write_text('{"codebase_version":"v2.1"}', encoding="utf-8")
    source_bytes = source_file.read_bytes()

    def fake_convert_dataset(*, repo_id: str, root: Path, push_to_hub: bool, force_conversion: bool) -> None:
        assert repo_id == "local/source"
        assert push_to_hub is False
        assert force_conversion is True
        (root / "meta" / "info.json").write_text('{"codebase_version":"v3.0"}', encoding="utf-8")

    monkeypatch.setattr("release_worker.lerobot.convert_dataset", fake_convert_dataset)

    # Act
    converted_root = LeRobotReleaseWorker().convert_v21(
        source_root=source_root,
        workspace_root=tmp_path / "workspace",
        repo_id="local/source",
    )

    # Assert
    assert converted_root != source_root
    assert source_file.read_bytes() == source_bytes
    assert '"v3.0"' in (converted_root / "meta" / "info.json").read_text(encoding="utf-8")
