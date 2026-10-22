# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Stages of one CARLA + Cosmos Transfer task: capture, control-video preparation, multi-view inference."""

from __future__ import annotations

import glob
import math
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..utils.pipeline_io import PACKAGE

COSMOS_ROOT = "/workspace"
SCENARIO_RUNNER_ROOT = os.environ.get("SCENARIO_RUNNER_ROOT", "/opt/scenario_runner")
DATA_ROOT = Path(os.environ.get("CARLA_COSMOS_DATA_ROOT", "/workspace/data"))
CARLA_HOST = os.environ.get("CARLA_HOST", "127.0.0.1")
CARLA_PORT = int(os.environ.get("CARLA_PORT", "2000"))
CARLA_TRAFFIC_MANAGER_PORT = int(os.environ.get("CARLA_TRAFFIC_MANAGER_PORT", "8005"))

ALL_VIEWS = ["front_wide", "cross_left", "cross_right", "rear_left", "rear_right", "rear", "front_tele"]

CAMERAS_7 = [
    "camera_front_wide_120fov.mp4",
    "camera_cross_left_120fov.mp4",
    "camera_cross_right_120fov.mp4",
    "camera_rear_left_70fov.mp4",
    "camera_rear_right_70fov.mp4",
    "camera_rear_tele_30fov.mp4",
    "camera_front_tele_30fov.mp4",
]
CAMERAS_6 = CAMERAS_7[:6]
CAMERAS_4 = [CAMERAS_7[0], CAMERAS_7[1], CAMERAS_7[2], CAMERAS_7[5]]


class StageError(RuntimeError):
    """A pipeline stage failed."""


@dataclass
class TaskParams:
    out_dir: Path
    prompt: str
    views: list[str]
    carla_params: dict[str, str] = field(default_factory=dict)
    seconds: float = 6.0
    num_chunks: int | None = None
    num_steps: int = 35
    guidance: int = 5
    control_weight: float = 1.0


def default_views() -> list[str]:
    """Views from CARLA_COSMOS_VIEWS (comma/space separated), or all of them."""
    raw = os.environ.get("CARLA_COSMOS_VIEWS", "").replace(",", " ").split()
    if not raw or "all" in (v.lower() for v in raw):
        return list(ALL_VIEWS)
    unknown = [v for v in raw if v not in ALL_VIEWS]
    if unknown:
        raise ValueError(f"Unknown CARLA_COSMOS_VIEWS entries {unknown}; use any of: all, {', '.join(ALL_VIEWS)}")
    return [v for v in ALL_VIEWS if v in raw]


def cosmos_gpu_ids(view_count: int) -> str:
    """CUDA devices for Cosmos: COSMOS_GPU_IDS if set, otherwise the legacy per-view-count layout."""
    configured = os.environ.get("COSMOS_GPU_IDS", "").replace(" ", "")
    if configured:
        return configured
    return "1,2,3,4" if view_count <= 4 else "0,1,2,3,4,5,6,7"


def chunks_for(seconds: float) -> int:
    """Cosmos Transfer 2.5 chunk count: 1 chunk is 29 frames, each further chunk adds 28."""
    if seconds <= 3.5:
        return 1
    if seconds <= 7.0:
        return 2
    if seconds <= 9.5:
        return 3
    return max(1, math.ceil(seconds / 2.8))


