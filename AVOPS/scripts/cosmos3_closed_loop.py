# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Zero-NGC closed-loop AVOps helpers for ODD gap analysis and Cosmos 3 generation.

Subcommands cover all four closed-loop stages without Airflow or NGC credentials:

* ``probe-clips``  Inspect raw/curated driving clips and write ``clip_manifest.json``.
* ``build-jobs``   Select diverse weather/time-of-day ODD gaps and build Cosmos 3 jobs.
* ``synthesize``   Call a live ``vllm/vllm-omni`` (``nvidia/Cosmos3-Nano-FP8``) endpoint to
                   generate synthetic driving clips via ``POST /v1/videos/sync``.
* ``compare-odd``  Compare Before vs. After ODD reports and verify per-clip VLM condition
                   adherence, writing ``closed_loop_report.json`` and ``closed_loop_summary.md``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Collection
from pathlib import Path
from typing import Any

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm"})
COSMOS3_ATTRIBUTES = ("weatherType", "timeOfDay")
EXTREME_DOMAIN_SHIFT_VALUES = frozenset({"rainy", "snowy", "foggy", "night"})
VALID_GENERATION_MODES = frozenset({"auto", "i2v", "t2v"})
WFM_FILENAME = "wfm_generation_input.json"
PROMPTS_FILENAME = "generation_prompts.json"
FINAL_GAP_FILENAME = "final_gap_output.json"

DEFAULT_NEGATIVE_PROMPT = {
    "subjects": [
        {
            "description": "Blurry, distorted vehicles or road markings with unnatural motion.",
            "appearance_details": "Compression artifacts, flickering edges, broken geometry, muddy textures.",
            "action": "Jerky slideshow motion or abrupt position jumping of objects.",
            "state_changes": "Sudden unmotivated flickering or color shifts between frames.",
        }
    ],
    "background_setting": (
        "Flat painted backdrop with inconsistent depth, swimming textures, flickering horizon, "
        "or disappearing lane markings."
    ),
    "lighting": {
        "conditions": "Unnatural flat lighting with contradictory shadow directions.",
        "shadows": "Aliased or popping shadows detached from vehicles.",
    },
    "cinematography": {
        "camera_motion": "Shaky rolling-shutter camera distortion disconnected from vehicle driving motion.",
        "focus": "Blurry out-of-focus frames with chromatic aberration.",
    },
    "physical_realism": "Vehicles defying gravity, clipping through surfaces, or breaking traffic physics.",
}

CONDITION_VISUAL_DIRECTIVES = {
    ("weatherType", "rainy"): {
        "weather": (
            "steady rainfall with wet reflective asphalt, light tire spray from vehicles, and overcast rain clouds"
        ),
        "lighting": (
            "cool diffused overcast daylight with specular wet-road reflections and illuminated vehicle taillights"
        ),
        "atmosphere": "active rain streaks visible in the air and subtle mist above the wet road surface",
    },
    ("weatherType", "foggy"): {
        "weather": (
            "dense atmospheric fog rolling across the roadway, reducing distant visibility while keeping near lanes "
            "clear"
        ),
        "lighting": (
            "soft muted grey diffused daylight scattered through heavy mist, with glowing headlights and taillights"
        ),
        "atmosphere": "thick volumetric haze fading distant scenery and horizon into pale grey fog",
    },
    ("weatherType", "snowy"): {
        "weather": (
            "active falling snow with snow-dusted shoulders, slushy wet-snow tire tracks, and winter overcast sky"
        ),
        "lighting": "cold pale winter daylight reflecting softly off snow banks along the roadside",
        "atmosphere": "visible snowflakes drifting across the windshield view in crisp winter air",
    },
    ("weatherType", "cloudy"): {
        "weather": "heavily overcast sky blanketed with dense layered grey clouds and no direct harsh sunlight",
        "lighting": "even flat overcast illumination with soft ambient shadows beneath vehicles",
        "atmosphere": "cool muted daytime tones with thick grey cloud cover across the horizon",
    },
    ("timeOfDay", "night"): {
        "weather": "clear nighttime driving conditions under a dark night sky",
        "lighting": (
            "dark night scene illuminated by forward vehicle headlights, glowing red taillights, "
            "and overhead highway lamps"
        ),
        "atmosphere": (
            "deep night contrast with realistic headlight beam cones casting light onto the asphalt lane markings"
        ),
    },
    ("timeOfDay", "evening"): {
        "weather": "late dusk twilight driving conditions with deep amber-blue evening sky",
        "lighting": (
            "low-angle warm sunset twilight fading into dusk with vehicle headlights and taillights switched on"
        ),
        "atmosphere": "long soft dusk shadows and golden-violet horizon glow over the roadway",
    },
    ("timeOfDay", "morning"): {
        "weather": "crisp early morning driving conditions with pale dawn sky and light ground haze",
        "lighting": "low-angle golden sunrise illumination casting long soft morning shadows across the asphalt",
        "atmosphere": "cool early morning clarity with gentle golden sunbeams near the horizon",
    },
}


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    stripped = raw.strip()
    return stripped if stripped else default


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name, "")
    if not raw:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name, "")
    if not raw:
        return default
    return float(raw)


