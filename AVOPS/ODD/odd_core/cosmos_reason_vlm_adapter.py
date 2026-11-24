#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "accelerate==1.12.0",
#     "av==16.0.1",
#     "fastapi==0.115.12",
#     "numpy==2.2.6",
#     "pillow==12.0.0",
#     "torch==2.9.1",
#     "torchvision==0.24.1",
#     "transformers==4.57.3",
#     "uvicorn==0.34.2",
# ]
# ///

"""
Cosmos-Reason2-2B → OpenAI-compatible VLM adapter server.

Exposes a minimal ``/v1/chat/completions`` endpoint so that
``run_odd_pipeline.py`` (or any OpenAI client) can treat Cosmos-Reason2-2B
as a BYOM VLM, exactly like any vLLM-OMNI or NIM server.

Usage (run outside the Docker container, from ``AVOPS/ODD``)::

    uv run odd_core/cosmos_reason_vlm_adapter.py \
        --model nvidia/Cosmos-Reason2-2B \
        --port 8010

    # Then point config_starter.yaml at:
    #   endpoints.vlm.url: "http://127.0.0.1:8010/v1"
    #   endpoints.vlm.model: "nvidia/Cosmos-Reason2-2B"

Dependencies are pinned in the PEP 723 metadata above. ``uv run`` installs
them in an isolated script environment rather than using the repo-root project.

Processor setup and generation/decode flow follow NVIDIA's Cosmos-Reason2
inference sample (Apache-2.0):
https://github.com/nvidia-cosmos/cosmos-reason2/blob/main/scripts/inference_sample.py

Video input: ``video_url`` content parts (``data:video/...;base64`` URLs) are decoded with PyAV and
sampled across the whole clip at ``--video-fps`` (at most ``--video-max-frames`` frames, long side
at most ``--video-max-dim``); ``image_url`` parts are passed as single images.

Note: This adapter is the lightweight alternative to running vLLM-OMNI.
vLLM-OMNI is preferred for production (batch scheduling, continuous batching,
LoRA, etc.), but this adapter works on standalone GPU instances while vLLM-OMNI is
not available.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import time
import uuid
import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Annotated, Any

from fastapi import Body

warnings.filterwarnings("ignore")

# Default cache directory: the default hugging face cache directory
DEFAULT_CACHE_DIR = os.environ.get(
    "HF_HOME",
    os.path.expanduser("~/.cache/huggingface"),
)

# --- Lazy imports so the module is importable without heavy deps ------------
_model = None
_processor = None
_model_name: str = "nvidia/Cosmos-Reason2-2B"
_cache_dir: str = DEFAULT_CACHE_DIR

PIXELS_PER_TOKEN = 32**2

DEFAULT_VIDEO_FPS = 4.0
DEFAULT_VIDEO_MAX_FRAMES = 32
DEFAULT_VIDEO_MAX_DIM = 768


@dataclass
class VideoOptions:
    """How a clip is sampled before it is given to the model."""

    fps: float = DEFAULT_VIDEO_FPS
    max_frames: int = DEFAULT_VIDEO_MAX_FRAMES
    max_dim: int = DEFAULT_VIDEO_MAX_DIM


_video_options = VideoOptions()


def _load_single_model(
    model_name: str = "nvidia/Cosmos-Reason2-2B",
    max_gpu_memory: str | None = None,
    cache_dir: str | None = DEFAULT_CACHE_DIR,
) -> tuple[Any, Any]:
    import torch
    import transformers

    resolved_cache_dir = os.path.abspath(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    os.makedirs(resolved_cache_dir, exist_ok=True)
    os.environ.setdefault("HF_HOME", resolved_cache_dir)
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", resolved_cache_dir)

    # Check if a local snapshot directory exists in cache_dir
    candidate_paths = [
        model_name,
        os.path.join(resolved_cache_dir, model_name),
        os.path.join(resolved_cache_dir, os.path.basename(model_name)),
    ]
    model_target = model_name
    for path in candidate_paths:
        if os.path.isdir(path):
            model_target = path
            print(f"[cosmos-reason-adapter] Found local model directory at {model_target}")
            break

    print(f"[cosmos-reason-adapter] Loading {model_target} (cache_dir={resolved_cache_dir}) …")
    transformers.set_seed(0)

    # transformers v4/v5 compat: prefer AutoModelForImageTextToText
    ModelClass = (
        transformers.AutoModelForImageTextToText
        if hasattr(transformers, "AutoModelForImageTextToText")
        else transformers.AutoModelForVision2Seq
    )
    hf_token = os.environ.get("HF_TOKEN") or None

    load_kwargs: dict[str, Any] = {
        "dtype": torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,
        "device_map": "auto",
        "attn_implementation": "sdpa",
        "cache_dir": resolved_cache_dir,
    }
    if hf_token:
        load_kwargs["token"] = hf_token
    else:
        load_kwargs["token"] = True

    if max_gpu_memory:
        load_kwargs["max_memory"] = {0: max_gpu_memory, "cpu": "30GiB"}

    try:
        model = ModelClass.from_pretrained(
            model_target,
            revision="9ce19a19",
            **load_kwargs,
        )
        processor = transformers.AutoProcessor.from_pretrained(
            model_target,
            revision="9ce19a19",
            cache_dir=resolved_cache_dir,
            token=load_kwargs["token"],
        )
    except Exception as exc:
        err_msg = str(exc)
        exc_type = type(exc).__name__
        if (
            "401" in err_msg
            or "token" in err_msg.lower()
            or "LocalTokenNotFoundError" in exc_type
            or "GatedRepoError" in exc_type
            or "restricted" in err_msg.lower()
        ):
            print(
                f"\n[cosmos-reason-adapter] [ERROR] Access to model '{model_name}' requires authentication:\n"
                f"Cosmos-Reason models are gated on Hugging Face.\n"
                f"1. Agree to terms at: https://huggingface.co/{model_name}\n"
                f'2. Set your Hugging Face token: export HF_TOKEN="hf_..." (or run `huggingface-cli login`).\n'
            )
        raise

    # Limit vision tokens to keep memory under control
    min_vision_tokens = 256
    max_vision_tokens = 4096

    for attr in ("image_processor", "video_processor"):
        proc = getattr(processor, attr, None)
        if proc is not None:
            if hasattr(proc, "size") and isinstance(proc.size, dict):
                proc.size = {
                    "shortest_edge": min_vision_tokens * PIXELS_PER_TOKEN,
                    "longest_edge": max_vision_tokens * PIXELS_PER_TOKEN,
                }
            if hasattr(proc, "min_pixels"):
                proc.min_pixels = min_vision_tokens * PIXELS_PER_TOKEN
            if hasattr(proc, "max_pixels"):
                proc.max_pixels = max_vision_tokens * PIXELS_PER_TOKEN

    print(f"[cosmos-reason-adapter] {model_name} ready on {model.device} (cache: {resolved_cache_dir})")
    return model, processor


def _load_model(
    model_name: str = "nvidia/Cosmos-Reason2-2B",
    max_gpu_memory: str | None = None,
    cache_dir: str | None = DEFAULT_CACHE_DIR,
) -> None:
    global _model, _processor, _model_name, _cache_dir

    _cache_dir = os.path.abspath(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    _model, _processor = _load_single_model(
        model_name=model_name,
        max_gpu_memory=max_gpu_memory,
        cache_dir=_cache_dir,
    )
    _model_name = model_name


def _data_url_bytes(url: str) -> bytes:
    if not url.startswith("data:"):
        raise ValueError("Only data: URLs are supported for image_url and video_url content")
    return base64.b64decode(url.split(",", 1)[1])


def sample_video_frames(video_bytes: bytes, options: VideoOptions) -> tuple[Any, dict[str, Any]]:
    """Sample frames evenly across the whole clip.

    Returns an array of shape (frames, height, width, 3) and the metadata the processor needs
    to time the frames: total frame count, effective fps, duration, and frame indices.
    """
    import av
    import numpy as np

    with av.open(BytesIO(video_bytes)) as container:
        stream = container.streams.video[0]
        source_fps = float(stream.average_rate or 30)
        duration = float(container.duration / av.time_base) if container.duration else (stream.frames or 1) / source_fps
        count = max(1, min(options.max_frames, round(duration * options.fps)))
        targets = [(index + 0.5) * duration / count for index in range(count)]
        frames: list[Any] = []
        for frame in container.decode(stream):
            timestamp = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
            while len(frames) < count and timestamp >= targets[len(frames)]:
                scale = min(1.0, options.max_dim / max(frame.width, frame.height))
                width = max(2, int(frame.width * scale) // 2 * 2)
                height = max(2, int(frame.height * scale) // 2 * 2)
                frames.append(frame.reformat(width=width, height=height, format="rgb24").to_ndarray())
            if len(frames) >= count:
                break
    if not frames:
        raise ValueError("The video has no decodable frames")
    metadata = {
        "total_num_frames": len(frames),
        "fps": len(frames) / duration if duration > 0 else options.fps,
        "duration": duration,
        "frames_indices": list(range(len(frames))),
    }
    return np.stack(frames), metadata


def _media_item(part: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Convert an image_url or video_url content part to a processor item and optional video metadata."""
    from PIL import Image

    if part["type"] == "image_url":
        image = Image.open(BytesIO(_data_url_bytes(part["image_url"]["url"]))).convert("RGB")
        return {"type": "image", "image": image}, None
    frames, metadata = sample_video_frames(_data_url_bytes(part["video_url"]["url"]), _video_options)
    return {"type": "video", "video": frames}, metadata


