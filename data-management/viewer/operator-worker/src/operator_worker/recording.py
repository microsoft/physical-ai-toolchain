"""Browser-commanded LeRobot episode buffer ownership."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

RecordingAction = Literal["save", "rerecord", "pause", "resume", "finish", "cancel"]
RecordingPhase = Literal["recording", "paused", "complete", "finalized", "cancelled"]


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


class RecordingSession:
    """Own one LeRobot dataset writer and its pending episode buffer."""

    def __init__(
        self,
        dataset: WritableDataset,
        *,
        dataset_id: str,
        num_episodes: int,
    ) -> None:
        self.dataset = dataset
        self.dataset_id = dataset_id
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
            self._finalize()
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
        self._finalize()

    def _save_pending(self) -> None:
        if self.episode_index < self.num_episodes and self.dataset.has_pending_frames():
            self.dataset.save_episode(parallel_encoding=False)
            self.episode_index += 1

    def _finalize(self) -> None:
        if not self._finalized:
            self.dataset.finalize()
            self._finalized = True
