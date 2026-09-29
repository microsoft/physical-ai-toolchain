# cspell:ignore ffprobe luma nostdin
"""Cut frame-exact multi-camera report clips and screen every decoded frame.

Reads a LeRobot v3.0 dataset and the bundle written by observe.py; writes clips, thumbnails,
a last-frame mosaic, per-camera video statistics, and a manifest to a new folder.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import math
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from observe import configure_logging, dataset_files, inputs_digest, sha256_file, write_json

logger = logging.getLogger(__name__)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2

CLIP_HEIGHT = 192
CLIP_CRF = 30
MAX_SIDE_CAMERAS = 3
THUMB_WIDTH = 160
TILE_WIDTH = 64
REPEAT_MAX_DIFF = 1
BLACK_LUMA = 12.0
LAG_SPAN = 8
BACKGROUND = (13, 26, 49)
LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(description="Cut report clips and screen the video of a LeRobot v3.0 dataset")
    parser.add_argument("dataset", type=Path, help="Dataset root that contains meta/info.json")
    parser.add_argument("bundle", type=Path, help="Bundle folder written by observe.py")
    parser.add_argument("-o", "--output", type=Path, required=True, help="New media folder; must not exist")
    parser.add_argument("--primary", help="Camera shown large in the clips (default: most complete camera)")
    parser.add_argument("--height", type=int, default=CLIP_HEIGHT, help="Clip height in pixels")
    parser.add_argument("--crf", type=int, default=CLIP_CRF, help="libx264 constant rate factor")
    parser.add_argument("-j", "--jobs", type=int, default=4, help="Episodes processed in parallel")
    parser.add_argument("--episodes", help="Subset such as 0,3,10-19 for trial runs")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return parser


def even(value: float) -> int:
    return max(2, 2 * round(value / 2))


def parse_episodes(text: str | None, available: list[int]) -> list[int]:
    if not text:
        return available
    wanted: set[int] = set()
    for part in text.split(","):
        first, _, last = part.strip().partition("-")
        wanted.update(range(int(first), int(last or first) + 1))
    return [ep for ep in available if ep in wanted]


def camera_size(shape: list[int] | None, height: int) -> tuple[int, int]:
    """Return an even (width, height) that keeps the camera aspect ratio."""
    if not shape or len(shape) < 2:
        return even(height * 4 / 3), even(height)
    rows, cols = (shape[0], shape[1]) if shape[-1] in (1, 3, 4) else (shape[1], shape[2])
    return even(height * cols / rows), even(height)


def decoder(path: Path, start: float, duration: float, size: tuple[int, int], log) -> subprocess.Popen:
    width, height = size
    command = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.6f}", "-i", str(path), "-t", f"{duration:.6f}"]
    command += ["-an", "-sn", "-vf", f"scale={width}:{height}:flags=area"]
    command += ["-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log)


def encoder(path: Path, size: tuple[int, int], fps: float, crf: int, log) -> subprocess.Popen:
    width, height = size
    command = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}"]
    command += ["-r", f"{fps:g}", "-i", "pipe:0", "-an"]
    command += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-g", str(max(1, round(fps))), "-bf", "2"]
    command += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    return subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log)


def jpeg(frame: np.ndarray, width: int) -> str:
    """Encode one RGB frame as a base64 JPEG through ffmpeg."""
    rows, cols, _ = frame.shape
    command = ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{cols}x{rows}", "-i", "pipe:0"]
    command += ["-frames:v", "1", "-vf", f"scale={width}:-2:flags=area"]
    command += ["-q:v", "4", "-f", "image2", "-c:v", "mjpeg", "pipe:1"]
    result = subprocess.run(command, input=frame.tobytes(), capture_output=True, check=True)
    return base64.b64encode(result.stdout).decode()


def count_frames(path: Path) -> int:
    command = ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0"]
    command += ["-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)]
    return int(subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip())


def lag_frames(motion: np.ndarray, speed: np.ndarray) -> float | None:
    """Cross-correlate image motion with joint speed; positive means the video trails the state."""
    n = min(len(motion), len(speed))
    a, b = motion[:n].astype(np.float64), speed[:n].astype(np.float64)
    if n < 4 * LAG_SPAN or a.std() == 0 or b.std() == 0:
        return None
    a, b = (a - a.mean()) / a.std(), (b - b.mean()) / b.std()
    shifts = np.arange(-LAG_SPAN, LAG_SPAN + 1)
    corr = np.array([np.mean(a[s:] * b[: n - s]) if s >= 0 else np.mean(a[: n + s] * b[-s:]) for s in shifts])
    k = int(np.argmax(corr))
    if k in (0, len(corr) - 1):
        return None
    denom = corr[k - 1] - 2 * corr[k] + corr[k + 1]
    offset = 0.5 * (corr[k - 1] - corr[k + 1]) / denom if denom else 0.0
    return round(float(shifts[k] + offset), 3)


def runs(mask: np.ndarray) -> list[list[int]]:
    out, start = [], None
    for k, flag in enumerate(np.append(mask, False)):
        if flag and start is None:
            start = k
        elif not flag and start is not None:
            out.append([start, k - 1])
            start = None
    return out


class CameraScreen:
    """Accumulate per-frame statistics for one decoded camera stream."""

    def __init__(self) -> None:
        self.luma: list[float] = []
        self.motion: list[float] = [0.0]
        self.repeated: list[bool] = []
        self.sharpness: list[float] = []
        self.previous: np.ndarray | None = None
        self.previous_luma: np.ndarray | None = None

    def add(self, frame: np.ndarray) -> None:
        luma = frame.astype(np.float32) @ LUMA
        self.luma.append(float(luma.mean()))
        lap = np.abs(4 * luma[1:-1, 1:-1] - luma[:-2, 1:-1] - luma[2:, 1:-1] - luma[1:-1, :-2] - luma[1:-1, 2:])
        self.sharpness.append(float(lap.mean()))
        if self.previous is not None:
            self.motion.append(float(np.abs(luma - self.previous_luma).mean()))
            diff = np.abs(frame.astype(np.int16) - self.previous.astype(np.int16)).max()
            self.repeated.append(bool(diff <= REPEAT_MAX_DIFF))
        self.previous, self.previous_luma = frame, luma

    def summary(self, speed: np.ndarray, idle_speed: float) -> dict:
        luma = np.array(self.luma)
        repeated = np.array(self.repeated, dtype=bool)
        moving = speed[1 : len(repeated) + 1] > idle_speed
        return {
            "luma_mean": round(float(luma.mean()), 2) if luma.size else None,
            "luma_p5": round(float(np.percentile(luma, 5)), 2) if luma.size else None,
            "sharpness_median": round(float(np.median(self.sharpness)), 3) if self.sharpness else None,
            "repeated": [[a + 1, b + 1] for a, b in runs(repeated)],
            "repeated_moving": [[a + 1, b + 1] for a, b in runs(repeated[: len(moving)] & moving)],
            "black": runs(luma < BLACK_LUMA),
            "lag_vs_state": lag_frames(np.array(self.motion), speed),
        }


def process(job: dict) -> dict:
    """Decode, screen, compose, and encode one episode."""
    ep, n, fps = job["episode"], job["frames"], job["fps"]
    out = Path(job["output"])
    cameras = job["cameras"]
    frame_bytes = {camera: size[0] * size[1] * 3 for camera, size in job["sizes"].items()}
    result: dict = {"episode": ep, "frames": n, "cameras": {}, "clip": None, "thumbs": None, "tile": None}
    windows = job["windows"]
    if windows.get(cameras[0]) is None:
        for camera in cameras:
            result["cameras"][camera] = {"available": windows.get(camera) is not None}
        return result
    with tempfile.TemporaryFile() as log:
        decoders = {}
        for camera in cameras:
            window = windows.get(camera)
            if window is None:
                continue
            start = max(0.0, window["from"] - 0.25 / fps)
            source = Path(job["dataset"]) / window["file"]
            span = window["to"] - window["from"]
            decoders[camera] = decoder(source, start, span, job["sizes"][camera], log)
        clip_path = out / "clips" / f"episode_{ep:06d}.mp4"
        canvas_size = job["canvas"]
        encode = encoder(clip_path, canvas_size, fps, job["crf"], log)
        screens = {camera: CameraScreen() for camera in decoders}
        decoded = dict.fromkeys(decoders, 0)
        last: dict[str, np.ndarray | None] = dict.fromkeys(decoders)
        first_primary = last_primary = None
        canvas = np.empty((canvas_size[1], canvas_size[0], 3), dtype=np.uint8)
        for k in range(n):
            canvas[:] = BACKGROUND
            for camera, proc in decoders.items():
                width, height = job["sizes"][camera]
                raw = proc.stdout.read(frame_bytes[camera]) if decoded[camera] == k else b""
                if len(raw) == frame_bytes[camera]:
                    frame = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
                    decoded[camera] += 1
                    screens[camera].add(frame)
                    last[camera] = frame
                frame = last[camera]
                if frame is None:
                    continue
                x, y = job["offsets"][camera]
                canvas[y : y + height, x : x + width] = frame
            primary = last[cameras[0]]
            if k == 0:
                first_primary = primary
            last_primary = primary
            encode.stdin.write(canvas.tobytes())
        encode.stdin.close()
        extra = {}
        for camera, proc in decoders.items():
            remaining = 0
            if decoded[camera] == n:
                while len(proc.stdout.read(frame_bytes[camera])) == frame_bytes[camera]:
                    remaining += 1
            proc.stdout.close()
            proc.wait()
            extra[camera] = remaining
        if encode.wait() != 0 or any(proc.returncode not in (0, None) for proc in decoders.values()):
            log.seek(0)
            raise RuntimeError(f"episode {ep}: ffmpeg failed: {log.read().decode(errors='replace')[-400:]}")
    speed = np.asarray(job["speed"], dtype=np.float64)
    for camera in cameras:
        if camera not in decoders:
            result["cameras"][camera] = {"available": False}
            continue
        window = windows[camera]
        result["cameras"][camera] = {
            "available": True,
            "decoded": decoded[camera] + extra[camera],
            "window_frames": round((window["to"] - window["from"]) * fps),
            "shortfall": n - decoded[camera],
            "extra": extra[camera],
            **screens[camera].summary(speed, job["idle_speed"]),
        }
    frames = count_frames(clip_path)
    if frames != n:
        raise RuntimeError(f"episode {ep}: clip has {frames} frames, expected {n}")
    result["clip"] = {
        "path": clip_path.name,
        "frames": frames,
        "bytes": clip_path.stat().st_size,
        "sha256": sha256_file(clip_path),
    }
    if first_primary is not None and last_primary is not None:
        result["thumbs"] = {"first": jpeg(first_primary, THUMB_WIDTH), "last": jpeg(last_primary, THUMB_WIDTH)}
        rows, cols, _ = last_primary.shape
        tile_height = even(TILE_WIDTH * rows / cols)
        ys = np.linspace(0, rows - 1, tile_height).astype(int)
        xs = np.linspace(0, cols - 1, TILE_WIDTH).astype(int)
        result["tile"] = base64.b64encode(last_primary[np.ix_(ys, xs)].tobytes()).decode()
        result["tile_size"] = [TILE_WIDTH, tile_height]
    return result


def build_mosaic(results: list[dict], path: Path) -> dict | None:
    """Tile the last primary frame of every episode into one JPEG."""
    tiles = [r for r in results if r.get("tile")]
    if not tiles:
        return None
    width, height = tiles[0]["tile_size"]
    columns = min(20, max(1, math.ceil(math.sqrt(len(tiles) * 1.6))))
    rows = math.ceil(len(tiles) / columns)
    sheet = np.full((rows * (height + 2), columns * (width + 2), 3), BACKGROUND, dtype=np.uint8)
    for k, result in enumerate(tiles):
        tile = np.frombuffer(base64.b64decode(result["tile"]), np.uint8).reshape(height, width, 3)
        y, x = (k // columns) * (height + 2) + 1, (k % columns) * (width + 2) + 1
        sheet[y : y + height, x : x + width] = tile
    size = f"{sheet.shape[1]}x{sheet.shape[0]}"
    command = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", size, "-i", "pipe:0"]
    command += ["-frames:v", "1", "-q:v", "4", "-c:v", "mjpeg", str(path)]
    subprocess.run(command, input=sheet.tobytes(), check=True, capture_output=True)
    return {"path": path.name, "columns": columns, "episodes": [r["episode"] for r in tiles]}


def layout_cameras(metrics: dict, primary: str | None, height: int) -> tuple[list[str], dict, dict, tuple[int, int]]:
    """Choose clip cameras and place them: the primary on the left, the rest stacked on the right."""
    cameras = [c for c in metrics["dataset"]["cameras"] if c["episodes_with_video"] > 0]
    if not cameras:
        raise ValueError("no camera has video files; nothing to cut")
    by_key = {c["key"]: c for c in cameras}
    if primary:
        if primary not in by_key:
            raise ValueError(f"camera {primary} has no video files")
        chosen = by_key[primary]
    else:
        chosen = max(cameras, key=lambda c: c["episodes_with_video"])
    side = [c for c in cameras if c["key"] != chosen["key"]][:MAX_SIDE_CAMERAS]
    order = [chosen["key"], *[c["key"] for c in side]]
    sizes = {chosen["key"]: camera_size(chosen["shape"], height)}
    offsets = {chosen["key"]: (0, 0)}
    if side:
        cell = even(height / len(side))
        column = 0
        for k, camera in enumerate(side):
            sizes[camera["key"]] = camera_size(camera["shape"], cell)
            offsets[camera["key"]] = (sizes[chosen["key"]][0], k * cell)
            column = max(column, sizes[camera["key"]][0])
        canvas = (sizes[chosen["key"]][0] + column, sizes[chosen["key"]][1])
    else:
        canvas = sizes[chosen["key"]]
    return order, sizes, offsets, canvas


def verify_inputs(root: Path, manifest: dict) -> list[str]:
    """Return the dataset files that differ from the bundle inventory."""
    expected = manifest["inputs"]
    current = {path.relative_to(root).as_posix(): path for path in dataset_files(root)}
    problems = sorted(set(expected) ^ set(current))
    problems += [rel for rel in sorted(set(expected) & set(current)) if sha256_file(current[rel]) != expected[rel]]
    return problems


def run(args: argparse.Namespace) -> int:
    if args.output.exists():
        logger.error("output %s exists; choose a new folder", args.output)
        return EXIT_ERROR
    manifest = json.loads((args.bundle / "manifest.json").read_text(encoding="utf-8"))
    logger.info("verifying %d dataset files against the bundle", len(manifest["inputs"]))
    problems = verify_inputs(args.dataset, manifest)
    if problems:
        logger.error("dataset differs from the bundle inputs (%s); recompute the bundle", ", ".join(problems[:5]))
        return EXIT_FAILURE
    metrics = json.loads((args.bundle / "metrics.json").read_text(encoding="utf-8"))
    records = {r["episode"]: r for r in json.loads((args.bundle / "episodes.json").read_text(encoding="utf-8"))}
    series = json.loads((args.bundle / "series.json").read_text(encoding="utf-8"))
    try:
        order, sizes, offsets, canvas = layout_cameras(metrics, args.primary, even(args.height))
    except ValueError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR
    fps = float(metrics["dataset"]["fps"])
    wanted = parse_episodes(args.episodes, sorted(records))
    (args.output / "clips").mkdir(parents=True)
    jobs = []
    for ep in wanted:
        record = records[ep]
        windows = {}
        for camera in order:
            window = record["videos"].get(camera)
            windows[camera] = window if window and window["exists"] else None
        jobs.append(
            {
                "episode": ep,
                "frames": record["frames"],
                "fps": fps,
                "dataset": str(args.dataset),
                "output": str(args.output),
                "cameras": order,
                "windows": windows,
                "sizes": sizes,
                "offsets": offsets,
                "canvas": canvas,
                "crf": args.crf,
                "speed": series[str(ep)]["speed"],
                "idle_speed": float(metrics["motion"]["idle_speed"]),
            }
        )
    results = []
    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for k, result in enumerate(pool.map(process, jobs), 1):
            results.append(result)
            if k % 25 == 0 or k == len(jobs):
                logger.info("processed %d of %d episodes", k, len(jobs))
    mosaic = build_mosaic(results, args.output / "mosaic.jpg")
    for result in results:
        result.pop("tile", None)
        result.pop("tile_size", None)
    write_json(args.output / "video.json", results, compact=True)
    clips = [r["clip"] for r in results if r["clip"]]
    out_manifest = {
        "tool": Path(__file__).name,
        "tool_sha256": sha256_file(Path(__file__)),
        "inputs_digest": inputs_digest(manifest["inputs"]),
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "episodes": wanted,
        "clip": {
            "cameras": order,
            "sizes": {camera: list(size) for camera, size in sizes.items()},
            "canvas": list(canvas),
            "codec": "libx264 yuv420p",
            "crf": args.crf,
            "gop": max(1, round(fps)),
        },
        "screens": {"repeat_max_diff": REPEAT_MAX_DIFF, "black_luma": BLACK_LUMA, "lag_span_frames": LAG_SPAN},
        "clips": len(clips),
        "clips_bytes": sum(clip["bytes"] for clip in clips),
        "mosaic": mosaic,
    }
    write_json(args.output / "manifest.json", out_manifest)
    missing = [r["episode"] for r in results if not r["clip"]]
    logger.info("wrote %s (%d clips, %.1f MB)", args.output, len(clips), out_manifest["clips_bytes"] / 1e6)
    if missing:
        logger.warning("no video for the primary camera in %d episodes: %s", len(missing), missing[:10])
    return EXIT_SUCCESS


def main() -> int:
    """Main entry point."""
    args = create_parser().parse_args()
    configure_logging(args.verbose)
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        logger.error("%s", exc)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
