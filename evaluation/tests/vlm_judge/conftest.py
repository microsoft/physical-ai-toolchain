"""CPU media fixtures for judge behavior tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection
from pathlib import Path
from typing import TYPE_CHECKING

import av
import numpy as np
import pytest

if TYPE_CHECKING:
    from vlm_judge.judge import JudgeResult


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


class StubJudgeService:
    """Judge service stand-in that returns ``outcome`` for every episode and raises on the chosen call numbers."""

    def __init__(self, outcome: bool | None, fail_calls: Collection[int]) -> None:
        self.outcome = outcome
        self.fail_calls = set(fail_calls)
        self.calls = 0

    def judge_episode(self, *, episode_id: str, instruction: str, **_kwargs: object) -> JudgeResult:
        # Imported here because the dataviewer suite loads this conftest as a plugin, where only the
        # evaluation.vlm_judge package path resolves.
        from vlm_judge.judge import JudgeResult

        self.calls += 1
        if self.calls in self.fail_calls:
            raise RuntimeError("judge backend unavailable")
        return JudgeResult(
            episode_id=episode_id,
            instruction=instruction,
            judge_model="stub",
            prompt_version="test",
            n_frames=2,
            outcome_success=self.outcome,
            outcome_confidence=0.75,
            outcome_n_valid_votes=3,
            progress_per_frame=[0, 100],
            voc=1.0,
        )


@pytest.fixture
def judge_service_stub() -> Callable[..., StubJudgeService]:
    """Return a factory for a judge service that answers one outcome or raises on chosen calls (1-based)."""

    def factory(outcome: bool | None = True, fail_calls: Collection[int] = ()) -> StubJudgeService:
        return StubJudgeService(outcome, fail_calls)

    return factory
