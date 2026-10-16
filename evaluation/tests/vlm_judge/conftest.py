"""CPU media fixtures for judge behavior tests."""

from __future__ import annotations

import json
from pathlib import Path

import av
import numpy as np
import pytest


@pytest.fixture
def rollout_video(tmp_path: Path) -> Path:
    """Return a one-second MP4 with twelve distinguishable frames."""
    path = tmp_path / "rollout.mp4"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=12)
        stream.width, stream.height, stream.pix_fmt = 24, 16, "yuv420p"
        for frame_index in range(12):
            image = np.full((16, 24, 3), frame_index * 20, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


@pytest.fixture
def jsonl_dataset(tmp_path: Path, rollout_video: Path) -> Path:
    """Return a v3 JSONL dataset with two real videos and one missing view."""
    root = tmp_path / "dataset"
    metadata = root / "meta/episodes/chunk-000"
    metadata.mkdir(parents=True)
    videos = root / "videos/front"
    videos.mkdir(parents=True)
    info = {
        "codebase_version": "v3.0",
        "fps": 12,
        "total_episodes": 3,
        "data_path": "data/episode_{episode_index:06d}.jsonl",
        "features": {"front": {"dtype": "video", "videos_path": "videos/front/episode_{episode_index:06d}.mp4"}},
    }
    (root / "meta/info.json").write_text(json.dumps(info))
    rows = [
        {"episode_index": 0, "length": 12, "tasks": json.dumps(["Pick the block"])},
        {"episode_index": 1, "length": 12, "task_index": 3},
        {"episode_index": 2, "length": 12, "tasks": ["Missing video"]},
    ]
    (metadata / "episode_000000.jsonl").write_text("\n" + "\n".join(json.dumps(row) for row in rows) + "\n")
    (root / "meta/tasks.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"task_index": 2}),
                json.dumps({"task_index": 3, "task": "Place the block"}),
            ]
        )
    )
    for index in (0, 1):
        (videos / f"episode_{index:06d}.mp4").write_bytes(rollout_video.read_bytes())
    return root
