"""Frame extraction utilities for VLM-as-judge evaluation.

Uses PyAV to seek directly to evenly-spaced timestamps inside an MP4 and
return decoded ``PIL.Image`` frames. Handles both whole-file and time-windowed
slicing (LeRobot v3.0 packs many episodes into one chunk file).
"""

from __future__ import annotations

import io
import logging
import math
from base64 import b64encode
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image

_LOGGER = logging.getLogger("evaluation.vlm_judge")


@dataclass(frozen=True, slots=True)
class FrameWindow:
    """Episode-aligned window inside a (possibly chunked) video file."""

    path: Path
    from_s: float | None
    to_s: float | None


def extract_frames(
    window: FrameWindow,
    *,
    n_frames: int,
    target_size: tuple[int, int] | None = (480, 480),
) -> list[Image]:
    """Return ``n_frames`` PIL images sampled evenly across the window."""
    import av
    from PIL import Image as PILImage

    if n_frames <= 0:
        raise ValueError(f"n_frames must be positive, got {n_frames}")
    if not window.path.exists():
        raise FileNotFoundError(f"Video not found: {window.path}")

    with av.open(str(window.path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        duration_s = _stream_duration_s(container, stream)
        from_s = max(window.from_s or 0.0, 0.0)
        to_s = window.to_s if window.to_s is not None else duration_s
        if to_s <= from_s:
            raise ValueError(
                f"Invalid window {from_s:.3f}->{to_s:.3f}s for {window.path}",
            )
        timestamps = _evenly_spaced(from_s, to_s, n_frames)
        frames = [_seek_and_decode(container, stream, ts) for ts in timestamps]

    images = [PILImage.fromarray(arr) for arr in frames]
    if target_size is not None:
        images = [_letterbox(img, target_size) for img in images]
    return images


def tile_horizontally(per_view_frames: Sequence[Sequence[Image]]) -> list[Image]:
    """Tile per-view frame sequences side-by-side into composite frames.

    Useful when a model accepts a single image per timestep but the dataset has
    front + wrist cameras; produces N composite frames where each is the views
    horizontally concatenated.
    """
    from PIL import Image as PILImage

    if not per_view_frames:
        raise ValueError("per_view_frames must not be empty")
    n_frames = len(per_view_frames[0])
    if any(len(seq) != n_frames for seq in per_view_frames):
        raise ValueError("All views must produce the same number of frames")

    composites: list[Image] = []
    for t in range(n_frames):
        row = [seq[t] for seq in per_view_frames]
        height = max(img.height for img in row)
        total_width = sum(img.width for img in row)
        canvas = PILImage.new("RGB", (total_width, height))
        x = 0
        for img in row:
            canvas.paste(img, (x, 0))
            x += img.width
        composites.append(canvas)
    return composites


def encode_jpeg_b64(images: Sequence[Image], *, quality: int = 80) -> list[str]:
    """JPEG-encode a list of PIL frames and return base64 strings."""
    encoded: list[str] = []
    for img in images:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=quality)
        encoded.append(b64encode(buf.getvalue()).decode("ascii"))
    return encoded


# -------------------------------------------------------------------------
# Internal helpers
# -------------------------------------------------------------------------


def _stream_duration_s(container: object, stream: object) -> float:
    duration = getattr(stream, "duration", None)
    time_base = getattr(stream, "time_base", None)
    if duration is not None and time_base is not None:
        return float(duration * time_base)
    container_duration = getattr(container, "duration", None)
    if container_duration is not None:
        return float(container_duration) / 1_000_000.0
    raise RuntimeError("Unable to determine video duration")


def _evenly_spaced(from_s: float, to_s: float, n: int) -> list[float]:
    if n == 1:
        return [(from_s + to_s) / 2.0]
    span = to_s - from_s
    # Sample interior points to avoid landing exactly on the last frame, which
    # often produces a black or broken decode. Pad each end by half a step.
    step = span / float(n)
    return [from_s + step * (i + 0.5) for i in range(n)]


def _seek_and_decode(container, stream, timestamp_s: float):
    pts = int(timestamp_s / float(stream.time_base))
    container.seek(pts, any_frame=False, backward=True, stream=stream)
    target = timestamp_s
    last_frame = None
    for frame in container.decode(stream):
        ts = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
        last_frame = frame
        if ts >= target:
            break
    if last_frame is None:
        raise RuntimeError(f"No frame decoded near t={timestamp_s:.3f}s")
    return last_frame.to_ndarray(format="rgb24")


def _letterbox(img: Image, target: tuple[int, int]):
    """Resize ``img`` to fit within ``target`` (W, H), preserving aspect."""
    from PIL import Image as PILImage

    tw, th = target
    if tw <= 0 or th <= 0:
        return img
    scale = min(tw / img.width, th / img.height)
    new_w = max(1, math.floor(img.width * scale))
    new_h = max(1, math.floor(img.height * scale))
    resized = img.resize((new_w, new_h), PILImage.Resampling.BILINEAR)
    if (new_w, new_h) == (tw, th):
        return resized
    canvas = PILImage.new("RGB", (tw, th), color=(0, 0, 0))
    canvas.paste(resized, ((tw - new_w) // 2, (th - new_h) // 2))
    return canvas
