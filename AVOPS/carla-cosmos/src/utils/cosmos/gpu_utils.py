"""GPU discovery and Cosmos context-parallel sizing."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class GPUInfo:
    index: str
    name: str
    memory_total_mb: int


def choose_context_parallel_size(active_views: int) -> int:
    if active_views <= 1:
        return 1
    if active_views <= 2:
        return 2
    if active_views <= 4:
        return 4
    if active_views <= 8:
        # Cosmos Transfer 2.5 multi-view needs n_views <= CP size (one view per rank in the VAE),
        # and its all-to-all attention splits the 2B model's 16 heads, so CP must divide 16.
        # Five to eight views therefore all need 8 GPUs; 6 is not a valid size.
        return 8
    raise ValueError("Cosmos Auto Multiview supports at most 8 context-parallel processes here.")


def parse_gpu_ids(value: str | None) -> list[str] | None:
    if value is None or value.strip() == "":
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def visible_gpu_infos() -> list[GPUInfo]:
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return []
    command = [
        nvidia_smi,
        "--query-gpu=index,name,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return []

    infos: list[GPUInfo] = []
    allowed = parse_gpu_ids(os.environ.get("CUDA_VISIBLE_DEVICES"))
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        index, name, memory = parts[0], parts[1], parts[2]
        if allowed is not None and index not in allowed:
            continue
        try:
            memory_total_mb = int(memory)
        except ValueError:
            continue
        infos.append(GPUInfo(index=index, name=name, memory_total_mb=memory_total_mb))
    return infos


def preflight_gpu_plan(
    *,
    active_views: int,
    requested_gpus: int | None,
    gpu_ids: list[str] | None,
    allow_low_vram: bool = False,
) -> tuple[int, list[str]]:
    """Return process count and warning strings for a Cosmos multiview launch."""
    required_processes = choose_context_parallel_size(active_views)
    infos = visible_gpu_infos()
    available_count = len(gpu_ids) if gpu_ids is not None else len(infos)
    if requested_gpus is not None:
        available_count = min(available_count or requested_gpus, requested_gpus)

    if available_count < required_processes:
        raise RuntimeError(
            f"{active_views} active view(s) require {required_processes} GPU process(es), "
            f"but only {available_count} visible/requested GPU(s) were found. Reduce --views or expose more GPUs."
        )

    warnings: list[str] = []
    selected_infos = infos[:required_processes]
    low_vram = [gpu for gpu in selected_infos if gpu.memory_total_mb and gpu.memory_total_mb < 64000]
    if low_vram and not allow_low_vram:
        names = ", ".join(f"{gpu.index}:{gpu.name}({gpu.memory_total_mb} MB)" for gpu in low_vram)
        warnings.append(
            "Selected GPU(s) report less than 64 GB VRAM. Cosmos docs list 65.4 GB for "
            f"single-GPU 2B inference; multiview may still OOM depending on view count/settings: {names}"
        )
    if required_processes >= 8 and any("L40" in gpu.name for gpu in selected_infos):
        warnings.append("Full 5-7 view multiview on L40-class 48 GB GPUs is experimental; use a smoke run first.")
    return required_processes, warnings
