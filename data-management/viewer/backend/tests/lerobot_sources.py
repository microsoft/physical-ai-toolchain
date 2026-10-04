"""A two-episode LeRobot v3.0 source and a hard-stopped export, shared by the export tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from src.api.services import lerobot_exporter
from src.api.services.lerobot_exporter import LOCK_FILE

FPS = 10
WIDTH, HEIGHT = 32, 24
CAMERA = "observation.images.front"
LENGTHS = (12, 8)


def frame_gray(episode: int, frame: int) -> int:
    return 40 + 15 * frame if episode == 0 else 200 - 15 * frame


def frame_state(episode: int, frame: int) -> list[float]:
    return [float(episode), float(frame), float(episode * 100 + frame)]


def write_source(root: Path, vector: pa.DataType | None = None) -> Path:
    """Write a two-episode v3.0 dataset whose frames and rows encode their episode and frame."""
    (root / "meta/episodes/chunk-000").mkdir(parents=True)
    (root / "data/chunk-000").mkdir(parents=True)
    video = root / f"videos/{CAMERA}/chunk-000/file-000.mp4"
    video.parent.mkdir(parents=True)
    with av.open(str(video), "w") as container:
        stream = container.add_stream("libx264", rate=FPS, options={"g": "2", "crf": "18"})
        stream.width, stream.height, stream.pix_fmt = WIDTH, HEIGHT, "yuv420p"
        for episode, length in enumerate(LENGTHS):
            for frame in range(length):
                image = np.full((HEIGHT, WIDTH, 3), frame_gray(episode, frame), dtype=np.uint8)
                for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
                    container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    rows: dict[str, list[Any]] = {name: [] for name in ("state", "phase", "flag", "timestamp", "frame", "episode")}
    episodes = []
    offset = 0
    for episode, length in enumerate(LENGTHS):
        for frame in range(length):
            rows["state"].append(frame_state(episode, frame))
            rows["phase"].append(frame // 4)
            rows["flag"].append(frame % 2 == 0)
            rows["timestamp"].append(frame / FPS)
            rows["frame"].append(frame)
            rows["episode"].append(episode)
        episodes.append(
            {
                "episode_index": episode,
                "tasks": ["pick the part"],
                "length": length,
                "data/chunk_index": 0,
                "data/file_index": 0,
                "dataset_from_index": offset,
                "dataset_to_index": offset + length,
                f"videos/{CAMERA}/chunk_index": 0,
                f"videos/{CAMERA}/file_index": 0,
                f"videos/{CAMERA}/from_timestamp": offset / FPS,
                f"videos/{CAMERA}/to_timestamp": (offset + length) / FPS,
                "meta/episodes/chunk_index": 0,
                "meta/episodes/file_index": 0,
                "stats/observation.state/count": [length],
            }
        )
        offset += length
    vector = vector or pa.list_(pa.float32(), 3)
    total = len(rows["frame"])
    data = pa.table(
        {
            "observation.state": pa.array(rows["state"], type=vector),
            "action": pa.array([[2 * value for value in state] for state in rows["state"]], type=vector),
            "observation.phase": pa.array(rows["phase"], type=pa.int64()),
            "observation.flag": pa.array(rows["flag"], type=pa.bool_()),
            "timestamp": pa.array(rows["timestamp"], type=pa.float32()),
            "frame_index": pa.array(rows["frame"], type=pa.int64()),
            "episode_index": pa.array(rows["episode"], type=pa.int64()),
            "index": pa.array(range(total), type=pa.int64()),
            "task_index": pa.array([0] * total, type=pa.int64()),
        }
    )
    pq.write_table(data, root / "data/chunk-000/file-000.parquet")
    pq.write_table(pa.Table.from_pylist(episodes), root / "meta/episodes/chunk-000/file-000.parquet")
    pq.write_table(pa.table({"task_index": [0], "task": ["pick the part"]}), root / "meta/tasks.parquet")
    scalar = {"shape": [1], "names": None}
    names = ["x", "y", "z"]
    info = {
        "codebase_version": "v3.0",
        "robot_type": "fixture",
        "total_episodes": len(LENGTHS),
        "total_frames": total,
        "total_tasks": 1,
        "chunks_size": 1000,
        "fps": FPS,
        "splits": {"train": f"0:{len(LENGTHS)}"},
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {
            CAMERA: {
                "dtype": "video",
                "shape": [HEIGHT, WIDTH, 3],
                "names": ["height", "width", "channels"],
                "info": {
                    "video.height": HEIGHT,
                    "video.width": WIDTH,
                    "video.codec": "h264",
                    "video.pix_fmt": "yuv420p",
                    "video.fps": FPS,
                    "video.channels": 3,
                    "video.g": 2,
                    "video.crf": 18,
                    "has_audio": False,
                },
            },
            "observation.state": {"dtype": "float32", "shape": [3], "names": names},
            "action": {"dtype": "float32", "shape": [3], "names": names},
            "observation.phase": {"dtype": "int64", **scalar},
            "observation.flag": {"dtype": "bool", **scalar},
            "timestamp": {"dtype": "float32", **scalar},
            "frame_index": {"dtype": "int64", **scalar},
            "episode_index": {"dtype": "int64", **scalar},
            "index": {"dtype": "int64", **scalar},
            "task_index": {"dtype": "int64", **scalar},
        },
    }
    (root / "meta/info.json").write_text(json.dumps(info))
    return root


BACKEND_ROOT = Path(__file__).resolve().parents[1]
STOPPED = 17
# Captured at import, so tests that patch the exporter's lock call still hold real locks.
_try_lock = lerobot_exporter._try_lock

_STOPPING_EXPORT = """
import os
import sys
from pathlib import Path

from src.api.services.lerobot_exporter import LeRobotExporter

source, output, stop = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
write = LeRobotExporter._write
rename = Path.rename


def write_then_stop(self, *args, **kwargs):
    write(self, *args, **kwargs)
    if stop == "before publishing":
        os._exit(17)


def rename_then_stop(self, target):
    moved = rename(self, target)
    if Path(target).parent == output and stop in ("after the first move", f"after {Path(target).name} moved"):
        os._exit(17)
    return moved


LeRobotExporter._write = write_then_stop
Path.rename = rename_then_stop
LeRobotExporter(source, output, dataset_id="stopped").export_episodes([0])
sys.exit(1)
"""


def stop_export(source: Path, output: Path, stop: str) -> None:
    """Export episode 0 in a child process that exits at ``stop`` without any cleanup, as a killed backend would."""
    completed = subprocess.run(
        [sys.executable, "-c", _STOPPING_EXPORT, str(source), str(output), stop],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == STOPPED, completed.stdout + completed.stderr


def hold_lock(directory: Path) -> int:
    """Lock ``directory`` as another running export would, and return the descriptor to close."""
    fd = os.open(directory / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    assert _try_lock(fd), "another export already holds the lock"
    return fd
