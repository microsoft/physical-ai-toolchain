#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

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
    #   endpoints.vlm.url: "http://localhost:8010/v1"
    #   endpoints.vlm.model: "nvidia/Cosmos-Reason2-2B"

Requires (auto-installed by uv if not present):
    pip install fastapi uvicorn torch transformers accelerate pillow

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
from io import BytesIO
from typing import Annotated, Any

from fastapi import Body

warnings.filterwarnings("ignore")

# Default cache directory: the models/ folder two levels above this module
DEFAULT_CACHE_DIR = os.environ.get(
    "COSMOS_MODEL_CACHE_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "models")),
)

# --- Lazy imports so the module is importable without heavy deps ------------
_model = None
_processor = None
_model_name: str = "nvidia/Cosmos-Reason2-2B"
_cache_dir: str = DEFAULT_CACHE_DIR

PIXELS_PER_TOKEN = 32**2


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
        "trust_remote_code": True,
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
            **load_kwargs,
        )
        processor = transformers.AutoProcessor.from_pretrained(
            model_target,
            trust_remote_code=True,
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
                f"2. Set your Hugging Face token: export HF_TOKEN=\"hf_...\" (or run `huggingface-cli login`).\n"
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


def _extract_image_from_message(content: list[dict[str, Any]]) -> Any | None:
    """Pull the first image or video frame out of a chat message content list."""
    from PIL import Image

    for part in content:
        p_type = part.get("type")
        if p_type == "image_url":
            url: str = part["image_url"]["url"]
            if url.startswith("data:"):
                _, b64 = url.split(",", 1)
                return Image.open(BytesIO(base64.b64decode(b64))).convert("RGB")
        elif p_type == "video_url":
            url: str = part["video_url"]["url"]
            if url.startswith("data:"):
                _, b64 = url.split(",", 1)
                vid_bytes = base64.b64decode(b64)
                try:
                    import av
                    with av.open(BytesIO(vid_bytes)) as container:
                        if container.streams.video:
                            for frame in container.decode(container.streams.video[0]):
                                return frame.to_image().convert("RGB")
                except Exception as e:
                    print(f"[cosmos-reason-adapter] Video frame extraction error: {e}")
    return None


def _run_inference(
    messages: list[dict[str, Any]],
    max_new_tokens: int = 512,
    requested_model: str | None = None,
) -> tuple[str, str]:
    """Run Cosmos-Reason2-2B inference from an OpenAI messages list."""
    import torch
    from PIL import Image

    if _model is None or _processor is None:
        raise RuntimeError(f"Model {_model_name} not loaded")

    active_model = _model
    active_processor = _processor
    active_model_name = _model_name

    # Build processor conversation — pull system + user turns
    conversation = []
    pil_image: Image.Image | None = None

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            conversation.append(
                {
                    "role": role,
                    "content": [{"type": "text", "text": content}],
                }
            )
        elif isinstance(content, list):
            # Extract image/frame if present
            if pil_image is None:
                pil_image = _extract_image_from_message(content)

            text_parts = [p["text"] for p in content if p.get("type") == "text"]
            proc_content: list[dict] = []
            if pil_image is not None and role == "user":
                proc_content.append({"type": "image", "image": pil_image})
            proc_content.extend({"type": "text", "text": t} for t in text_parts)
            conversation.append({"role": role, "content": proc_content})

    # Process inputs
    inputs = active_processor.apply_chat_template(
        conversation,
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        return_tensors="pt",
    )

    inputs = inputs.to(active_model.device)

    with torch.no_grad():
        generated_ids = active_model.generate(**inputs, max_new_tokens=max_new_tokens)

    generated_ids_trimmed = [
        out_ids[len(in_ids):]
        for in_ids, out_ids in zip(inputs.input_ids, generated_ids, strict=False)
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
    parser = argparse.ArgumentParser(
        description="Cosmos-Reason2-2B VLM adapter (default: nvidia/Cosmos-Reason2-2B)"
    )
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
        default="0.0.0.0",
        help="Host to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--max-gpu-memory",
        default=None,
        help=(
            "Maximum GPU memory allocation for model (e.g. 4GiB), offloading the rest to CPU. "
            "If not set, uses available GPU memory."
        ),
    )
    args = parser.parse_args()

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
