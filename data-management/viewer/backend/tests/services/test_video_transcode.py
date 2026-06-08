# cspell:ignore ffprobe veryfast
"""Tests for the on-demand video transcoding helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.services import video_transcode


@pytest.fixture(autouse=True)
def _reset_locks():
    video_transcode._locks.clear()
    yield
    video_transcode._locks.clear()


class TestHaveTool:
    def test_have_tool_uses_shutil_which(self) -> None:
        with patch("src.api.services.video_transcode.shutil.which", return_value="/usr/bin/x"):
            assert video_transcode._have_tool("x") is True
        with patch("src.api.services.video_transcode.shutil.which", return_value=None):
            assert video_transcode._have_tool("x") is False


class TestTranscodingAvailable:
    def test_returns_true_when_both_tools_present(self) -> None:
        with patch("src.api.services.video_transcode._have_tool", return_value=True):
            assert video_transcode.transcoding_available() is True

    def test_returns_false_when_any_tool_missing(self) -> None:
        with patch("src.api.services.video_transcode._have_tool", side_effect=[True, False]):
            assert video_transcode.transcoding_available() is False


class TestLockReuse:
    def test_same_key_returns_same_lock(self) -> None:
        lock_a = asyncio.run(video_transcode._get_lock("k"))
        lock_b = asyncio.run(video_transcode._get_lock("k"))
        assert lock_a is lock_b

    def test_different_keys_return_distinct_locks(self) -> None:
        lock_a = asyncio.run(video_transcode._get_lock("a"))
        lock_b = asyncio.run(video_transcode._get_lock("b"))
        assert lock_a is not lock_b


class TestProbeVideoCodec:
    def test_returns_codec_name(self) -> None:
        payload = json.dumps({"streams": [{"codec_name": "h264"}]}).encode("utf-8")
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(payload, b""))
        proc.returncode = 0
        with patch("src.api.services.video_transcode.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            result = asyncio.run(video_transcode._probe_video_codec(Path("/tmp/x.mp4")))
        assert result == "h264"

    def test_returns_none_on_non_zero_exit(self) -> None:
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b"err"))
        proc.returncode = 1
        with patch("src.api.services.video_transcode.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            assert asyncio.run(video_transcode._probe_video_codec(Path("/tmp/x.mp4"))) is None

    def test_returns_none_on_invalid_json(self) -> None:
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"not-json", b""))
        proc.returncode = 0
        with patch("src.api.services.video_transcode.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            assert asyncio.run(video_transcode._probe_video_codec(Path("/tmp/x.mp4"))) is None

    def test_returns_none_when_no_streams(self) -> None:
        payload = json.dumps({"streams": []}).encode("utf-8")
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(payload, b""))
        proc.returncode = 0
        with patch("src.api.services.video_transcode.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            assert asyncio.run(video_transcode._probe_video_codec(Path("/tmp/x.mp4"))) is None


class TestCacheKey:
    def test_stable_for_existing_file(self, tmp_path: Path) -> None:
        source = tmp_path / "src.mp4"
        source.write_bytes(b"xyz")
        key1 = video_transcode._cache_key(source)
        key2 = video_transcode._cache_key(source)
        assert key1 == key2
        assert len(key1) == 40  # sha1 hex

    def test_falls_back_when_stat_fails(self) -> None:
        source = Path("/does/not/exist.mp4")
        # Stat raises OSError → fallback path. Must still hash deterministically.
        assert isinstance(video_transcode._cache_key(source), str)


class TestEnsureBrowserCompatible:
    def test_returns_source_when_tools_missing(self, tmp_path: Path) -> None:
        source = tmp_path / "in.mp4"
        source.write_bytes(b"x")
        with patch("src.api.services.video_transcode.transcoding_available", return_value=False):
            result = asyncio.run(video_transcode.ensure_browser_compatible(source))
        assert result == source

    def test_returns_source_when_codec_compatible(self, tmp_path: Path) -> None:
        source = tmp_path / "in.mp4"
        source.write_bytes(b"x")
        with (
            patch("src.api.services.video_transcode.transcoding_available", return_value=True),
            patch("src.api.services.video_transcode._probe_video_codec", AsyncMock(return_value="h264")),
        ):
            result = asyncio.run(video_transcode.ensure_browser_compatible(source))
        assert result == source

    def test_returns_source_when_probe_fails(self, tmp_path: Path) -> None:
        source = tmp_path / "in.mp4"
        source.write_bytes(b"x")
        with (
            patch("src.api.services.video_transcode.transcoding_available", return_value=True),
            patch("src.api.services.video_transcode._probe_video_codec", AsyncMock(return_value=None)),
        ):
            assert asyncio.run(video_transcode.ensure_browser_compatible(source)) == source

    def test_returns_cached_when_already_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "in.mp4"
        source.write_bytes(b"data")
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(video_transcode, "_CACHE_DIR", cache_dir)
        cached = cache_dir / f"{video_transcode._cache_key(source)}.mp4"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b"already-converted")
        with (
            patch("src.api.services.video_transcode.transcoding_available", return_value=True),
            patch("src.api.services.video_transcode._probe_video_codec", AsyncMock(return_value="mpeg4")),
        ):
            result = asyncio.run(video_transcode.ensure_browser_compatible(source))
        assert result == cached

    def test_transcodes_when_codec_incompatible(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "in.mp4"
        source.write_bytes(b"data")
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(video_transcode, "_CACHE_DIR", cache_dir)
        cached = cache_dir / f"{video_transcode._cache_key(source)}.mp4"

        async def fake_transcode(src: Path, dst: Path) -> bool:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(b"converted")
            return True

        with (
            patch("src.api.services.video_transcode.transcoding_available", return_value=True),
            patch("src.api.services.video_transcode._probe_video_codec", AsyncMock(return_value="mpeg4")),
            patch("src.api.services.video_transcode._transcode_to_h264", side_effect=fake_transcode),
        ):
            result = asyncio.run(video_transcode.ensure_browser_compatible(source))
        assert result == cached
        assert cached.read_bytes() == b"converted"

    def test_returns_source_when_transcode_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = tmp_path / "in.mp4"
        source.write_bytes(b"data")
        monkeypatch.setattr(video_transcode, "_CACHE_DIR", tmp_path / "cache")
        with (
            patch("src.api.services.video_transcode.transcoding_available", return_value=True),
            patch("src.api.services.video_transcode._probe_video_codec", AsyncMock(return_value="mpeg4")),
            patch("src.api.services.video_transcode._transcode_to_h264", AsyncMock(return_value=False)),
        ):
            assert asyncio.run(video_transcode.ensure_browser_compatible(source)) == source


class TestTranscodeToH264:
    def test_success_replaces_target(self, tmp_path: Path) -> None:
        source = tmp_path / "in.mp4"
        target = tmp_path / "out.mp4"
        source.write_bytes(b"data")

        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0

        async def fake_exec(*args, **kwargs):
            tmp = target.with_suffix(target.suffix + ".part")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(b"x")
            return proc

        with patch("src.api.services.video_transcode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            ok = asyncio.run(video_transcode._transcode_to_h264(source, target))
        assert ok is True
        assert target.exists()

    def test_failure_removes_partial(self, tmp_path: Path) -> None:
        source = tmp_path / "in.mp4"
        target = tmp_path / "out.mp4"
        source.write_bytes(b"data")

        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b"err"))
        proc.returncode = 1

        async def fake_exec(*args, **kwargs):
            tmp = target.with_suffix(target.suffix + ".part")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(b"x")
            return proc

        with patch("src.api.services.video_transcode.asyncio.create_subprocess_exec", side_effect=fake_exec):
            ok = asyncio.run(video_transcode._transcode_to_h264(source, target))
        assert ok is False
        assert not target.with_suffix(target.suffix + ".part").exists()
