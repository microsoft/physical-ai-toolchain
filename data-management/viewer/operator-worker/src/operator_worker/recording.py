"""Explicit LeRobot episode-buffer ownership and dataset contract checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

RecordingAction = Literal["save", "rerecord", "pause", "resume", "finish", "cancel"]
RecordingPhase = Literal["recording", "paused", "complete", "finalized", "cancelled"]


class RecordingError(RuntimeError):
    """Raised when recording cannot produce a discoverable source dataset."""


class WritableDataset(Protocol):
    features: dict[str, dict[str, Any]]

    def add_frame(self, frame: dict[str, Any]) -> None: ...

    def has_pending_frames(self) -> bool: ...

    def save_episode(self, parallel_encoding: bool = True) -> None: ...

    def clear_episode_buffer(self) -> None: ...

    def finalize(self) -> None: ...

    def push_to_hub(self) -> None: ...


@dataclass(frozen=True)
class RecordingCommandResult:
    dataset_id: str
    episode_index: int
    phase: RecordingPhase
    should_stop: bool


def validate_discoverable_dataset(dataset_root: Path, dataset_id: str) -> None:
    """Require the current local discovery markers without creating release state."""
    if dataset_root.name != dataset_id or dataset_root.is_symlink():
        raise RecordingError("Recording dataset root does not match the dataset identifier")
    info_path = dataset_root / "meta" / "info.json"
    data_path = dataset_root / "data"
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RecordingError("Finalized recording is not discoverable: invalid meta/info.json") from error
    if not isinstance(info, dict) or not data_path.is_dir():
        raise RecordingError("Finalized recording is not discoverable by the current dataset contract")


class RecordingSession:
    """Own one dataset writer and require explicit episode and finalization commands."""

    def __init__(
        self,
        dataset: WritableDataset,
        *,
        dataset_id: str,
        num_episodes: int,
        dataset_root: Path | None = None,
    ) -> None:
        self.dataset = dataset
        self.dataset_id = dataset_id
        self.dataset_root = dataset_root
        self.num_episodes = num_episodes
        self.episode_index = 0
        self.phase: RecordingPhase = "recording"
        self._finalized = False

    @property
    def features(self) -> dict[str, dict[str, Any]]:
        return self.dataset.features

    def push_to_hub(self) -> None:
        self.dataset.push_to_hub()

    def add_frame(self, frame: dict[str, Any]) -> None:
        if self.phase == "recording" and self.episode_index < self.num_episodes:
            self.dataset.add_frame(frame)

    def command(self, action: RecordingAction) -> RecordingCommandResult:
        if action == "save":
            self._save_pending()
            self.phase = "complete" if self.episode_index >= self.num_episodes else "recording"
        elif action == "rerecord":
            if self.dataset.has_pending_frames():
                self.dataset.clear_episode_buffer()
            self.phase = "recording"
        elif action == "pause":
            if self.phase == "recording":
                self.phase = "paused"
        elif action == "resume":
            if self.phase == "paused":
                self.phase = "recording"
        elif action == "finish":
            self._save_pending()
            self._finalize()
            self.phase = "finalized"
        elif action == "cancel":
            if self.dataset.has_pending_frames():
                self.dataset.clear_episode_buffer()
            self._finalize(validate_contract=False)
            self.phase = "cancelled"
        return RecordingCommandResult(
            dataset_id=self.dataset_id,
            episode_index=self.episode_index,
            phase=self.phase,
            should_stop=action in {"finish", "cancel"},
        )

    def finalize_for_cleanup(self) -> None:
        if self.dataset.has_pending_frames():
            self.dataset.clear_episode_buffer()
        self._finalize(validate_contract=False)

    def _save_pending(self) -> None:
        if self.episode_index < self.num_episodes and self.dataset.has_pending_frames():
            self.dataset.save_episode(parallel_encoding=False)
            self.episode_index += 1

    def _finalize(self, *, validate_contract: bool = True) -> None:
        if self._finalized:
            return
        self.dataset.finalize()
        if validate_contract and self.dataset_root is not None:
            validate_discoverable_dataset(self.dataset_root, self.dataset_id)
        self._finalized = True