def generate_carla_grid(carla_sim_dir: str, output_path: str) -> None:
    if all(os.path.exists(os.path.join(carla_sim_dir, c)) for c in CAMERAS_7):
        cams = CAMERAS_7
        filter_desc = (
            "[0:v][1:v][2:v][3:v][4:v][5:v][6:v]xstack=inputs=7:"
            "layout=0_0|w0_0|w0+w1_0|0_h0|w0_h0|w0+w1_h0|0_h0+h3:"
            "fill=black[v]"
        )
    elif all(os.path.exists(os.path.join(carla_sim_dir, c)) for c in CAMERAS_6):
        cams = CAMERAS_6
        filter_desc = (
            "[0:v][1:v][2:v][3:v][4:v][5:v]xstack=inputs=6:layout=0_0|w0_0|w0+w1_0|0_h0|w0_h0|w0+w1_h0:fill=black[v]"
        )
    elif all(os.path.exists(os.path.join(carla_sim_dir, c)) for c in CAMERAS_4):
        cams = CAMERAS_4
        filter_desc = "[0:v][1:v][2:v][3:v]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0:fill=black[v]"
    else:
        return
    inputs = []
    for cam in cams:
        inputs.extend(["-i", os.path.join(carla_sim_dir, cam)])
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_desc, "-map", "[v]"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", output_path]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print(f"Generated CARLA simulation multiview grid: {output_path}")
    except Exception as exc:
        print(f"CARLA grid generation skipped: {exc}")


def hf_token() -> str | None:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    token_file = Path("/root/.cache/huggingface/token")
    if not token and token_file.is_file():
        token = token_file.read_text(encoding="utf-8")
    return token.strip().strip("\"'") if token else None


def stage_env(view_count: int) -> dict[str, str]:
    env = os.environ.copy()
    # /workspace makes the stage packages importable with `python -m`.
    env["PYTHONPATH"] = os.pathsep.join(["/workspace", SCENARIO_RUNNER_ROOT, "/opt/carla-pythonapi"])
    env["HF_DEBUG"] = "1"
    env["CUDA_VISIBLE_DEVICES"] = cosmos_gpu_ids(view_count)
    env["NCCL_P2P_DISABLE"] = "1"
    env["NCCL_IB_DISABLE"] = "1"
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    token = hf_token()
    if token:
        env["HF_TOKEN"] = token
        env["HUGGING_FACE_HUB_TOKEN"] = token
    return env


def run_stage(name: str, cmd: list[str], env: dict[str, str], log_dir: Path, tolerate: str | None = None) -> None:
    print(f"--- {name} ---")
    result = subprocess.run(cmd, cwd=COSMOS_ROOT, env=env, capture_output=True, text=True)
    if result.returncode == 0 or (tolerate and tolerate in result.stderr):
        print(f"{name} completed")
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name.split(':')[0].strip().lower().replace(' ', '_')}_error.log"
    log_path.write_text(result.stderr, encoding="utf-8")
    raise StageError(f"{name} failed (exit {result.returncode}); see {log_path}\n{result.stderr[-1500:]}")


def check_carla_reachable() -> None:
    try:
        with socket.create_connection((CARLA_HOST, CARLA_PORT), timeout=5.0):
            pass
    except OSError as exc:
        raise StageError(f"CARLA is not reachable at {CARLA_HOST}:{CARLA_PORT}: {exc}") from exc


def synthesized_duration(video: str) -> float | None:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video,
    ]  # fmt: skip
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True)
        return float(out.stdout.strip())
    except Exception as exc:
        print(f"ffprobe duration check note: {exc}")
        return None


