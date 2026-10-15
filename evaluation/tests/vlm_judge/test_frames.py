"""Real-media sampling and image-layout contracts for judge frames."""

from __future__ import annotations

import base64
import io
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from vlm_judge.frames import (
    FrameWindow,
    MediaInputError,
    _stream_duration_s,
    encode_jpeg_b64,
    extract_frames,
    tile_horizontally,
)


@pytest.mark.parametrize("count", [0, -1])
def test_given_nonpositive_count_when_extracting_then_rejects_sampling(rollout_video: Path, count: int) -> None:
    with pytest.raises(MediaInputError, match=r"^Invalid media input$") as error:
        extract_frames(FrameWindow(rollout_video, None, None), n_frames=count)

    assert isinstance(error.value.__cause__, ValueError)
    assert "n_frames must be positive" in str(error.value.__cause__)


def test_given_missing_video_when_extracting_then_reports_missing_media(tmp_path: Path) -> None:
    with pytest.raises(MediaInputError, match=r"^Invalid media input$") as error:
        extract_frames(FrameWindow(tmp_path / "missing.mp4", None, None), n_frames=1)

    assert isinstance(error.value.__cause__, FileNotFoundError)
    assert "Video not found" in str(error.value.__cause__)


@pytest.mark.parametrize("window", [(0.5, 0.5), (0.75, 0.25), (0, 0)])
def test_given_reversed_or_empty_window_when_extracting_then_rejects_window(
    rollout_video: Path, window: tuple[float, float]
) -> None:
    with pytest.raises(MediaInputError, match=r"^Invalid media input$") as error:
        extract_frames(FrameWindow(rollout_video, *window), n_frames=3)

    assert isinstance(error.value.__cause__, ValueError)
    assert "Invalid window" in str(error.value.__cause__)


@pytest.mark.parametrize("size", [None, (0, 32), (24, 16), (32, 32)])
def test_given_window_when_extracting_then_samples_real_frames_and_preserves_aspect(
    rollout_video: Path, size: tuple[int, int] | None
) -> None:
    images = extract_frames(FrameWindow(rollout_video, 0.25, 0.75), n_frames=3, target_size=size)

    expected_size = (32, 32) if size == (32, 32) else (24, 16)
    assert [image.size for image in images] == [expected_size] * 3
    intensities = [image.getpixel((image.width // 2, image.height // 2))[0] for image in images]
    assert intensities == pytest.approx([80, 120, 160], abs=4)
    if size == (32, 32):
        assert images[-1].getpixel((0, 0)) == (0, 0, 0)


def test_given_one_sample_and_negative_start_when_extracting_then_samples_window_midpoint(rollout_video: Path) -> None:
    [image] = extract_frames(FrameWindow(rollout_video, -0.5, None), n_frames=1, target_size=None)

    assert image.size == (24, 16)
    assert image.getpixel((12, 8))[0] == pytest.approx(120, abs=4)


def test_given_different_camera_sizes_when_tiling_then_preserves_each_view_and_pads_background() -> None:
    left = Image.new("RGB", (12, 8), "red")
    right = Image.new("RGB", (8, 12), "green")

    [composite] = tile_horizontally([[left], [right]])

    assert composite.size == (20, 12)
    assert composite.getpixel((0, 0)) == (255, 0, 0)
    assert composite.getpixel((12, 0)) == (0, 128, 0)
    assert composite.getpixel((0, 11)) == (0, 0, 0)


@pytest.mark.parametrize("views", [[], [[Image.new("RGB", (8, 8))], []]])
def test_given_missing_or_unequal_views_when_tiling_then_rejects_input(views: list[list[Image.Image]]) -> None:
    with pytest.raises(ValueError):
        tile_horizontally(views)


def test_given_empty_camera_sequences_when_tiling_then_returns_no_frames() -> None:
    assert tile_horizontally([[], []]) == []


def test_given_non_rgb_image_when_encoding_then_returns_decodable_rgb_jpeg() -> None:
    image = Image.new("RGBA", (10, 12), (240, 20, 10, 0))

    [encoded] = encode_jpeg_b64([image])

    with Image.open(io.BytesIO(base64.b64decode(encoded))) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.mode == "RGB"
        assert decoded.size == image.size
        assert decoded.getpixel((0, 0)) == pytest.approx((240, 20, 10), abs=4)
    assert encode_jpeg_b64([]) == []


@pytest.mark.parametrize(
    ("stream", "container", "seconds"),
    [
        (SimpleNamespace(duration=2, time_base=Fraction(1, 4)), SimpleNamespace(duration=2_000_000), 0.5),
        (SimpleNamespace(duration=None, time_base=None), SimpleNamespace(duration=2_000_000), 2.0),
    ],
)
def test_given_video_timing_metadata_when_reading_duration_then_prefers_stream_and_falls_back_to_container(
    stream: SimpleNamespace, container: SimpleNamespace, seconds: float
) -> None:
    assert _stream_duration_s(container, stream) == seconds


def test_given_no_timing_metadata_when_reading_duration_then_reports_unusable_video() -> None:
    with pytest.raises(RuntimeError, match="Unable to determine video duration"):
        _stream_duration_s(SimpleNamespace(), SimpleNamespace())
