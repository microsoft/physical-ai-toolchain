"""Behavioral tests for the disk-backed cache."""

from __future__ import annotations

from pathlib import Path

import pytest
from vlm_judge.cache import JudgeCache


def _touch(path: Path, size_bytes: int = 64) -> Path:
    path.write_bytes(b"\0" * size_bytes)
    return path


class TestJudgeCache:
    def test_disabled_when_root_is_none(self) -> None:
        cache = JudgeCache(None)
        assert cache.enabled is False
        assert cache.get("anything") is None
        cache.put("anything", {"x": 1})

    def test_round_trip(self, tmp_path: Path) -> None:
        cache = JudgeCache(tmp_path / "cache")
        video = _touch(tmp_path / "ep.mp4")
        key = cache.key(
            video_paths={"front": video},
            instruction="pick orange",
            judge_model="echo",
            prompt_version="v1",
        )
        cache.put(key, {"outcome_success": True})
        assert cache.get(key) == {"outcome_success": True}

    def test_key_changes_with_instruction(self, tmp_path: Path) -> None:
        cache = JudgeCache(tmp_path / "cache")
        video = _touch(tmp_path / "ep.mp4")
        a = cache.key(
            video_paths={"front": video},
            instruction="pick orange",
            judge_model="echo",
            prompt_version="v1",
        )
        b = cache.key(
            video_paths={"front": video},
            instruction="pick apple",
            judge_model="echo",
            prompt_version="v1",
        )
        assert a != b

    def test_key_invalidates_on_file_mtime_change(self, tmp_path: Path) -> None:
        cache = JudgeCache(tmp_path / "cache")
        video = _touch(tmp_path / "ep.mp4", 32)
        key1 = cache.key(
            video_paths={"front": video},
            instruction="t",
            judge_model="echo",
            prompt_version="v1",
        )
        # Larger file -> different size -> different key
        _touch(video, 128)
        key2 = cache.key(
            video_paths={"front": video},
            instruction="t",
            judge_model="echo",
            prompt_version="v1",
        )
        assert key1 != key2

    def test_corrupt_entry_is_ignored(self, tmp_path: Path) -> None:
        cache = JudgeCache(tmp_path / "cache")
        video = _touch(tmp_path / "ep.mp4")
        key = cache.key(
            video_paths={"front": video},
            instruction="t",
            judge_model="echo",
            prompt_version="v1",
        )
        # Manually corrupt the cache entry
        path = tmp_path / "cache" / f"{key}.json"
        path.write_text("not json")
        assert cache.get(key) is None

    @pytest.mark.parametrize("agent_config", [None, {"k": 1}, object()])
    def test_serialises_various_config_types(self, tmp_path: Path, agent_config: object) -> None:
        cache = JudgeCache(tmp_path / "cache")
        video = _touch(tmp_path / "ep.mp4")
        key = cache.key(
            video_paths={"front": video},
            instruction="t",
            judge_model="echo",
            prompt_version="v1",
            agent_config=agent_config,
        )
        assert isinstance(key, str)
        assert len(key) == 64