def _apply_chat_template(conversation: list[dict[str, Any]], video_metadata: list[dict[str, Any]]) -> Any:
    kwargs: dict[str, Any] = {
        "tokenize": True,
        "add_generation_prompt": True,
        "return_dict": True,
        "return_tensors": "pt",
    }
    if not video_metadata:
        return _processor.apply_chat_template(conversation, **kwargs)
    from transformers.video_utils import VideoMetadata

    options = {"do_sample_frames": False, "video_metadata": [VideoMetadata(**m) for m in video_metadata]}
    try:
        return _processor.apply_chat_template(conversation, processor_kwargs=options, **kwargs)
    except TypeError:  # transformers releases that take the options directly
        return _processor.apply_chat_template(conversation, **options, **kwargs)


def _run_inference(
    messages: list[dict[str, Any]],
    max_new_tokens: int = 512,
    requested_model: str | None = None,
) -> tuple[str, str]:
    """Run Cosmos-Reason2-2B inference from an OpenAI messages list."""
    import torch

    if _model is None or _processor is None:
        raise RuntimeError(f"Model {_model_name} not loaded")

    active_model = _model
    active_processor = _processor
    active_model_name = _model_name

    # Build the processor conversation from the system and user turns
    conversation: list[dict[str, Any]] = []
    video_metadata: list[dict[str, Any]] = []

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            conversation.append({"role": role, "content": [{"type": "text", "text": content}]})
        elif isinstance(content, list):
            proc_content: list[dict[str, Any]] = []
            for part in content:
                if part.get("type") == "text":
                    proc_content.append({"type": "text", "text": part["text"]})
                elif part.get("type") in ("image_url", "video_url"):
                    item, metadata = _media_item(part)
                    proc_content.append(item)
                    if metadata is not None:
                        video_metadata.append(metadata)
            conversation.append({"role": role, "content": proc_content})

    inputs = _apply_chat_template(conversation, video_metadata)

    inputs = inputs.to(active_model.device)

    with torch.no_grad():
        generated_ids = active_model.generate(**inputs, max_new_tokens=max_new_tokens)

    generated_ids_trimmed = [
        out_ids[len(in_ids) :] for in_ids, out_ids in zip(inputs.input_ids, generated_ids, strict=False)
    ]

    output_text = active_processor.batch_decode(
        generated_ids_trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )

    raw = output_text[0].strip()
    # Strip chain-of-thought reasoning blocks
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"<thought>.*?</thought>", "", raw, flags=re.DOTALL).strip()
    return raw, active_model_name


