#!/usr/bin/env python3
"""Stage 2: convert a portable CARLA capture into Cosmos multiview inputs."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..utils.carla.camera_views import CAMERA_VIEWS, resolve_views, views_to_cosmos_names
from ..utils.cosmos.cosmos_parquet import write_pylist_parquet
from ..utils.cosmos.cosmos_patches import (
    apply_clipgt_traffic_light_state_patch,
    apply_robust_triangulation_patch,
)
from ..utils.cosmos.cosmos_rdshq import write_rdshq_annotations
from ..utils.cosmos.multiview_spec import build_multiview_spec, build_prompt, write_prompt_and_spec
from ..utils.cosmos.scene_rows import stabilize_static_obstacle_rows
from ..utils.cosmos.video_utils import resample_video
from ..utils.pipeline_io import (
    DEFAULT_DATA_ROOT,
    SCENE_ELEMENTS,
    capture_dir,
    capture_rows_dir,
    control_dir,
    multiview_dir,
    rdshq_scene_dir,
    read_json,
    rgb_dir,
    scene_dir,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT, help="Pipeline data root.")
    parser.add_argument("--clip-id", default="carla_000001", help="Clip ID to prepare.")
    parser.add_argument(
        "--views",
        nargs="+",
        default=["all"],
        help=(
            "Views to prepare. Use all or any of: "
            "front_wide front_tele cross_left cross_right rear_left rear_right rear."
        ),
    )
    parser.add_argument(
        "--cosmos-root",
        type=Path,
        default=Path(os.environ.get("COSMOS_ROOT", os.environ.get("COSMOS_TRANSFER_ROOT", "~/sim/cosmos-transfer2.5"))),
        help="cosmos-transfer2.5 checkout root.",
    )
    parser.add_argument("--cosmos-python", type=Path, default=None, help="Cosmos Python executable override.")
    parser.add_argument(
        "--annotation-format",
        choices=["parquet", "rdshq"],
        default="parquet",
        help="Scene annotation backend for Cosmos control rendering. Default: parquet.",
    )
    parser.add_argument(
        "--annotation-fps",
        default=None,
        help="Annotation FPS metadata/timebase. Defaults to --parquet-fps for compatibility.",
    )
    parser.add_argument("--parquet-fps", default="capture", help="Annotation FPS metadata. Default: capture.")
    parser.add_argument("--control-render-fps", default="parquet", help="HDMap render FPS. Default: annotation.")
    parser.add_argument("--model-fps", type=int, default=10, help="FPS for controls consumed by Cosmos inference.")
    parser.add_argument("--width", type=int, default=None, help="Prepared control width. Defaults to capture width.")
    parser.add_argument("--height", type=int, default=None, help="Prepared control height. Defaults to capture height.")
    parser.add_argument("--prompt", default=None, help="Prompt text override.")
    parser.add_argument("--prompt-file", type=Path, default=None, help="Prompt text file override.")
    parser.add_argument("--guidance", type=int, default=3)
    parser.add_argument("--control-weight", type=float, default=1.0)
    parser.add_argument("--num-steps", type=int, default=35)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--num-chunks", type=int, default=2)
    parser.add_argument("--chunk-overlap", type=int, default=1)
    parser.add_argument("--save-combined-views", action="store_true", help="Ask Cosmos to save combined views only.")
    parser.add_argument(
        "--skip-control-render",
        action="store_true",
        help="Write scene annotations only, or reuse existing controls.",
    )
    parser.add_argument(
        "--qhull-patch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply the idempotent robust triangulation patch before Stage 2 control rendering.",
    )
    parser.add_argument(
        "--traffic-light-state-patch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Patch Cosmos' Parquet loader to consume CARLA traffic-light state sequences.",
    )
    parser.add_argument("--render-gpu-id", default=None, help="CUDA_VISIBLE_DEVICES value for control rendering.")
    parser.add_argument(
        "--stabilize-static-obstacles",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Infer parked/stopped obstacle tracks from Stage 1 rows and smooth their boxes before rendering.",
    )
    parser.add_argument(
        "--static-obstacle-threshold-m",
        type=float,
        default=0.20,
        help="Maximum horizontal displacement for treating an obstacle track as static.",
    )
    return parser.parse_args()


def parse_fps(value: str, *, capture_fps: int, parquet_fps: int | None = None) -> int:
    lowered = str(value).lower()
    if lowered == "capture":
        return int(capture_fps)
    if lowered in {"parquet", "annotation"}:
        if parquet_fps is None:
            raise ValueError("annotation FPS has not been resolved yet")
        return int(parquet_fps)
    return int(value)


def select_cosmos_python(cosmos_root: Path, override: Path | None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    for candidate in (cosmos_root / ".venv/bin/python", cosmos_root / ".venv/bin/python3"):
        if candidate.exists():
            return candidate
    python = shutil.which("python3") or shutil.which("python")
    if python:
        return Path(python)
    raise RuntimeError("Could not find Python for Cosmos. Set --cosmos-python.")


def copy_rgb_videos(data_root: Path, clip_id: str, manifest: dict[str, Any], view_keys: list[str]) -> None:
    portable_source_dir = capture_dir(data_root, clip_id) / "rgb_videos"
    manifest_rgb_dir = manifest.get("rgb_dir")
    manifest_source_dir = Path(manifest_rgb_dir).expanduser() if manifest_rgb_dir else None
    source_dir = portable_source_dir if portable_source_dir.exists() else manifest_source_dir
    if source_dir is None or not source_dir.exists():
        raise FileNotFoundError(
            f"Missing captured RGB videos. Expected {portable_source_dir}; manifest fallback was {manifest_source_dir}"
        )
    destination_dir = rgb_dir(data_root, clip_id)
    destination_dir.mkdir(parents=True, exist_ok=True)
    for key in view_keys:
        camera_name = CAMERA_VIEWS[key].cosmos_name
        src = source_dir / f"{camera_name}.mp4"
        dst = destination_dir / src.name
        if not src.exists():
            raise FileNotFoundError(f"Missing captured RGB video: {src}")
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)


def downsample_rows(
    element: str,
    rows: list[dict[str, Any]],
    *,
    capture_fps: int,
    parquet_fps: int,
    keep_timestamps: set[int] | None,
) -> tuple[list[dict[str, Any]], set[int] | None]:
    if parquet_fps == capture_fps:
        if element == "egomotion_estimate":
            return rows, {int(row["key"]["timestamp_micros"]) for row in rows}
        return rows, keep_timestamps
    if parquet_fps > capture_fps:
        raise ValueError("annotation FPS cannot exceed capture FPS unless upsampling is implemented.")
    if capture_fps % parquet_fps != 0:
        raise ValueError(f"capture FPS {capture_fps} must be evenly divisible by parquet FPS {parquet_fps}.")
    step = capture_fps // parquet_fps
    if element == "egomotion_estimate":
        selected = [row for idx, row in enumerate(rows) if idx % step == 0]
        return selected, {int(row["key"]["timestamp_micros"]) for row in selected}
    if element == "obstacle" and keep_timestamps is not None:
        return [
            row for row in rows if int(row.get("key", {}).get("timestamp_micros", -1)) in keep_timestamps
        ], keep_timestamps
    return rows, keep_timestamps


def load_scene_rows(
    data_root: Path,
    clip_id: str,
    *,
    capture_fps: int,
    annotation_fps: int,
) -> dict[str, list[dict[str, Any]]]:
    rows_root = capture_rows_dir(data_root, clip_id)
    if not rows_root.exists():
        raise FileNotFoundError(f"Missing portable rows directory: {rows_root}")
    rows_by_element: dict[str, list[dict[str, Any]]] = {}
    keep_timestamps: set[int] | None = None
    for element in SCENE_ELEMENTS:
        path = rows_root / f"{element}.json"
        rows = read_json(path) if path.exists() else []
        rows, keep_timestamps = downsample_rows(
            element,
            rows,
            capture_fps=capture_fps,
            parquet_fps=annotation_fps,
            keep_timestamps=keep_timestamps,
        )
        rows_by_element[element] = rows
    return rows_by_element


def write_scene_annotations(
    data_root: Path,
    clip_id: str,
    view_keys: list[str],
    rows_by_element: dict[str, list[dict[str, Any]]],
    *,
    capture_fps: int,
    parquet_fps: int,
) -> None:
    output_dir = scene_dir(data_root, clip_id)
    for element in SCENE_ELEMENTS:
        write_pylist_parquet(output_dir / f"{clip_id}.{element}.parquet", rows_by_element.get(element, []), element)

    timestamp_root = capture_dir(data_root, clip_id) / "timestamps"
    for key in view_keys:
        camera_name = CAMERA_VIEWS[key].cosmos_name
        src = timestamp_root / f"{camera_name}.json"
        if src.exists():
            timestamps = read_json(src)
            if parquet_fps != capture_fps:
                step = capture_fps // parquet_fps
                timestamps = [row for idx, row in enumerate(timestamps) if idx % step == 0]
                write_json(output_dir / f"{clip_id}.{camera_name}.json", timestamps)
            else:
                shutil.copy2(src, output_dir / f"{clip_id}.{camera_name}.json")


def run_control_renderer(
    *,
    cosmos_root: Path,
    cosmos_python: Path,
    input_dir: Path,
    output_root: Path,
    clip_id: str,
    camera_names: list[str],
    annotation_fps: int,
    control_render_fps: int,
    render_gpu_id: str | None,
) -> None:
    code = r"""
