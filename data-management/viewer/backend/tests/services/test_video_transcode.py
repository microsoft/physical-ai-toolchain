# cspell:ignore ffprobe veryfast
"""Behavior tests for on-demand video transcoding."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.api.services import video_transcode


class _FakeProcess:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def _install_fake_subprocesses(
    monkeypatch: pytest.MonkeyPatch,
    processes: list[_FakeProcess],
    *,
    ffmpeg_payload: bytes | None = None,
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def create_subprocess_exec(*args: object, **kwargs: object) -> _FakeProcess:
        calls.append((args, kwargs))
        if args[0] == "ffmpeg" and ffmpeg_payload is not None:
            partial = Path(str(args[-1]))
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(ffmpeg_payload)
        if not processes:
            raise AssertionError("Unexpected subprocess invocation")
        return processes.pop(0)

    monkeypatch.setattr(video_transcode.asyncio, "create_subprocess_exec", create_subprocess_exec)
    return calls


def _assert_probe_call(
    call: tuple[tuple[object, ...], dict[str, object]],
    source: Path,
) -> None:
    args, kwargs = call
    assert args == (
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name",
        "-of",
        "json",
        str(source),
    )
    assert kwargs == {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        ({"ffprobe", "ffmpeg"}, True),
        ({"ffprobe"}, False),
        ({"ffmpeg"}, False),
        (set(), False),
    ],
)
def test_transcoding_available_requires_both_tools(
    monkeypatch: pytest.MonkeyPatch,
    available: set[str],
    expected: bool,
) -> None:
    monkeypatch.setattr(
        video_transcode.shutil,
        "which",
        lambda executable: f"/usr/bin/{executable}" if executable in available else None,
    )

    assert video_transcode.transcoding_available() is expected


@pytest.mark.asyncio
async def test_ensure_browser_compatible_returns_source_when_tools_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(video_transcode.shutil, "which", lambda _executable: None)
    subprocess = AsyncMock()
    monkeypatch.setattr(video_transcode.asyncio, "create_subprocess_exec", subprocess)

    result = await video_transcode.ensure_browser_compatible(source)

    assert result == source
    subprocess.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_browser_compatible_returns_source_for_supported_probe_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(video_transcode.shutil, "which", lambda executable: f"/usr/bin/{executable}")
    payload = json.dumps({"streams": [{"codec_name": "H264"}]}).encode()
    calls = _install_fake_subprocesses(monkeypatch, [_FakeProcess(0, stdout=payload)])

    result = await video_transcode.ensure_browser_compatible(source)

    assert result == source
    assert len(calls) == 1
    _assert_probe_call(calls[0], source)


@pytest.mark.parametrize(
    ("process", "case"),
    [
        (_FakeProcess(1, stderr=b"probe failed"), "failed probe"),
        (_FakeProcess(0, stdout=b"not-json"), "invalid JSON"),
        (_FakeProcess(0, stdout=b'{"streams": []}'), "missing video stream"),
    ],
)
@pytest.mark.asyncio
async def test_ensure_browser_compatible_returns_source_when_probe_has_no_codec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process: _FakeProcess,
    case: str,
) -> None:
    source = tmp_path / f"{case}.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(video_transcode.shutil, "which", lambda executable: f"/usr/bin/{executable}")
    calls = _install_fake_subprocesses(monkeypatch, [process])

    result = await video_transcode.ensure_browser_compatible(source)

    assert result == source
    assert len(calls) == 1
    _assert_probe_call(calls[0], source)


@pytest.mark.asyncio
async def test_ensure_browser_compatible_atomically_replaces_successful_transcode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"source")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(video_transcode, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(video_transcode.shutil, "which", lambda executable: f"/usr/bin/{executable}")
    probe_payload = json.dumps({"streams": [{"codec_name": "mpeg4"}]}).encode()
    calls = _install_fake_subprocesses(
        monkeypatch,
        [_FakeProcess(0, stdout=probe_payload), _FakeProcess(0)],
        ffmpeg_payload=b"converted",
    )

    result = await video_transcode.ensure_browser_compatible(source)

    assert result.parent == cache_dir
    assert result.suffix == ".mp4"
    assert result.read_bytes() == b"converted"
    assert list(cache_dir.glob("*.part")) == []
    assert len(calls) == 2
    _assert_probe_call(calls[0], source)
    ffmpeg_args, ffmpeg_kwargs = calls[1]
    assert ffmpeg_args == (
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(result.with_suffix(".mp4.part")),
    )
    assert ffmpeg_kwargs == {
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.PIPE,
    }


@pytest.mark.asyncio
async def test_ensure_browser_compatible_removes_failed_transcode_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"source")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(video_transcode, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(video_transcode.shutil, "which", lambda executable: f"/usr/bin/{executable}")
    probe_payload = json.dumps({"streams": [{"codec_name": "mpeg4"}]}).encode()
    calls = _install_fake_subprocesses(
        monkeypatch,
        [_FakeProcess(0, stdout=probe_payload), _FakeProcess(1, stderr=b"conversion failed")],
        ffmpeg_payload=b"partial",
    )

    result = await video_transcode.ensure_browser_compatible(source)

    assert result == source
    assert cache_dir.exists()
    assert list(cache_dir.iterdir()) == []
    assert len(calls) == 2
    _assert_probe_call(calls[0], source)


@pytest.mark.asyncio
async def test_ensure_browser_compatible_reuses_completed_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"source")
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(video_transcode, "_CACHE_DIR", cache_dir)
    monkeypatch.setattr(video_transcode.shutil, "which", lambda executable: f"/usr/bin/{executable}")
    probe_payload = json.dumps({"streams": [{"codec_name": "mpeg4"}]}).encode()
    calls = _install_fake_subprocesses(
        monkeypatch,
        [
            _FakeProcess(0, stdout=probe_payload),
            _FakeProcess(0),
            _FakeProcess(0, stdout=probe_payload),
        ],
        ffmpeg_payload=b"converted",
    )

    first = await video_transcode.ensure_browser_compatible(source)
    second = await video_transcode.ensure_browser_compatible(source)

    assert second == first
    assert second.read_bytes() == b"converted"
    assert [call[0][0] for call in calls] == ["ffprobe", "ffmpeg", "ffprobe"]
