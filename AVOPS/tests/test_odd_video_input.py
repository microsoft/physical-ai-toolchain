# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Tests for whole-clip VLM input: the annotator's request and the adapter's frame sampling."""

from __future__ import annotations

import base64
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ODD_DIR = Path(__file__).resolve().parents[1] / "ODD"
sys.path.insert(0, str(ODD_DIR))

from odd_core import annotator as annotator_module  # noqa: E402
from odd_core.annotator import VLMAnnotator, encode_video_b64  # noqa: E402

QA = {"sceneDetails": {"weatherType": "What is the weather?"}}
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def make_clip(path: Path, seconds: int = 3, size: str = "640x360") -> Path:
    source = f"testsrc=duration={seconds}:size={size}:rate=10"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", source, "-pix_fmt", "yuv420p", str(path)],
        check=True,
        capture_output=True,
    )
    return path


class FakeClient:
    """Records chat requests and answers the batch question as JSON."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        message = SimpleNamespace(content='{"sceneDetails.weatherType": "Rainy"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def annotate(tmp_path: Path, **options) -> tuple[dict, FakeClient]:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not decoded here")
    annotator = VLMAnnotator(**options)
    annotator._openai_client = client = FakeClient()
    return annotator.annotate_video(clip, QA), client


def test_annotator_sends_the_whole_clip_as_a_video_url(tmp_path):
    result, client = annotate(tmp_path)
    part = client.requests[0]["messages"][1]["content"][0]
    assert part["type"] == "video_url"
    prefix = "data:video/mp4;base64,"
    assert part["video_url"]["url"].startswith(prefix)
    assert base64.b64decode(part["video_url"]["url"][len(prefix) :]) == b"not decoded here"
    assert "video clip" in client.requests[0]["messages"][1]["content"][1]["text"]
    assert result["sceneDetails"]["weatherType"]["answer"] == "Rainy"


def test_annotator_can_send_only_the_first_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(annotator_module, "extract_first_frame_b64", lambda path: "FRAME")
    _, client = annotate(tmp_path, media="frame")
    part = client.requests[0]["messages"][1]["content"][0]
    assert part == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,FRAME"}}


def test_annotator_rejects_an_unknown_media_mode():
    with pytest.raises(ValueError, match="media must be one of"):
        VLMAnnotator(media="audio")


def test_annotator_reports_a_clip_it_cannot_prepare(tmp_path):
    annotator = VLMAnnotator()
    with pytest.raises(RuntimeError, match="Failed to prepare video input"):
        annotator.annotate_video(tmp_path / "missing.mp4", QA)
    assert annotator.annotate_video(tmp_path / "missing.mp4", QA, fallback_mock=True)["sceneDetails"]


def test_small_mp4_is_sent_unchanged(tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"bytes")
    assert base64.b64decode(encode_video_b64(clip)) == b"bytes"


@needs_ffmpeg
def test_other_containers_and_oversized_clips_are_re_encoded(tmp_path):
    avi = make_clip(tmp_path / "a.avi")
    encoded = base64.b64decode(encode_video_b64(avi))
    assert encoded[4:8] == b"ftyp"  # an MP4 file type box  # cspell:ignore ftyp

    mp4 = make_clip(tmp_path / "b.mp4", size="1280x720")
    original = mp4.stat().st_size
    smaller = base64.b64decode(encode_video_b64(mp4, max_bytes=original - 1, max_dim=320))
    assert len(smaller) < original
    with pytest.raises(ValueError, match="limit is"):
        encode_video_b64(mp4, max_bytes=100)


def load_adapter():
    pytest.importorskip("fastapi")
    pytest.importorskip("av")
    pytest.importorskip("numpy")
    spec = importlib.util.spec_from_file_location(
        "cosmos_reason_vlm_adapter_test", ODD_DIR / "odd_core" / "cosmos_reason_vlm_adapter.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@needs_ffmpeg
def test_adapter_samples_frames_across_the_whole_clip(tmp_path):
    adapter = load_adapter()
    data = make_clip(tmp_path / "c.mp4", seconds=3, size="1280x720").read_bytes()
    frames, meta = adapter.sample_video_frames(data, adapter.VideoOptions(fps=4, max_frames=32, max_dim=320))
    assert frames.shape == (12, 180, 320, 3)
    assert meta["total_num_frames"] == 12
    assert meta["duration"] == pytest.approx(3.0, abs=0.2)
    assert meta["fps"] == pytest.approx(4.0, abs=0.4)
    assert frames[0].tobytes() != frames[-1].tobytes()  # the last frame comes from later in the clip


@needs_ffmpeg
def test_adapter_caps_the_number_of_frames(tmp_path):
    adapter = load_adapter()
    data = make_clip(tmp_path / "d.mp4", seconds=6).read_bytes()
    frames, meta = adapter.sample_video_frames(data, adapter.VideoOptions(fps=10, max_frames=8, max_dim=640))
    assert len(frames) == meta["total_num_frames"] == 8


def test_adapter_rejects_urls_it_cannot_read():
    adapter = load_adapter()
    with pytest.raises(ValueError, match="data: URLs"):
        adapter._data_url_bytes("https://example.com/clip.mp4")