import runpy
import sys
from cosmos_transfer2._src.imaginaire.auxiliary.world_scenario.rendering.config import SETTINGS

input_dir, output_dir, clip_id, cameras, annotation_fps, control_fps = sys.argv[1:7]
SETTINGS["INPUT_POSE_FPS"] = int(annotation_fps)
SETTINGS["TARGET_RENDER_FPS"] = int(control_fps)
SETTINGS["SOURCE_POSE_FPS"] = int(annotation_fps)
SETTINGS["SOURCE_OBSTACLE_FPS"] = int(annotation_fps)
sys.argv = [
    "generate_control_videos.py",
    "-i",
    input_dir,
    "-o",
    output_dir,
    "--cameras",
    cameras,
    "--clip-id",
    clip_id,
]
runpy.run_path("scripts/generate_control_videos.py", run_name="__main__")
"""
    env = os.environ.copy()
    if render_gpu_id is not None:
        env["CUDA_VISIBLE_DEVICES"] = render_gpu_id
    command = [
        str(cosmos_python),
        "-c",
        code,
        str(input_dir),
        str(output_root),
        clip_id,
        ",".join(camera_names),
        str(annotation_fps),
        str(control_render_fps),
    ]
    subprocess.run(command, check=True, cwd=cosmos_root, env=env)


def prepare_controls(
    *,
    data_root: Path,
    clip_id: str,
    view_keys: list[str],
    model_fps: int,
    width: int,
    height: int,
) -> Path:
    source_dir = control_dir(data_root, clip_id)
    output_root = multiview_dir(data_root, clip_id)
    control_subdir = f"control_{model_fps}fps"
    output_dir = output_root / control_subdir
    for key in view_keys:
        view = CAMERA_VIEWS[key]
        source = source_dir / f"{clip_id}.{view.cosmos_name}.mp4"
        if not source.exists():
            raise FileNotFoundError(f"Missing control video: {source}")
        resample_video(source, output_dir / view.control_name, fps=model_fps, width=width, height=height)
    return output_dir


def main() -> None:
    args = parse_args()
    data_root = args.data_root.expanduser().resolve()
    cosmos_root = args.cosmos_root.expanduser().resolve()
    manifest_path = capture_dir(data_root, args.clip_id) / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing Stage 1 manifest: {manifest_path}")
    manifest = read_json(manifest_path)
    available_views = manifest.get("active_views") or [
        camera.get("view_key") for camera in manifest.get("cameras", []) if isinstance(camera, dict)
    ]
    view_keys = resolve_views(args.views, available=available_views)
    capture_fps = int(manifest.get("capture_fps") or manifest.get("fps") or 10)
    annotation_fps_arg = args.annotation_fps if args.annotation_fps is not None else args.parquet_fps
    annotation_fps = parse_fps(annotation_fps_arg, capture_fps=capture_fps)
    control_render_fps = parse_fps(args.control_render_fps, capture_fps=capture_fps, parquet_fps=annotation_fps)
    width = int(args.width or manifest.get("width") or 1280)
    height = int(args.height or manifest.get("height") or 720)
    seed = manifest.get("seed")

    copy_rgb_videos(data_root, args.clip_id, manifest, view_keys)
    rows_by_element = load_scene_rows(
        data_root,
        args.clip_id,
        capture_fps=capture_fps,
        annotation_fps=annotation_fps,
    )
    if args.stabilize_static_obstacles:
        stabilization_stats = stabilize_static_obstacle_rows(
            rows_by_element,
            static_threshold_m=args.static_obstacle_threshold_m,
        )
    else:
        stabilization_stats = {
            "enabled": False,
            "static_threshold_m": float(args.static_obstacle_threshold_m),
        }
    if args.annotation_format == "parquet":
        annotation_dir = scene_dir(data_root, args.clip_id)
        write_scene_annotations(
            data_root,
            args.clip_id,
            view_keys,
            rows_by_element,
            capture_fps=capture_fps,
            parquet_fps=annotation_fps,
        )
    else:
        annotation_dir = rdshq_scene_dir(data_root, args.clip_id)
        write_rdshq_annotations(
            annotation_dir,
            args.clip_id,
            rows_by_element,
            manifest,
            view_keys,
            annotation_fps=annotation_fps,
            width=width,
            height=height,
            static_threshold_m=args.static_obstacle_threshold_m,
        )

    if args.qhull_patch and not args.skip_control_render:
        print(apply_robust_triangulation_patch(cosmos_root))
    if args.traffic_light_state_patch and args.annotation_format == "parquet" and not args.skip_control_render:
        print(apply_clipgt_traffic_light_state_patch(cosmos_root))

    if not args.skip_control_render:
        run_control_renderer(
            cosmos_root=cosmos_root,
            cosmos_python=select_cosmos_python(cosmos_root, args.cosmos_python),
            input_dir=annotation_dir,
            output_root=data_root / "control_videos",
            clip_id=args.clip_id,
            camera_names=views_to_cosmos_names(view_keys),
            annotation_fps=annotation_fps,
            control_render_fps=control_render_fps,
            render_gpu_id=args.render_gpu_id,
        )

    try:
        prepare_controls(
            data_root=data_root,
            clip_id=args.clip_id,
            view_keys=view_keys,
            model_fps=args.model_fps,
            width=width,
            height=height,
        )
    except FileNotFoundError:
        if args.skip_control_render:
            print("Scene annotations written. Skipping spec because control videos are not available.", file=sys.stderr)
            return
        raise

    prompt_text = args.prompt
    if args.prompt_file is not None:
        prompt_text = args.prompt_file.expanduser().read_text()
    prompt_text = build_prompt(manifest, prompt_text)
    spec = build_multiview_spec(
        clip_id=args.clip_id,
        view_keys=view_keys,
        control_subdir=f"control_{args.model_fps}fps",
        fps=args.model_fps,
        guidance=args.guidance,
        control_weight=args.control_weight,
        num_steps=args.num_steps,
        seed=seed,
        num_chunks=args.num_chunks,
        chunk_overlap=args.chunk_overlap,
        save_combined_views=args.save_combined_views,
    )
    prompt_path, spec_path = write_prompt_and_spec(multiview_dir(data_root, args.clip_id), prompt_text, spec)
    write_json(
        multiview_dir(data_root, args.clip_id) / "prepare_manifest.json",
        {
            "clip_id": args.clip_id,
            "active_views": view_keys,
            "annotation_format": args.annotation_format,
            "capture_fps": capture_fps,
            "annotation_fps": annotation_fps,
            "parquet_fps": annotation_fps if args.annotation_format == "parquet" else None,
            "control_render_fps": control_render_fps,
            "model_fps": args.model_fps,
            "annotation_dir": str(annotation_dir),
            "static_obstacle_stabilization": stabilization_stats,
            "prompt_path": str(prompt_path),
            "spec_path": str(spec_path),
        },
    )
    print(f"Prepared Cosmos inputs for {args.clip_id}")
    print(f"  Annotation format: {args.annotation_format}")
    print(f"  Scene annotations: {annotation_dir}")
    print(f"  Controls/spec:     {spec_path.parent}")


if __name__ == "__main__":
    main()
