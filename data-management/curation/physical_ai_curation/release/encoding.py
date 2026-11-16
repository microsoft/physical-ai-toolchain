"""Bounded CPU PyAV encoding of explicit source frame windows into a binary sink.

Reads one binary source stream and writes one binary MP4 sink. The primitive opens no
paths, publishes nothing, and admits no durable jobs. PyAV and NumPy load only inside
operations, so importing this module stays optional-free.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise
from typing import Any, BinaryIO

from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.media import ReleaseExecutionError, ReleaseMediaLimits, ReleasePlanError

ENCODER_CODEC = "libx264"
ENCODER_PIX_FMT = "yuv420p"
ENCODER_PRESET = "medium"
MAX_SAMPLES_PER_WINDOW = 256
SUPPORTED_SOURCE_PIX_FMTS = frozenset({"yuv420p", "yuvj420p", "yuv422p", "yuv444p", "rgb24", "bgr24", "gray"})
STATISTIC_ALGORITHM = (
    "population min, max, mean, and std per RGB channel over sampled frames, "
    "normalized by 255 with float64 accumulation; quantiles are not computed"
)
_CHANNELS = 3
_WORKING_BYTES_PER_PIXEL = 64
_PTS_TOLERANCE_FRAMES = 0.1
_RATE_RELATIVE_TOLERANCE = 1e-3
_RATIONAL_LIMIT = 1_000_000


@dataclass(frozen=True, slots=True)
class EncodingWindow:
    episode_id: str
    from_timestamp: float
    source_frame_count: int
    kept_frames: tuple[int, ...]
    color_filter: str = "none"

    def __post_init__(self) -> None:
        if not isinstance(self.episode_id, str) or not self.episode_id:
            raise ReleasePlanError("window episode identifier must be non-empty")
        if isinstance(self.from_timestamp, bool) or not isinstance(self.from_timestamp, (int, float)):
            raise ReleasePlanError("window start timestamp must be a number")
        if not math.isfinite(self.from_timestamp) or self.from_timestamp < 0:
            raise ReleasePlanError("window start timestamp must be finite and nonnegative")
        if type(self.source_frame_count) is not int or self.source_frame_count <= 0:
            raise ReleasePlanError("window source frame count must be a positive integer")
        if type(self.kept_frames) is not tuple or not self.kept_frames:
            raise ReleasePlanError("window must keep at least one frame in a tuple")
        if any(type(index) is not int or not 0 <= index < self.source_frame_count for index in self.kept_frames):
            raise ReleasePlanError("kept frame index is outside the source window")
        if any(later <= earlier for earlier, later in pairwise(self.kept_frames)):
            raise ReleasePlanError("kept frames must be strictly increasing")
        if self.color_filter not in ("none", "invert", "grayscale"):
            raise ReleasePlanError("unsupported window color filter")


@dataclass(frozen=True, slots=True)
class EncodeOptions:
    crf: int = 23
    gop: int = 50
    flip_horizontal: bool = False
    flip_vertical: bool = False
    max_samples_per_window: int = 32

    def __post_init__(self) -> None:
        if type(self.crf) is not int or not 0 <= self.crf <= 51:
            raise ReleasePlanError("crf must be an integer from 0 to 51")
        if type(self.gop) is not int or self.gop < 1:
            raise ReleasePlanError("gop must be a positive integer")
        if type(self.flip_horizontal) is not bool or type(self.flip_vertical) is not bool:
            raise ReleasePlanError("flip options must be booleans")
        if (
            type(self.max_samples_per_window) is not int
            or not 1 <= self.max_samples_per_window <= MAX_SAMPLES_PER_WINDOW
        ):
            raise ReleasePlanError(f"max samples per window must be from 1 to {MAX_SAMPLES_PER_WINDOW}")


@dataclass(frozen=True, slots=True)
class EncodedWindow:
    episode_id: str
    output_from_frame: int
    output_to_frame: int
    output_from_s: float
    output_to_s: float
    statistics: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VideoOutput:
    windows: tuple[EncodedWindow, ...]
    codec: str
    pix_fmt: str
    fps: float
    width: int
    height: int
    statistic_algorithm: str
    sampling_recipe: str


@dataclass(frozen=True, slots=True)
class _Session:
    av: Any
    np: Any
    container: Any
    stream: Any
    output: Any
    out_stream: Any
    settings: EncodeOptions
    fps: float
    time_base: Fraction
    width: int
    height: int


def encode_windows(
    source: BinaryIO,
    sink: BinaryIO,
    windows: Sequence[EncodingWindow],
    *,
    fps: float | Fraction,
    options: EncodeOptions | None = None,
    limits: ReleaseMediaLimits | None = None,
) -> VideoOutput:
    """Encode windows in output order, raising instead of approximating missing or drifting frames."""
    ordered = _validate_windows(windows)
    fps_value, rate = _checked_fps(fps)
    settings = options if options is not None else EncodeOptions()
    budget = (limits if limits is not None else ReleaseMediaLimits()).processing_memory_bytes

    import av
    import numpy as np

    try:
        with av.open(source, mode="r") as container:
            stream = _source_stream(container, fps_value)
            width = int(stream.codec_context.width)
            height = int(stream.codec_context.height)
            _check_geometry(width, height, budget)
            with av.open(sink, mode="w", format="mp4") as output:
                out_stream = output.add_stream(ENCODER_CODEC, rate=rate)
                out_stream.width = width
                out_stream.height = height
                out_stream.pix_fmt = ENCODER_PIX_FMT
                out_stream.options = _encoder_options(settings)
                session = _Session(
                    av, np, container, stream, output, out_stream, settings, fps_value, 1 / rate, width, height
                )
                encoded = []
                first_frame = 0
                for window in ordered:
                    result = _encode_window(session, window, first_frame)
                    encoded.append(result)
                    first_frame = result.output_to_frame
                for packet in out_stream.encode():
                    output.mux(packet)
                return VideoOutput(
                    windows=tuple(encoded),
                    codec=ENCODER_CODEC,
                    pix_fmt=ENCODER_PIX_FMT,
                    fps=fps_value,
                    width=width,
                    height=height,
                    statistic_algorithm=STATISTIC_ALGORITHM,
                    sampling_recipe=_sampling_recipe(settings),
                )
    except ReleaseVerificationError:
        raise
    except (av.FFmpegError, OSError, ValueError):
        raise ReleaseExecutionError("media encoding refused: source or encoder is invalid") from None


def _validate_windows(windows: Sequence[EncodingWindow]) -> tuple[EncodingWindow, ...]:
    ordered = tuple(windows)
    if not ordered:
        raise ReleasePlanError("at least one window is required")
    if not all(isinstance(window, EncodingWindow) for window in ordered):
        raise ReleasePlanError("windows must be EncodingWindow values")
    identifiers = [window.episode_id for window in ordered]
    if len(set(identifiers)) != len(identifiers):
        raise ReleasePlanError("window episode identifiers must be unique")
    return ordered


def _checked_fps(fps: float | Fraction) -> tuple[float, Fraction]:
    if isinstance(fps, bool) or not isinstance(fps, (int, float, Fraction)):
        raise ReleasePlanError("fps must be a finite positive number")
    value = float(fps)
    if not math.isfinite(value) or value <= 0:
        raise ReleasePlanError("fps must be a finite positive number")
    return value, Fraction(fps).limit_denominator(_RATIONAL_LIMIT)


def _source_stream(container: Any, fps: float) -> Any:
    streams = container.streams.video
    if not streams:
        raise ReleaseExecutionError("source has no video stream")
    stream = streams[0]
    actual = stream.average_rate
    if actual is None or abs(float(actual) - fps) > _RATE_RELATIVE_TOLERANCE * fps:
        raise ReleaseExecutionError("source fps is missing or does not match the requested fps")
    if stream.codec_context.pix_fmt not in SUPPORTED_SOURCE_PIX_FMTS:
        raise ReleasePlanError("unsupported source depth or pixel format")
    return stream


def _check_geometry(width: int, height: int, budget: int) -> None:
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise ReleasePlanError("output dimensions must be positive and even for yuv420p")
    if width * height * _WORKING_BYTES_PER_PIXEL > budget:
        raise ReleasePlanError("decoded frame working set exceeds the processing memory budget")


def _encoder_options(settings: EncodeOptions) -> dict[str, str]:
    return {
        "crf": str(settings.crf),
        "preset": ENCODER_PRESET,
        "g": str(settings.gop),
        "keyint_min": str(settings.gop),
        "sc_threshold": "0",
        "threads": "1",
    }


def _sampling_recipe(settings: EncodeOptions) -> str:
    return (
        "per window: local output frames at indices 0, s, 2s, ... with s = ceil(kept / "
        f"{settings.max_samples_per_window}), at most {settings.max_samples_per_window} samples per window, "
        "taken from post-transform RGB frames before encoding"
    )


def _decoded_frames(session: _Session, window: EncodingWindow) -> Iterator[Any]:
    time_base = float(session.stream.time_base)
    start = int(session.stream.start_time or 0)
    tolerance = _PTS_TOLERANCE_FRAMES / session.fps
    session.container.seek(
        start + round(window.from_timestamp / time_base), stream=session.stream, backward=True, any_frame=False
    )
    produced = 0
    for frame in session.container.decode(session.stream):
        if frame.pts is None:
            raise ReleaseExecutionError("source frame lacks a presentation timestamp")
        relative = (frame.pts - start) * time_base
        if relative < window.from_timestamp - tolerance:
            continue
        if abs(relative - (window.from_timestamp + produced / session.fps)) > tolerance:
            raise ReleaseExecutionError("source timestamps have a gap or drift from the window")
        yield frame
        produced += 1
        if produced == window.source_frame_count:
            return
    raise ReleaseExecutionError("source ended before the window's frame count")


def _oriented_rgb(session: _Session, frame: Any, color_filter: str) -> Any:
    pixels = frame.reformat(format="rgb24").to_ndarray()
    if color_filter == "invert":
        pixels = 255 - pixels
    elif color_filter == "grayscale":
        from PIL import Image, ImageOps

        pixels = session.np.asarray(ImageOps.grayscale(Image.fromarray(pixels)).convert("RGB"))
    if session.settings.flip_vertical:
        pixels = pixels[::-1]
    if session.settings.flip_horizontal:
        pixels = pixels[:, ::-1]
    return session.np.ascontiguousarray(pixels)


def _emit(session: _Session, pixels: Any, index: int) -> None:
    frame = session.av.VideoFrame.from_ndarray(pixels, format="rgb24").reformat(format=ENCODER_PIX_FMT)
    frame.pts = index
    frame.time_base = session.time_base
    for packet in session.out_stream.encode(frame):
        session.output.mux(packet)


def _encode_window(session: _Session, window: EncodingWindow, first_frame: int) -> EncodedWindow:
    np = session.np
    kept = frozenset(window.kept_frames)
    stride = math.ceil(len(window.kept_frames) / session.settings.max_samples_per_window)
    sums = np.zeros(_CHANNELS)
    squares = np.zeros(_CHANNELS)
    lows = np.full(_CHANNELS, np.inf)
    highs = np.full(_CHANNELS, -np.inf)
    sampled = 0
    local = 0
    for index, frame in enumerate(_decoded_frames(session, window)):
        if index not in kept:
            continue
        pixels = _oriented_rgb(session, frame, window.color_filter)
        if local % stride == 0:
            values = pixels.reshape(-1, _CHANNELS).astype(np.float64) / 255.0
            sums += values.sum(axis=0)
            squares += np.square(values).sum(axis=0)
            lows = np.minimum(lows, values.min(axis=0))
            highs = np.maximum(highs, values.max(axis=0))
            sampled += 1
        _emit(session, pixels, first_frame + local)
        local += 1
    pixel_total = sampled * session.width * session.height
    mean = sums / pixel_total
    std = np.sqrt(np.maximum(squares / pixel_total - mean * mean, 0.0))
    shape = (_CHANNELS, 1, 1)
    last_frame = first_frame + local
    return EncodedWindow(
        episode_id=window.episode_id,
        output_from_frame=first_frame,
        output_to_frame=last_frame,
        output_from_s=first_frame / session.fps,
        output_to_s=last_frame / session.fps,
        statistics={
            "count": [sampled],
            "min": lows.reshape(shape).tolist(),
            "max": highs.reshape(shape).tolist(),
            "mean": mean.reshape(shape).tolist(),
            "std": std.reshape(shape).tolist(),
        },
    )
