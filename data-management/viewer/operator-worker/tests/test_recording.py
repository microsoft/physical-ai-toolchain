from __future__ import annotations

from operator_worker.recording import RecordingSession


class FakeDataset:
    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.saved = 0
        self.cleared = 0
        self.finalized = 0
        self.parallel_encoding: bool | None = None

    def add_frame(self, frame: dict) -> None:
        self.frames.append(frame)

    def has_pending_frames(self) -> bool:
        return bool(self.frames)

    def save_episode(self, parallel_encoding: bool = True) -> None:
        self.saved += 1
        self.parallel_encoding = parallel_encoding
        self.frames.clear()

    def clear_episode_buffer(self) -> None:
        self.cleared += 1
        self.frames.clear()

    def finalize(self) -> None:
        self.finalized += 1


def test_save_and_discard_map_to_lerobot_episode_operations() -> None:
    dataset = FakeDataset()
    session = RecordingSession(dataset, dataset_id="demo", num_episodes=3)
    session.add_frame({"frame": 1})

    saved = session.command("save")
    session.add_frame({"frame": 2})
    discarded = session.command("rerecord")

    assert saved.episode_index == 1
    assert saved.phase == "recording"
    assert discarded.episode_index == 1
    assert dataset.saved == 1
    assert dataset.parallel_encoding is False
    assert dataset.cleared == 1


def test_episode_limit_waits_for_explicit_finish() -> None:
    dataset = FakeDataset()
    session = RecordingSession(dataset, dataset_id="demo", num_episodes=1)
    session.add_frame({"frame": 1})

    saved = session.command("save")

    assert saved.phase == "complete"
    assert saved.should_stop is False
    assert dataset.finalized == 0


def test_finish_saves_pending_episode_and_finalizes() -> None:
    dataset = FakeDataset()
    session = RecordingSession(dataset, dataset_id="demo", num_episodes=3)
    session.add_frame({"frame": 1})

    result = session.command("finish")

    assert result.phase == "finalized"
    assert result.should_stop is True
    assert result.episode_index == 1
    assert dataset.saved == 1
    assert dataset.finalized == 1


def test_cancel_discards_only_pending_episode_and_preserves_committed() -> None:
    dataset = FakeDataset()
    session = RecordingSession(dataset, dataset_id="demo", num_episodes=3)
    session.add_frame({"frame": 1})
    session.command("save")
    session.add_frame({"frame": 2})

    result = session.command("cancel")

    assert result.phase == "cancelled"
    assert result.should_stop is True
    assert result.episode_index == 1
    assert dataset.saved == 1
    assert dataset.cleared == 1
    assert dataset.finalized == 1


def test_pause_and_resume_preserve_the_pending_episode() -> None:
    dataset = FakeDataset()
    session = RecordingSession(dataset, dataset_id="demo", num_episodes=3)
    session.add_frame({"frame": 1})

    paused = session.command("pause")
    session.add_frame({"frame": 2})
    resumed = session.command("resume")
    session.add_frame({"frame": 3})

    assert paused.phase == "paused"
    assert resumed.phase == "recording"
    assert dataset.frames == [{"frame": 1}, {"frame": 3}]
    assert dataset.saved == 0
    assert dataset.cleared == 0
