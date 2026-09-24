from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest

from operator_worker.recording import RecordingError, RecordingSession


class FakeDataset:
    features: ClassVar[dict[str, dict[str, Any]]] = {}

    def __init__(self, root: Path) -> None:
        self.root = root
        self.frames: list[dict[str, Any]] = []
        self.saved = 0
        self.finalized = 0

    def add_frame(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    def has_pending_frames(self) -> bool:
        return bool(self.frames)

    def save_episode(self, parallel_encoding: bool = True) -> None:
        self.saved += 1
        self.frames.clear()

    def clear_episode_buffer(self) -> None:
        self.frames.clear()

    def finalize(self) -> None:
        self.finalized += 1
        (self.root / "meta").mkdir(parents=True)
        (self.root / "data").mkdir()
        (self.root / "meta" / "info.json").write_text(json.dumps({"total_episodes": self.saved}), encoding="utf-8")

    def push_to_hub(self) -> None:
        raise AssertionError("upload is not part of local recording finalization")


def test_given_pending_frames_when_finished_then_current_dataset_contract_is_discoverable(tmp_path: Path) -> None:
    dataset_root = tmp_path / "captured-dataset"
    session = RecordingSession(
        FakeDataset(dataset_root),
        dataset_id="captured-dataset",
        dataset_root=dataset_root,
        num_episodes=1,
    )
    session.add_frame({"frame": 1})

    result = session.command("finish")

    assert result.phase == "finalized"
    assert result.should_stop is True
    assert (dataset_root / "meta" / "info.json").is_file()
    assert (dataset_root / "data").is_dir()
    assert not (dataset_root / "releases").exists()


def test_given_incomplete_dataset_contract_when_finished_then_finalization_fails(tmp_path: Path) -> None:
    class IncompleteDataset(FakeDataset):
        def finalize(self) -> None:
            self.finalized += 1

    dataset_root = tmp_path / "captured-dataset"
    session = RecordingSession(
        IncompleteDataset(dataset_root),
        dataset_id="captured-dataset",
        dataset_root=dataset_root,
        num_episodes=1,
    )

    with pytest.raises(RecordingError, match="discoverable"):
        session.command("finish")
