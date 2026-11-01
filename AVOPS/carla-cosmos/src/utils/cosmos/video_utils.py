"""Video utility helpers used by prep and inference stages."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def require_ffmpeg() -> str:
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        raise RuntimeError("ffmpeg is required for video resampling.")
    return ffmpeg_bin


def resample_video(
    source_path: Path,
    output_path: Path,
    *,
    fps: int,
    width: int | None = None,
    height: int | None = None,
) -> Path:
    """Resample a video to a target FPS and optional resolution."""
    ffmpeg_bin = require_ffmpeg()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filters = [f"fps={fps}"]
    if width is not None and height is not None:
        filters.append(f"scale={width}:{height}:flags=lanczos")
    command = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-vf",
        ",".join(filters),
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "18",
        "-preset",
        "fast",
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg failed while resampling {source_path}:\n{exc.stderr}") from exc
    return output_path