def _build_app():
    """Build the FastAPI application."""
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import JSONResponse
    except ImportError as exc:
        raise ImportError("Install fastapi: pip install fastapi uvicorn") from exc

    app = FastAPI(title="Cosmos-Reason2-2B VLM Adapter", version="1.0.0")

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "model": _model_name,
            "cache_dir": _cache_dir,
        }

    @app.get("/v1/models")
    async def list_models():
        return {
            "object": "list",
            "data": [
                {
                    "id": _model_name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "nvidia",
                }
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(body: Annotated[dict[str, Any], Body()]):
        messages: list[dict[str, Any]] = body.get("messages", [])
        max_new_tokens: int = body.get("max_tokens", 512)
        requested_model: str | None = body.get("model")

        try:
            output, resolved_model = _run_inference(
                messages,
                max_new_tokens=max_new_tokens,
                requested_model=requested_model,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": resolved_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": output},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": -1,
                    "completion_tokens": -1,
                    "total_tokens": -1,
                },
            }
        )

    return app


def main():
    parser = argparse.ArgumentParser(description="Cosmos-Reason2-2B VLM adapter (default: nvidia/Cosmos-Reason2-2B)")
    parser.add_argument(
        "--model",
        default="nvidia/Cosmos-Reason2-2B",
        help="Model ID or local path (default: nvidia/Cosmos-Reason2-2B)",
    )
    parser.add_argument(
        "--cache-dir",
        default=DEFAULT_CACHE_DIR,
        help=f"Directory to cache model files (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8010,
        help="Port to listen on (default: 8010)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--max-gpu-memory",
        default=None,
        help=(
            "Maximum GPU memory allocation for model (e.g. 4GiB), offloading the rest to CPU. "
            "If not set, uses available GPU memory."
        ),
    )
    parser.add_argument(
        "--video-fps",
        type=float,
        default=DEFAULT_VIDEO_FPS,
        help=f"Frames per second sampled from video clips (default: {DEFAULT_VIDEO_FPS})",
    )
    parser.add_argument(
        "--video-max-frames",
        type=int,
        default=DEFAULT_VIDEO_MAX_FRAMES,
        help=f"Maximum frames sampled from one clip, across its whole length (default: {DEFAULT_VIDEO_MAX_FRAMES})",
    )
    parser.add_argument(
        "--video-max-dim",
        type=int,
        default=DEFAULT_VIDEO_MAX_DIM,
        help=f"Longest side in pixels of sampled frames (default: {DEFAULT_VIDEO_MAX_DIM})",
    )
    args = parser.parse_args()

    _video_options.fps = args.video_fps
    _video_options.max_frames = args.video_max_frames
    _video_options.max_dim = args.video_max_dim

    _load_model(
        model_name=args.model,
        max_gpu_memory=args.max_gpu_memory,
        cache_dir=args.cache_dir,
    )

    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError("Install uvicorn: pip install uvicorn") from exc

    app = _build_app()
    print(f"[cosmos-reason-adapter] Serving on http://{args.host}:{args.port}/v1")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
