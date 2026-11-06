"""Prompt and multiview spec generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..carla.camera_views import CAMERA_VIEWS, views_in_cosmos_order
from ..pipeline_io import write_json

DEFAULT_PROMPT = (
    "A realistic multi-camera autonomous driving video captured from an ego vehicle driving through "
    "a modern urban road network. The cameras show synchronized front, side, and rear views. Preserve "
    "the road geometry, lane markings, traffic lights, traffic signs, sidewalks, buildings, vehicles, "
    "pedestrians, and ego-motion from the HDMap control videos. The generated video should be "
    "photorealistic, temporally consistent, and match the captured CARLA scenario conditions."
)


def build_prompt(manifest: dict[str, Any], prompt: str | None = None) -> str:
    if prompt:
        return prompt.strip()

    parts = [DEFAULT_PROMPT]
    weather = manifest.get("weather")
    if isinstance(weather, dict):
        preset = weather.get("preset")
        if preset:
            parts.append(f"The CARLA weather preset is {preset}.")
        clouds = weather.get("cloudiness")
        rain = weather.get("precipitation")
        sun = weather.get("sun_altitude_angle")
        if any(value is not None for value in (clouds, rain, sun)):
            parts.append(
                f"Scenario weather metadata: cloudiness={clouds}, precipitation={rain}, sun_altitude_angle={sun}."
            )
    town = manifest.get("town") or manifest.get("map")
    if town:
        parts.append(f"The source CARLA town/map is {town}.")
    return " ".join(parts)


def build_multiview_spec(
    *,
    clip_id: str,
    view_keys: list[str],
    control_subdir: str,
    fps: int,
    guidance: int,
    control_weight: float,
    num_steps: int,
    seed: int,
    num_chunks: int,
    chunk_overlap: int,
    save_combined_views: bool,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": clip_id,
        "prompt_path": "prompt.txt",
        "fps": int(fps),
        "num_conditional_frames": 0,
        "guidance": int(guidance),
        "control_weight": float(control_weight),
        "num_steps": int(num_steps),
        "seed": int(seed),
        "enable_autoregressive": True,
        "num_chunks": int(num_chunks),
        "chunk_overlap": int(chunk_overlap),
        "save_combined_views": bool(save_combined_views),
    }
    for view_key in views_in_cosmos_order(view_keys):
        view = CAMERA_VIEWS[view_key]
        spec[view.inference_key] = {
            "control_path": f"{control_subdir}/{view.control_name}",
        }
    return spec


def write_prompt_and_spec(output_dir: Path, prompt_text: str, spec: dict[str, Any]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = output_dir / "prompt.txt"
    spec_path = output_dir / "multiview_spec.json"
    prompt_path.write_text(prompt_text.strip() + "\n")
    write_json(spec_path, spec)
    return prompt_path, spec_path