def _resolve_generation_mode(mode: str, target_value: str) -> str:
    if mode not in VALID_GENERATION_MODES:
        raise ValueError(f"Unsupported Cosmos 3 generation mode: {mode}")
    normalized = target_value.strip().lower()
    if mode == "t2v" or (mode == "auto" and normalized in EXTREME_DOMAIN_SHIFT_VALUES):
        return "t2v"
    return "i2v"


def _sanitize_header_value(value: str, fallback: str = "upload.bin") -> str:
    cleaned = re.sub(r'["\r\n]+', "", value).strip()
    return cleaned or fallback


def _validate_endpoint_url(endpoint_url: str) -> str:
    parsed = urllib.parse.urlparse(endpoint_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid endpoint URL (must use http or https with a host): {endpoint_url}")
    return endpoint_url.strip().rstrip("/")


def log(message: str) -> None:
    print(f"[AVOPS-CL] {message}", file=sys.stderr)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sanitize_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-") or "clip"


def discover_videos(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")
    return sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS)


def ffprobe_clip(video_path: Path) -> dict[str, Any]:
    """Return clip metadata via ffprobe when installed, falling back to basic file metadata."""
    record = {
        "filename": video_path.name,
        "scene_name": video_path.stem,
        "path": str(video_path.resolve()),
        "size_bytes": video_path.stat().st_size,
    }
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return record
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,r_frame_rate,nb_frames:format=duration",
        "-of",
        "json",
        str(video_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        info = json.loads(proc.stdout)
        streams = info.get("streams") or []
        if streams:
            stream = streams[0]
            record["codec"] = stream.get("codec_name")
            record["width"] = stream.get("width")
            record["height"] = stream.get("height")
            record["r_frame_rate"] = stream.get("r_frame_rate")
        duration = (info.get("format") or {}).get("duration")
        if duration is not None:
            record["duration_sec"] = round(float(duration), 3)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
        record["probe_warning"] = str(exc)
    return record


def probe_clips(input_dir: Path, out_manifest: Path) -> dict[str, Any]:
    videos = discover_videos(input_dir)
    if not videos:
        raise ValueError(f"No video files found in {input_dir}")
    clips = [ffprobe_clip(video) for video in videos]
    manifest = {
        "input_dir": str(input_dir.resolve()),
        "clip_count": len(clips),
        "total_size_bytes": sum(c["size_bytes"] for c in clips),
        "clips": clips,
    }
    write_json(out_manifest, manifest)
    return manifest


def load_odd_prompts_map(odd_output_dir: Path) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    prompts_path = odd_output_dir / PROMPTS_FILENAME
    if not prompts_path.is_file():
        return {}
    raw = load_json(prompts_path)
    entries = raw.get("prompts", []) if isinstance(raw, dict) else raw
    mapping = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = (
            str(entry.get("category", "")),
            str(entry.get("attribute", "")),
            str(entry.get("value", "")),
            str(entry.get("scene_name", "")),
        )
        mapping[key] = entry
    return mapping


def build_cosmos3_prompt_payload(
    attribute: str,
    value: str,
    base_prompt: str,
    scene_name: str,
) -> dict[str, Any]:
    """Wrap an ODD variation prompt in NVIDIA Cosmos 3 structured JSON prompt format."""
    directives = CONDITION_VISUAL_DIRECTIVES.get(
        (attribute, value),
        {
            "weather": f"{value} driving conditions",
            "lighting": f"realistic natural illumination matching {value} conditions",
            "atmosphere": f"consistent outdoor atmospheric cues for {value} driving",
        },
    )
    return {
        "subjects": [
            {
                "description": (
                    f"Forward-facing autonomous vehicle dashcam view continuing naturally from {scene_name} "
                    f"under {value} {attribute} conditions."
                ),
                "appearance_details": (
                    "Photorealistic highway and road traffic vehicles, clear lane markings, guardrails, "
                    "and natural road textures consistent with the reference frame."
                ),
                "action": "Smooth forward ego-vehicle driving along the existing lane trajectory.",
                "state_changes": f"Maintains steady driving motion while rendering {directives['weather']}.",
                "number_of_subjects": 1,
            }
        ],
        "background_setting": f"{base_prompt} Scene environment reflects {directives['weather']}.",
        "lighting": {
            "conditions": directives["lighting"],
            "direction": "Physically consistent sky and vehicle illumination",
            "shadows": "Soft, temporally coherent contact shadows anchored beneath vehicles",
            "illumination_effect": directives["atmosphere"],
        },
        "aesthetics": {
            "composition": "Forward automotive dashcam framing matching the reference camera viewpoint",
            "mood_atmosphere": directives["atmosphere"],
        },
        "cinematography": {
            "camera_motion": "Steady forward vehicle-mounted dashcam motion without sudden jumps",
            "framing": "Wide forward roadway perspective",
            "camera_angle": "Eye-level automotive windshield dashcam",
        },
        "style_medium": "Photorealistic automotive dashcam video",
        "temporal_caption": (
            f"Continuous forward driving sequence from the initial reference frame under {directives['weather']}. "
            f"{base_prompt}"
        ),
        "physical_realism": (
            "Strict adherence to automotive physics, stable lane geometry, coherent vehicle spacing, "
            "and realistic weather-surface interactions."
        ),
    }


def _run_ffmpeg_extract(ffmpeg: str, video_path: Path, frame_out: Path, timestamp_sec: float) -> None:
    cmd = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(timestamp_sec),
        "-i",
        str(video_path),
        "-vframes",
        "1",
        str(frame_out),
    ]
    subprocess.run(cmd, check=True, timeout=30)


def extract_reference_frame(video_path: Path, frame_out: Path, timestamp_sec: float = 1.0) -> Path:
    """Extract a PNG conditioning keyframe from a base driving clip using ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to extract conditioning keyframes")
    frame_out.parent.mkdir(parents=True, exist_ok=True)

    if timestamp_sec > 0.0:
        with contextlib.suppress(subprocess.CalledProcessError):
            _run_ffmpeg_extract(ffmpeg, video_path, frame_out, timestamp_sec)
        if frame_out.is_file() and frame_out.stat().st_size > 0:
            return frame_out
        _run_ffmpeg_extract(ffmpeg, video_path, frame_out, 0.0)
    else:
        _run_ffmpeg_extract(ffmpeg, video_path, frame_out, 0.0)

    if not frame_out.is_file() or frame_out.stat().st_size == 0:
        raise RuntimeError(f"Failed to extract reference frame from {video_path}")
    return frame_out


def select_diverse_gap_jobs(
    wfm: dict[str, Any],
    odd_prompts: dict[tuple[str, str, str, str], dict[str, Any]],
    max_clips: int = 6,
    attributes: Collection[str] = COSMOS3_ATTRIBUTES,
    video_dir_override: Path | None = None,
) -> list[dict[str, Any]]:
    """Select synthetic generation jobs round-robin across missing (attribute, value) buckets."""
    buckets = []
    for category, gap_attributes in (wfm.get("generation_gaps") or {}).items():
        for attribute, info in gap_attributes.items():
            if attribute not in attributes:
                continue
            for value, spec in (info.get("values") or {}).items():
                required = max(1, int(spec.get("required_count", 1)))
                candidates = list(spec.get("suitable_videos") or [])
                if candidates:
                    buckets.append((required, category, attribute, value, candidates))

    # Prioritize highest-shortfall missing conditions first, keeping declaration order on ties.
    buckets.sort(key=lambda item: -item[0])

    selected = []
    used_pairs = set()
    used_scenes = set()
    round_idx = 0
    while buckets and (max_clips <= 0 or len(selected) < max_clips):
        progressed = False
        for bucket_idx, (required, category, attribute, value, candidates) in enumerate(buckets):
            if max_clips > 0 and len(selected) >= max_clips:
                break
            if round_idx >= min(required, len(candidates)):
                continue
            # Prefer a base video not yet used in this batch for visual and dataset diversity.
            ordered = [candidates[(round_idx + bucket_idx + i) % len(candidates)] for i in range(len(candidates))]
            video = next((v for v in ordered if str(v["scene_name"]) not in used_scenes), ordered[0])
            scene_name = str(video["scene_name"])
            unique_key = (attribute, value, scene_name)
            if unique_key in used_pairs:
                video = next((v for v in ordered if (attribute, value, str(v["scene_name"])) not in used_pairs), None)
                if video is None:
                    continue
                scene_name = str(video["scene_name"])
                unique_key = (attribute, value, scene_name)
            used_pairs.add(unique_key)
            used_scenes.add(scene_name)
            raw_path = Path(str(video["video_path"]))
            resolved_video = (video_dir_override / raw_path.name) if video_dir_override else raw_path
            prompt_entry = odd_prompts.get((category, attribute, value, scene_name), {})
            base_prompt = str(
                prompt_entry.get("prompt")
                or f"Generate a realistic forward dashcam driving scene with {attribute}={value}. "
                "Preserve the camera viewpoint, road layout, and vehicle motion."
            )
            job_id = sanitize_slug(f"synth_{len(selected) + 1:02d}_{attribute}_{value}_{scene_name}")
            selected.append(
                {
                    "job_id": job_id,
                    "category": category,
                    "attribute": attribute,
                    "target_value": value,
                    "base_scene_name": scene_name,
                    "base_video_path": str(resolved_video),
                    "prompt_source": str(prompt_entry.get("source", "fallback")),
                    "odd_prompt_text": base_prompt,
                    "cosmos3_prompt": build_cosmos3_prompt_payload(attribute, value, base_prompt, scene_name),
                    "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
                }
            )
            progressed = True
        if not progressed:
            break
        round_idx += 1
    return selected


def build_jobs(
    odd_output_dir: Path,
    out_path: Path,
    max_clips: int = 6,
    frames_dir: Path | None = None,
    video_dir_override: Path | None = None,
    extract_frames: bool = True,
    mode: str = "auto",
) -> dict[str, Any]:
    if mode not in VALID_GENERATION_MODES:
        raise ValueError(f"Unsupported Cosmos 3 generation mode: {mode}")
    wfm_path = odd_output_dir / WFM_FILENAME
    if not wfm_path.is_file():
        raise FileNotFoundError(f"ODD generation input not found: {wfm_path}")
    wfm = load_json(wfm_path)
    odd_prompts = load_odd_prompts_map(odd_output_dir)
    jobs = select_diverse_gap_jobs(
        wfm=wfm,
        odd_prompts=odd_prompts,
        max_clips=max_clips,
        video_dir_override=video_dir_override,
    )
    if not jobs:
        raise ValueError(f"No eligible weather/timeOfDay gaps found in {wfm_path}")

    resolved_frames_dir = frames_dir or (out_path.parent / "reference_frames")
    for job in jobs:
        effective_mode = _resolve_generation_mode(mode, str(job["target_value"]))
        job["planned_mode"] = effective_mode
        if effective_mode == "t2v":
            job["reference_frame_path"] = None
            continue
        frame_path = resolved_frames_dir / f"{job['job_id']}.png"
        job["reference_frame_path"] = str(frame_path)
        if extract_frames:
            extract_reference_frame(Path(job["base_video_path"]), frame_path)

    plan = {
        "odd_output_dir": str(odd_output_dir.resolve()),
        "mode": mode,
        "job_count": len(jobs),
        "jobs": jobs,
    }
    write_json(out_path, plan)
    return plan


def encode_multipart_form(
    fields: dict[str, str],
    file_field: str | None = None,
    file_path: Path | None = None,
) -> tuple[bytes, str]:
    """Encode a multipart/form-data HTTP request body using stdlib only."""
    boundary = f"----AvopsCosmos3Boundary{uuid.uuid4().hex}"
    chunks = []
    for name, value in fields.items():
        safe_name = _sanitize_header_value(name, fallback="field")
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{safe_name}"\r\n\r\n'.encode())
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    if file_field and file_path:
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        safe_field = _sanitize_header_value(file_field, fallback="file")
        safe_filename = _sanitize_header_value(file_path.name)
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{safe_field}"; filename="{safe_filename}"\r\n'.encode())
        chunks.append(f"Content-Type: {mime}\r\n\r\n".encode())
        chunks.append(file_path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _validate_video_response(job_id: str, content_type: str, payload: bytes) -> None:
    if not payload:
        raise RuntimeError(f"Cosmos 3 returned empty video payload for {job_id}")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in {"application/json", "application/problem+json"} or media_type.startswith("text/"):
        preview = payload.decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"Cosmos 3 returned non-video response ({media_type}) for {job_id}: {preview}")
    trimmed = payload.lstrip()
    if trimmed.startswith((b"{", b"[", b"<!DOCTYPE", b"<html", b"<HTML")):
        preview = payload.decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"Cosmos 3 returned non-video payload for {job_id}: {preview}")


def synthesize_jobs(
    jobs_plan_path: Path,
    endpoint_url: str,
    out_dir: Path,
    size: str = "854x480",
    num_frames: int = 97,
    fps: int = 24,
    num_inference_steps: int = 35,
    guidance_scale: float = 6.0,
    flow_shift: float = 10.0,
    seed: int = 17,
    guardrails: bool = False,
    timeout_sec: float = 900.0,
    use_structured_prompt: bool = True,
    mode: str = "auto",
) -> dict[str, Any]:
    """Call vLLM-Omni POST /v1/videos/sync for each planned ODD gap job.

    ``mode="auto"`` uses T2V for extreme domain shifts (``rainy``, ``snowy``, ``foggy``, ``night``)
    so bright daytime reference frames do not anchor the diffusion output away from the target
    condition, and I2V (with ``input_reference``) for subtle atmospheric shifts.
    """
    if mode not in VALID_GENERATION_MODES:
        raise ValueError(f"Unsupported Cosmos 3 generation mode: {mode}")
    base_url = _validate_endpoint_url(endpoint_url)
    plan = load_json(jobs_plan_path)
    jobs = plan.get("jobs") or []
    if not jobs:
        raise ValueError(f"No jobs in plan: {jobs_plan_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    sync_url = f"{base_url}/v1/videos/sync"

    results = []
    for idx, job in enumerate(jobs):
        job_id = str(job["job_id"])
        effective_mode = _resolve_generation_mode(mode, str(job["target_value"]))
        ref_frame_raw = job.get("reference_frame_path")
        ref_frame = Path(str(ref_frame_raw)) if ref_frame_raw else None
        if effective_mode == "i2v" and (ref_frame is None or not ref_frame.is_file()):
            raise FileNotFoundError(f"Missing reference frame for {job_id}: {ref_frame}")

        prompt_str = json.dumps(job["cosmos3_prompt"]) if use_structured_prompt else str(job["odd_prompt_text"])
        neg_str = json.dumps(job["negative_prompt"]) if use_structured_prompt else "blurry, distorted, low quality"
        fields = {
            "prompt": prompt_str,
            "negative_prompt": neg_str,
            "size": size,
            "num_frames": str(num_frames),
            "fps": str(fps),
            "num_inference_steps": str(num_inference_steps),
            "guidance_scale": str(guidance_scale),
            "max_sequence_length": "4096",
            "flow_shift": str(flow_shift),
            "seed": str(seed + idx),
            "extra_params": json.dumps(
                {
                    "use_resolution_template": False,
                    "use_duration_template": False,
                    "guardrails": guardrails,
                }
            ),
        }
        if effective_mode == "i2v":
            body, content_type = encode_multipart_form(fields, file_field="input_reference", file_path=ref_frame)
        else:
            body, content_type = encode_multipart_form(fields)
        req = urllib.request.Request(
            sync_url,
            data=body,
            headers={"Content-Type": content_type, "Accept": "video/mp4"},
            method="POST",
        )
        t0 = time.monotonic()
        log(
            f"[{idx + 1}/{len(jobs)}] Synthesizing {job_id} "
            f"({job['attribute']}={job['target_value']}, mode={effective_mode}) via {sync_url}"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                resp_headers = getattr(resp, "headers", None)
                resp_content_type = resp_headers.get("Content-Type", "") if resp_headers is not None else ""
                video_bytes = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"Cosmos 3 synthesis failed for {job_id}: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cosmos 3 synthesis request failed for {job_id} ({sync_url}): {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise RuntimeError(f"Cosmos 3 synthesis transport error for {job_id} ({sync_url}): {exc}") from exc
        elapsed = round(time.monotonic() - t0, 2)
        _validate_video_response(job_id, resp_content_type, video_bytes)

        out_video = out_dir / f"{job_id}.mp4"
        out_video.write_bytes(video_bytes)
        record = {
            "job_id": job_id,
            "category": job["category"],
            "attribute": job["attribute"],
            "target_value": job["target_value"],
            "mode": effective_mode,
            "base_scene_name": job["base_scene_name"],
            "base_video_path": job["base_video_path"],
            "reference_frame_path": str(ref_frame) if (effective_mode == "i2v" and ref_frame is not None) else None,
            "output_video_path": str(out_video.resolve()),
            "size_bytes": len(video_bytes),
            "generation_seconds": elapsed,
            "size": size,
            "num_frames": num_frames,
            "fps": fps,
        }
        results.append(record)
        log(f"  -> Saved {out_video.name} ({len(video_bytes):,} bytes in {elapsed:.1f}s)")

    manifest = {
        "endpoint_url": base_url,
        "generated_count": len(results),
        "total_generation_seconds": round(sum(r["generation_seconds"] for r in results), 2),
        "clips": results,
    }
    write_json(out_dir / "synthesis_manifest.json", manifest)
    return manifest


def _extract_gap_map(final_gap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(g["attribute"]): g for g in (final_gap.get("detected_gaps") or [])}


def _lookup_vlm_answer(annotation: dict[str, Any], category: str, attribute: str) -> str | None:
    cat_block = annotation.get(category)
    if not isinstance(cat_block, dict):
        return None
    entry = cat_block.get(attribute)
    if isinstance(entry, dict):
        val = entry.get("answer")
        return str(val).strip().lower() if val is not None else None
    if entry is not None:
        return str(entry).strip().lower()
    return None


def compare_odd_reports(
    before_dir: Path,
    after_dir: Path,
    synthesis_manifest_path: Path | None,
    out_dir: Path,
) -> dict[str, Any]:
    """Compare Before vs. After ODD reports and compute per-clip VLM condition adherence."""
    before = load_json(before_dir / FINAL_GAP_FILENAME)
    after = load_json(after_dir / FINAL_GAP_FILENAME)

    b_sum = before["dataset_summary"]
    a_sum = after["dataset_summary"]
    overall_delta = round(float(a_sum["overall_odd_score"]) - float(b_sum["overall_odd_score"]), 4)

    category_deltas = {}
    all_cats = sorted(set(before.get("category_scores", {})) | set(after.get("category_scores", {})))
    for cat in all_cats:
        b_val = float(before.get("category_scores", {}).get(cat, 0.0))
        a_val = float(after.get("category_scores", {}).get(cat, 0.0))
        category_deltas[cat] = {
            "before": round(b_val, 4),
            "after": round(a_val, 4),
            "delta": round(a_val - b_val, 4),
        }

    b_gaps = _extract_gap_map(before)
    a_gaps = _extract_gap_map(after)
    attribute_deltas = []
    for attr in sorted(set(b_gaps) | set(a_gaps)):
        bg = b_gaps.get(attr)
        ag = a_gaps.get(attr)
        threshold = float((bg or ag)["threshold"])
        gap_closed = bg is not None and ag is None
        before_score = round(float(bg["odd_score"]), 4) if bg is not None else None
        after_score = round(float(ag["odd_score"]), 4) if ag is not None else None
        delta = round(after_score - before_score, 4) if (before_score is not None and after_score is not None) else None
        attribute_deltas.append(
            {
                "category": str((bg or ag)["category"]),
                "attribute": attr,
                "threshold": threshold,
                "before_score": before_score,
                "after_score": after_score,
                "delta": delta,
                "before_needed": int(bg["total_needed"]) if bg else 0,
                "after_needed": int(ag["total_needed"]) if ag else 0,
                "gap_closed": gap_closed,
            }
        )

    adherence_checks = []
    if synthesis_manifest_path and synthesis_manifest_path.is_file():
        manifest = load_json(synthesis_manifest_path)
        ann_dir = after_dir / "Text_Annotations"
        for clip in manifest.get("clips") or []:
            clip_stem = Path(str(clip["output_video_path"])).stem
            ann_path = ann_dir / f"{clip_stem}.json"
            perceived = None
            if ann_path.is_file():
                perceived = _lookup_vlm_answer(load_json(ann_path), str(clip["category"]), str(clip["attribute"]))
            target = str(clip["target_value"]).strip().lower()
            adherence_checks.append(
                {
                    "clip": clip_stem,
                    "attribute": str(clip["attribute"]),
                    "target_value": target,
                    "perceived_value": perceived,
                    "matched": perceived == target,
                }
            )

    matched_count = sum(1 for item in adherence_checks if item["matched"])
    adherence_rate = round(matched_count / len(adherence_checks), 4) if adherence_checks else None

    report = {
        "before_scenes": int(b_sum["total_scenes"]),
        "after_scenes": int(a_sum["total_scenes"]),
        "synthetic_clips_added": int(a_sum["total_scenes"]) - int(b_sum["total_scenes"]),
        "overall_odd_score": {
            "before": round(float(b_sum["overall_odd_score"]), 4),
            "after": round(float(a_sum["overall_odd_score"]), 4),
            "delta": overall_delta,
            "threshold": float(b_sum["odd_score_threshold"]),
        },
        "total_gaps": {
            "before": int(b_sum["total_gaps_detected"]),
            "after": int(a_sum["total_gaps_detected"]),
        },
        "total_variations_needed": {
            "before": int(b_sum["total_variations_generated"]),
            "after": int(a_sum["total_variations_generated"]),
            "reduction": int(b_sum["total_variations_generated"]) - int(a_sum["total_variations_generated"]),
        },
        "category_deltas": category_deltas,
        "attribute_deltas": attribute_deltas,
        "condition_adherence": {
            "checked": len(adherence_checks),
            "matched": matched_count,
            "rate": adherence_rate,
            "clips": adherence_checks,
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "closed_loop_report.json", report)

    lines = [
        "# AVOps Closed-Loop ODD Improvement Report",
        "",
        f"Evaluated `{report['before_scenes']}` baseline driving clips before synthesis and "
        f"`{report['after_scenes']}` clips (`+{report['synthetic_clips_added']}` Cosmos 3 synthetic clips) after.",
        "",
        "## Overall Metrics",
        "",
        "| Metric | Before | After | Delta |",
        "| --- | ---: | ---: | ---: |",
        f"| Overall ODD Score | {report['overall_odd_score']['before']:.4f} | "
        f"{report['overall_odd_score']['after']:.4f} | {overall_delta:+.4f} |",
        f"| Detected Gaps | {report['total_gaps']['before']} | {report['total_gaps']['after']} | "
        f"{report['total_gaps']['after'] - report['total_gaps']['before']:+d} |",
        f"| Variations Needed | {report['total_variations_needed']['before']} | "
        f"{report['total_variations_needed']['after']} | "
        f"{-report['total_variations_needed']['reduction']:+d} |",
        "",
        "## Category Scores",
        "",
        "| Category | Before | After | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for cat, vals in category_deltas.items():
        lines.append(f"| `{cat}` | {vals['before']:.4f} | {vals['after']:.4f} | {vals['delta']:+.4f} |")

    if attribute_deltas:
        lines.extend(
            [
                "",
                "## Attribute Gaps",
                "",
                "| Attribute | Threshold | Before | After | Delta |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in attribute_deltas:
            before_cell = (
                f"{item['before_score']:.4f}"
                if item["before_score"] is not None
                else f">= {item['threshold']} (no gap)"
            )
            if item["gap_closed"]:
                after_cell = f">= {item['threshold']} (closed)"
            elif item["after_score"] is not None:
                after_cell = f"{item['after_score']:.4f}"
            else:
                after_cell = "n/a"
            delta_cell = f"{item['delta']:+.4f}" if item["delta"] is not None else "n/a"
            lines.append(
                f"| `{item['attribute']}` | {item['threshold']} | {before_cell} | {after_cell} | {delta_cell} |"
            )

    if adherence_checks:
        lines.extend(
            [
                "",
                "## Cosmos 3 VLM Condition Adherence",
                "",
                "| Synthetic Clip | Attribute | Target | Perceived | Match |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in adherence_checks:
            lines.append(
                f"| `{item['clip']}` | `{item['attribute']}` | `{item['target_value']}` | "
                f"`{item['perceived_value'] or 'n/a'}` | {'Yes' if item['matched'] else 'No'} |"
            )

    (out_dir / "closed_loop_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zero-NGC AVOps closed-loop ODD + Cosmos 3 helper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe-clips", help="Stage 1: probe input driving videos")
    p_probe.add_argument("--input-dir", type=Path, required=True)
    p_probe.add_argument("--out", type=Path, required=True)

    p_jobs = sub.add_parser("build-jobs", help="Select diverse ODD gaps and build Cosmos 3 jobs")
    p_jobs.add_argument("--odd-output", type=Path, required=True)
    p_jobs.add_argument("--out", type=Path, required=True)
    p_jobs.add_argument("--max-clips", type=int, default=_env_int("AVOPS_MAX_CLIPS", 6))
    p_jobs.add_argument("--frames-dir", type=Path, default=None)
    p_jobs.add_argument("--video-dir", type=Path, default=None)
    p_jobs.add_argument("--mode", choices=("auto", "i2v", "t2v"), default=_env_str("COSMOS3_MODE", "auto"))
    p_jobs.add_argument("--no-extract-frames", action="store_true")

    p_syn = sub.add_parser("synthesize", help="Stage 3: synthesize driving clips via vLLM-Omni Cosmos 3")
    p_syn.add_argument("--jobs", type=Path, required=True)
    p_syn.add_argument("--endpoint-url", default=_env_str("COSMOS3_URL", "http://127.0.0.1:8000"))
    p_syn.add_argument("--out-dir", type=Path, required=True)
    p_syn.add_argument("--size", default=_env_str("COSMOS3_SIZE", "854x480"))
    p_syn.add_argument("--num-frames", type=int, default=_env_int("COSMOS3_NUM_FRAMES", 97))
    p_syn.add_argument("--fps", type=int, default=_env_int("COSMOS3_FPS", 24))
    p_syn.add_argument("--steps", type=int, default=_env_int("COSMOS3_STEPS", 35))
    p_syn.add_argument("--guidance-scale", type=float, default=_env_float("COSMOS3_GUIDANCE", 6.0))
    p_syn.add_argument("--flow-shift", type=float, default=_env_float("COSMOS3_FLOW_SHIFT", 10.0))
    p_syn.add_argument("--seed", type=int, default=_env_int("COSMOS3_SEED", 17))
    p_syn.add_argument("--timeout", type=float, default=_env_float("COSMOS3_TIMEOUT", 900.0))
    p_syn.add_argument("--mode", choices=("auto", "i2v", "t2v"), default=_env_str("COSMOS3_MODE", "auto"))
    p_syn.add_argument("--guardrails", action="store_true", help="Enable Cosmos guardrail checks on the server")
    p_syn.add_argument("--plain-prompt", action="store_true")

    p_cmp = sub.add_parser("compare-odd", help="Stage 4: compare Before vs. After ODD reports")
    p_cmp.add_argument("--before-dir", type=Path, required=True)
    p_cmp.add_argument("--after-dir", type=Path, required=True)
    p_cmp.add_argument("--synthesis-manifest", type=Path, default=None)
    p_cmp.add_argument("--out-dir", type=Path, required=True)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "probe-clips":
        manifest = probe_clips(args.input_dir, args.out)
        log(f"Probed {manifest['clip_count']} clip(s) -> {args.out}")
        return 0
    if args.command == "build-jobs":
        plan = build_jobs(
            odd_output_dir=args.odd_output,
            out_path=args.out,
            max_clips=args.max_clips,
            frames_dir=args.frames_dir,
            video_dir_override=args.video_dir,
            extract_frames=not args.no_extract_frames,
            mode=args.mode,
        )
        log(f"Planned {plan['job_count']} Cosmos 3 synthesis job(s) -> {args.out}")
        return 0
    if args.command == "synthesize":
        manifest = synthesize_jobs(
            jobs_plan_path=args.jobs,
            endpoint_url=args.endpoint_url,
            out_dir=args.out_dir,
            size=args.size,
            num_frames=args.num_frames,
            fps=args.fps,
            num_inference_steps=args.steps,
            guidance_scale=args.guidance_scale,
            flow_shift=args.flow_shift,
            seed=args.seed,
            guardrails=args.guardrails,
            timeout_sec=args.timeout,
            use_structured_prompt=not args.plain_prompt,
            mode=args.mode,
        )
        log(
            f"Synthesized {manifest['generated_count']} clip(s) in "
            f"{manifest['total_generation_seconds']:.1f}s -> {args.out_dir}"
        )
        return 0
    if args.command == "compare-odd":
        report = compare_odd_reports(
            before_dir=args.before_dir,
            after_dir=args.after_dir,
            synthesis_manifest_path=args.synthesis_manifest,
            out_dir=args.out_dir,
        )
        log(
            "Closed-loop ODD delta: "
            f"{report['overall_odd_score']['before']:.4f} -> {report['overall_odd_score']['after']:.4f} "
            f"({report['overall_odd_score']['delta']:+.4f})"
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
