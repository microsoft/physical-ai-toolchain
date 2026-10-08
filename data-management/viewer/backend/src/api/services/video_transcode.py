# cspell:ignore ffprobe veryfast
"""
On-demand video transcoding to a browser-compatible codec.

Some datasets store videos in codecs (e.g. MPEG-4 Part 2 / `mpeg4`) that
HTML5 ``<video>`` elements cannot decode. This module probes a source file
with ``ffprobe`` and, when needed, transcodes it once into H.264 + AAC MP4
using ``ffmpeg``. Results are cached on disk so subsequent requests serve
the converted file directly via ``FileResponse`` (which gives free Range
support for seeking).
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# Codecs that Chromium, Firefox, and Safari can play in an MP4/WebM container.
_WEB_COMPATIBLE_VIDEO_CODECS = frozenset({"h264", "vp8", "vp9", "av1", "hevc"})

# Cache for transcoded outputs. Survives across requests within a process
# lifetime; cleared when the container restarts.
_CACHE_DIR = (
    Path(os.environ.get("VIDEO_TRANSCODE_CACHE_DIR", tempfile.gettempdir())) / f"dvw_video_cache_{uuid.uuid4().hex}"
)
_CACHE_MAX_BYTES = 1024 * 1024 * 1024
_capacity_guard = asyncio.Lock()
atexit.register(shutil.rmtree, _CACHE_DIR, ignore_errors=True)

# Per-cache-key locks so concurrent requests for the same video only run
# ffmpeg once.
_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


def _have_tool(name: str) -> bool:
    return shutil.which(name) is not None


def transcoding_available() -> bool:
    """Return True when both ffprobe and ffmpeg are installed."""
    return _have_tool("ffprobe") and _have_tool("ffmpeg")


async def _get_lock(key: str) -> asyncio.Lock:
    async with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _locks[key] = lock
        return lock


async def _probe_video_codec(path: Path) -> str | None:
    """Return the first video stream's codec_name, or None on failure."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(stdout.decode("utf-8") or "{}")
        streams = data.get("streams") or []
        if streams:
            return streams[0].get("codec_name")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return None


def _cache_key(source: Path) -> str:
    """Build a stable cache key from absolute path + size + mtime."""
    try:
        st = source.stat()
        payload = f"{source.resolve()}::{st.st_dev}::{st.st_ino}::{st.st_size}::{st.st_mtime_ns}::{st.st_ctime_ns}"
    except OSError:
        payload = str(source.resolve())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _transcode_to_h264(source: Path, target: Path, max_bytes: int) -> bool:
    """Run ffmpeg, writing H.264 + AAC MP4 with faststart to ``target``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=target.stem, suffix=".part", dir=target.parent)
    os.close(descriptor)
    tmp = Path(temporary)
    source_key = _cache_key(source)
    cmd = [
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
        "-fs",
        str(max_bytes),
        str(tmp),
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0 or not 0 < tmp.stat().st_size < max_bytes or _cache_key(source) != source_key:
            _LOGGER.warning("Video conversion unavailable or exceeded its source/storage boundary")
            return False
        tmp.replace(target)
        return True
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        tmp.unlink(missing_ok=True)


async def ensure_browser_compatible(source: Path) -> Path:
    """Return a path to a browser-playable copy of ``source``.

    If the source already uses a web-compatible codec, returns it unchanged.
    Otherwise transcodes once into the on-disk cache and returns the cached
    path. On any failure (ffmpeg missing, probe failed, transcode failed),
    returns the original source so the caller can still attempt to serve it.
    """
    if not transcoding_available():
        return source

    codec = await _probe_video_codec(source)
    if codec is None or codec.lower() in _WEB_COMPATIBLE_VIDEO_CODECS:
        return source

    key = _cache_key(source)
    cached = _CACHE_DIR / f"{key}.mp4"
    if cached.exists() and cached.stat().st_size > 0:
        return cached

    lock = await _get_lock(key)
    async with lock, _capacity_guard:
        if cached.exists() and cached.stat().st_size > 0:
            return cached
        used = sum(path.stat().st_size for path in _CACHE_DIR.glob("*") if path.is_file())
        available = _CACHE_MAX_BYTES - used
        if available <= 0:
            _LOGGER.warning("Video conversion cache capacity exhausted")
            return source
        _LOGGER.info("Transcoding %s (codec=%s) to H.264 cache %s", source, codec, cached)
        ok = await _transcode_to_h264(source, cached, available)
        if not ok:
            return source
        return cached