def stage_carla_videos(clip_id: str, carla_sim_dir: Path, duration: float | None, view_count: int) -> None:
    """Copy the CARLA camera videos, trimmed to the Cosmos output length, and build their grid."""
    rgb_dir = DATA_ROOT / "captures" / clip_id / "rgb_videos"
    if not rgb_dir.is_dir():
        return
    for source in rgb_dir.glob("*.mp4"):
        target = carla_sim_dir / source.name
        if duration and duration > 0:
            trim = ["ffmpeg", "-y", "-i", str(source), "-t", f"{duration:.3f}", "-c", "copy", str(target)]
            subprocess.run(trim, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            shutil.copy2(source, target)
    manifest = DATA_ROOT / "captures" / clip_id / "manifest.json"
    if manifest.is_file():
        shutil.copy2(manifest, carla_sim_dir / "manifest.json")
    generate_carla_grid(str(carla_sim_dir), str(carla_sim_dir / "carla_multiview_grid.mp4"))
    print(f"Stored CARLA {view_count} camera views (synchronized to {duration or 0:.2f}s) in {carla_sim_dir}")


def run_task(params: TaskParams) -> Path:
    """Run capture, preparation and inference for one task; return the multi-view grid video."""
    views = params.views
    num_chunks = params.num_chunks or chunks_for(params.seconds)
    # Capture enough CARLA frames so Cosmos never starves or clamps chunks.
    capture_seconds = math.ceil((29 + 28 * (num_chunks - 1) + 10) / 10.0)
    clip_id = f"carla_auto_{int(time.time())}"
    out_dir = params.out_dir
    carla_sim_dir = out_dir / "carla_simulation"
    cosmos_synth_dir = out_dir / "cosmos_synthesized"
    carla_sim_dir.mkdir(parents=True, exist_ok=True)
    cosmos_synth_dir.mkdir(parents=True, exist_ok=True)
    env = stage_env(len(views))
    log_dir = out_dir / "logs"

    # A stale ScenarioRunner from an earlier task would hold the simulator.
    subprocess.run(["pkill", "-9", "-f", "scenario_runner.py"], stderr=subprocess.DEVNULL)
    time.sleep(1)
    check_carla_reachable()

    capture = [
        os.environ.get("CARLA_PYTHON", "python"), "-m", f"{PACKAGE}.tasks.run_carla_capture",
        "--host", CARLA_HOST, "--port", str(CARLA_PORT),
        "--traffic-manager-port", str(CARLA_TRAFFIC_MANAGER_PORT),
        "--clip-id", clip_id, "--seconds", str(capture_seconds),
        "--camera-timeout", "15.0", "--no-cleanup", "--out", str(DATA_ROOT),
    ]  # fmt: skip
    for key, value in params.carla_params.items():
        flag = "--" + key.removeprefix("carla_").replace("_", "-")
        if value.lower() == "true":
            capture.append(flag)
        elif value.lower() != "false":
            capture.extend([flag, value])
    capture += ["--views", *views]
    # The CARLA client can abort in a C++ destructor after a successful capture.
    run_stage("Stage 1: CARLA capture", capture, env, log_dir, tolerate="terminate called")

    prepare = [
        "python", "-m", f"{PACKAGE}.tasks.prepare_cosmos_inputs",
        "--clip-id", clip_id, "--data-root", str(DATA_ROOT), "--cosmos-root", COSMOS_ROOT,
        "--prompt", params.prompt, "--num-steps", str(params.num_steps),
        "--guidance", str(params.guidance), "--control-weight", str(params.control_weight),
        "--num-chunks", str(num_chunks), "--views", *views,
    ]  # fmt: skip
    run_stage("Stage 2: prepare Cosmos inputs", prepare, env, log_dir)

    infer = [
        "python", "-m", f"{PACKAGE}.tasks.run_cosmos_inference",
        "--clip-id", clip_id, "--data-root", str(DATA_ROOT), "--cosmos-root", COSMOS_ROOT,
        "--output-dir", str(cosmos_synth_dir), "--views", *views,
    ]  # fmt: skip
    run_stage("Stage 3: Cosmos inference", infer, env, log_dir)

    outputs = glob.glob(str(cosmos_synth_dir / "**" / "*.mp4"), recursive=True)
    if not outputs:
        raise StageError(f"Cosmos finished but wrote no .mp4 under {cosmos_synth_dir}")
    sample = next((v for v in outputs if "_grid" not in v), outputs[0])
    stage_carla_videos(clip_id, carla_sim_dir, synthesized_duration(sample), len(views))

    grids = [v for v in outputs if "_grid" in v]
    selected = grids[0] if grids else sorted(outputs)[-1]
    grid = out_dir / "multiview_grid.mp4"
    shutil.copy2(selected, grid)
    return grid
