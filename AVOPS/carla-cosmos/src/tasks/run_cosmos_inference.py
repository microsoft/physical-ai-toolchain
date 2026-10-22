#!/usr/bin/env python3
"""Stage 3: run Cosmos Transfer2.5 Auto Multiview inference."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..utils.carla.camera_views import CAMERA_VIEWS, resolve_views
from ..utils.cosmos.gpu_utils import parse_gpu_ids, preflight_gpu_plan
from ..utils.pipeline_io import DEFAULT_DATA_ROOT, PACKAGE, multiview_dir, realistic_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT, help="Pipeline data root.")
    parser.add_argument("--clip-id", default="carla_000001", help="Clip ID to infer.")
    parser.add_argument("--spec", type=Path, default=None, help="Prepared multiview spec override.")
    parser.add_argument(
        "--views",
        nargs="+",
        default=["all"],
        help="Views to infer. Use all or any prepared subset.",
    )
    parser.add_argument(
        "--cosmos-root",
        type=Path,
        default=Path(os.environ.get("COSMOS_ROOT", os.environ.get("COSMOS_TRANSFER_ROOT", "~/sim/cosmos-transfer2.5"))),
        help="cosmos-transfer2.5 checkout root.",
    )
    parser.add_argument("--cosmos-python", type=Path, default=None, help="Cosmos Python executable override.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory override.")
    parser.add_argument("--inference-fps", default="spec", help="Override spec FPS, or use 'spec'.")
    parser.add_argument("--gpus", type=int, default=None, help="Number of visible GPUs to use.")
    parser.add_argument("--gpu-ids", default=None, help="Comma-separated CUDA device IDs to expose.")
    parser.add_argument("--allow-low-vram", action="store_true", help="Warn instead of blocking on low-VRAM GPUs.")
    parser.add_argument("--master-port", type=int, default=12341)
    parser.add_argument("--dry-run", action="store_true", help="Print the launch command without running inference.")
    return parser.parse_args()


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


def active_views_from_spec(spec: dict[str, Any]) -> list[str]:
    return [key for key, view in CAMERA_VIEWS.items() if spec.get(view.inference_key, {}).get("control_path")]


def subset_spec(spec: dict[str, Any], view_keys: list[str]) -> dict[str, Any]:
    selected = set(view_keys)
    output: dict[str, Any] = {}
    for key, value in spec.items():
        matching_view = next(
            (view_key for view_key, view in CAMERA_VIEWS.items() if view.inference_key == key),
            None,
        )
        if matching_view is not None and matching_view not in selected:
            continue
        output[key] = value
    return output


def write_runtime_spec(spec_path: Path, spec: dict[str, Any], view_keys: list[str], inference_fps: str) -> Path:
    runtime_spec = subset_spec(spec, view_keys)
    if inference_fps != "spec":
        runtime_spec["fps"] = int(inference_fps)
    output_path = spec_path.with_name("multiview_spec.runtime.json")
    output_path.write_text(json.dumps(runtime_spec, indent=2) + "\n")
    return output_path


def main() -> None:
    args = parse_args()
    data_root = args.data_root.expanduser().resolve()
    cosmos_root = args.cosmos_root.expanduser().resolve()
    spec_path = (
        args.spec.expanduser().resolve()
        if args.spec is not None
        else multiview_dir(data_root, args.clip_id) / "multiview_spec.json"
    )
    if not spec_path.exists():
        raise FileNotFoundError(f"Missing prepared multiview spec: {spec_path}")
    spec = json.loads(spec_path.read_text())
    available_views = active_views_from_spec(spec)
    view_keys = resolve_views(args.views, available=available_views)
    runtime_spec_path = write_runtime_spec(spec_path, spec, view_keys, args.inference_fps)

    gpu_ids = parse_gpu_ids(args.gpu_ids)
    process_count, warnings = preflight_gpu_plan(
        active_views=len(view_keys),
        requested_gpus=args.gpus,
        gpu_ids=gpu_ids,
        allow_low_vram=args.allow_low_vram,
    )
    for warning in warnings:
        print(f"Warning: {warning}")

    cosmos_python = select_cosmos_python(cosmos_root, args.cosmos_python)
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else realistic_dir(data_root, args.clip_id)
    )
    env = os.environ.copy()
    repo_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = repo_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if gpu_ids is not None:
        env["CUDA_VISIBLE_DEVICES"] = ",".join(gpu_ids)
    elif args.gpus is not None and "CUDA_VISIBLE_DEVICES" not in env:
        env["CUDA_VISIBLE_DEVICES"] = ",".join(str(idx) for idx in range(args.gpus))

    torchrun = cosmos_python.with_name("torchrun")
    torchrun_bin = str(torchrun) if torchrun.exists() else (shutil.which("torchrun") or "torchrun")
    command = [
        torchrun_bin,
        f"--nproc_per_node={process_count}",
        f"--master_port={args.master_port}",
        "-m",
        f"{PACKAGE}.utils.cosmos.cosmos_multiview_launcher",
        "-i",
        str(runtime_spec_path),
        "-o",
        str(output_dir),
    ]
    if args.disable_guardrails:
        command.append("--disable-guardrails")

    print("Cosmos inference launch:")
    print("  " + " ".join(command))
    print(f"  active_views={','.join(view_keys)}")
    print(f"  output_dir={output_dir}")
    if args.dry_run:
        return
    subprocess.run(command, check=True, cwd=cosmos_root, env=env)


if __name__ == "__main__":
    main()
