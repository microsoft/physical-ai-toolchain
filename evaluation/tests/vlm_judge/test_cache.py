"""Behavioral tests for the disk-backed cache."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from vlm_judge.cache import JudgeCache


def _touch(path: Path, size_bytes: int = 64) -> Path:
    path.write_bytes(b"\0" * size_bytes)
    return path


class TestJudgeCache:
    def test_legacy_payload_cannot_match_new_identity(self, tmp_path: Path) -> None:
        video = _touch(tmp_path / "ep.mp4")
        legacy = {
            "videos": [
                {
                    "view": "front",
                    "path": str(video),
                    "size": video.stat().st_size,
                    "mtime_ns": video.stat().st_mtime_ns,
                }
            ],
            "time_window": {"from_s": None, "to_s": None},
            "instruction": "pick cube",
            "judge_model": "echo",
            "prompt_version": "v1",
            "agent_config": None,
        }
        legacy_key = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
        current = JudgeCache(None).key(
            video_paths={"front": video}, instruction="pick cube", judge_model="echo", prompt_version="v1"
        )
        assert current != legacy_key

    def test_explicit_local_locator_is_canonical(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "model").mkdir()
        cache = JudgeCache(None)
        arguments = {"video_paths": {}, "instruction": "x", "prompt_version": "v1", "backend_kind": "qwen3-vl"}
        assert cache.key(**arguments, judge_model="./model") == cache.key(
            **arguments, judge_model=str(tmp_path / "model")
        )

    def test_backend_and_normalized_revision_separate_cache_entries(self, tmp_path: Path) -> None:
        cache = JudgeCache(tmp_path / "cache")
        arguments = {
            "video_paths": {"front": _touch(tmp_path / "ep.mp4")},
            "instruction": "pick cube",
            "judge_model": "owner/model",
            "prompt_version": "v1",
        }
        echo = cache.key(**arguments, backend_kind="echo", model_revision="a" * 40)
        native = cache.key(**arguments, backend_kind="qwen3-vl", model_revision="a" * 40)
        changed = cache.key(**arguments, backend_kind="qwen3-vl", model_revision="b" * 40)
        normalized = cache.key(**arguments, backend_kind="qwen3-vl", model_revision="A" * 40)

        assert len({echo, native, changed}) == 3
        assert native == normalized

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

    def test_key_changes_with_time_window(self, tmp_path: Path) -> None:
        cache = JudgeCache(tmp_path / "cache")
        video = _touch(tmp_path / "chunk.mp4")
        first = cache.key(
            video_paths={"front": video},
            instruction="pick orange",
            judge_model="echo",
            prompt_version="v1",
            from_s=0.0,
            to_s=3.0,
        )
        second = cache.key(
            video_paths={"front": video},
            instruction="pick orange",
            judge_model="echo",
            prompt_version="v1",
            from_s=3.0,
            to_s=6.0,
        )
        assert first != second

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
        # Manually corrupt the cache entry (dir is created lazily on first put)
        path = tmp_path / "cache" / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
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
