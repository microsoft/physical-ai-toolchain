"""Behavior tests for the isolated LeRobot release worker client."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from src.api.models.reviews import SourceFileIdentity, SourceIdentity
from src.api.release.lerobot import LeRobotReleaseAdapter, ReleaseEpisode


def _source(episode_index: int) -> SourceIdentity:
    digest = hashlib.sha256(f"episode-{episode_index}".encode()).hexdigest()
    return SourceIdentity(
        dataset_id="source-dataset",
        episode_index=episode_index,
        source_format="lerobot",
        format_version="v3.0",
        source_digest=digest,
        files=(SourceFileIdentity(relative_path="meta/info.json", size_bytes=1, sha256=digest),),
    )


def _episode(episode_index: int) -> ReleaseEpisode:
    frames = tuple(
        {
            "observation.state": np.asarray([index, index + 0.5], dtype=np.float32),
            "action": np.asarray([index + 1.0, index + 1.5], dtype=np.float32),
        }
        for index in range(3)
    )
    return ReleaseEpisode(
        source=_source(episode_index),
        decision_id=f"decision-{episode_index}",
        task="pick object",
        frames=frames,
    )


def test_write_v3_serializes_safe_worker_request_and_returns_readback(tmp_path: Path) -> None:
    # Arrange
    features = {
        "observation.state": {"dtype": "float32", "shape": (2,), "names": ["joint_0", "joint_1"]},
        "action": {"dtype": "float32", "shape": (2,), "names": ["joint_0", "joint_1"]},
    }
    requests: list[dict[str, Any]] = []

    def runner(request_path: Path) -> dict[str, Any]:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        requests.append(request)
        value_path = request_path.parent / request["episodes"][0]["frames"][0]["values"][0]["path"]
        assert np.load(value_path, allow_pickle=False).shape == (2,)
        return {
            "episode_index_mapping": {"5": 0, "9": 1},
            "accepted_decision_ids": ["decision-5", "decision-9"],
            "readback": {
                "episode_count": 2,
                "frame_count": 6,
                "features": ["action", "observation.state"],
                "sampled_visual_frames": 0,
            },
        }

    adapter = LeRobotReleaseAdapter(runner=runner)

    # Act
    result = adapter.write_v3(
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
    assert requests[0]["operation"] == "write-v3"
    assert [episode["source_episode_index"] for episode in requests[0]["episodes"]] == [5, 9]


def test_convert_v21_delegates_copy_isolation_to_worker(tmp_path: Path) -> None:
    # Arrange
    source_root = tmp_path / "source"
    (source_root / "meta").mkdir(parents=True)
    source_file = source_root / "meta" / "info.json"
    source_file.write_text('{"codebase_version":"v2.1"}', encoding="utf-8")
    source_bytes = source_file.read_bytes()
    requests: list[dict[str, Any]] = []

    def runner(request_path: Path) -> dict[str, Any]:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        requests.append(request)
        converted_root = Path(request["workspace_root"]) / Path(request["source_root"]).name
        (converted_root / "meta").mkdir(parents=True)
        (converted_root / "meta" / "info.json").write_text('{"codebase_version":"v3.0"}', encoding="utf-8")
        return {"converted_root": str(converted_root)}

    # Act
    converted_root = LeRobotReleaseAdapter(runner=runner).convert_v21(
        source_root=source_root,
        workspace_root=tmp_path / "workspace",
        repo_id="local/source",
    )

    # Assert
    assert requests[0]["operation"] == "convert-v21"
    assert converted_root != source_root
    assert source_file.read_bytes() == source_bytes
    assert '"v3.0"' in (converted_root / "meta" / "info.json").read_text(encoding="utf-8")
