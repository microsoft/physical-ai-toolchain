"""Minimal OpenAI-compatible chat-completions shim for Qwen3VLBackend.

A thin FastAPI app that exposes ``POST /v1/chat/completions`` so any
OpenAI-compatible client can drive a locally-loaded ``Qwen3VLBackend``.
Use it as the standalone model server when vLLM / NIM / Azure OpenAI is
not available — the dataviewer (or any other consumer) then connects
via ``VLM_JUDGE_BACKEND=openai-compat`` and ``VLM_JUDGE_BASE_URL=
http://<host>:<port>/v1``.

Launch:

    python -m evaluation.vlm_judge.openai_shim --port 8001 \
        --model-id Qwen/Qwen3-VL-4B-Instruct
"""

from __future__ import annotations

import argparse
import base64
import io
import logging
import os
import time
import uuid
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from .backend import GenerationConfig, JudgeBackend, Qwen3VLBackend

# Remote image fetching is OFF by default: the shim runs untrusted-input
# inference and can bind beyond localhost, so fetching arbitrary URLs
# server-side is an SSRF / request-hang risk. The dataviewer always sends
# images as ``data:`` URIs. Set VLM_SHIM_ALLOW_REMOTE_IMAGES=true to opt into
# bounded http(s) fetching on a trusted network.
_ALLOW_REMOTE_IMAGES = os.environ.get("VLM_SHIM_ALLOW_REMOTE_IMAGES", "false").lower() == "true"
_REMOTE_IMAGE_TIMEOUT_S = float(os.environ.get("VLM_SHIM_REMOTE_IMAGE_TIMEOUT_S", "10"))

_LOGGER = logging.getLogger("evaluation.vlm_judge.openai_shim")


class ImageUrl(BaseModel):
    url: str


class ContentPart(BaseModel):
    type: str
    text: str | None = None
    image_url: ImageUrl | None = None


class Message(BaseModel):
    role: str
    content: str | list[ContentPart]


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[Message]
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = Field(default=512, ge=1)
    stream: bool = False


def build_app(backend: JudgeBackend):
    """Build a FastAPI app that serves OpenAI-compatible chat completions."""
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Qwen3-VL OpenAI-compat shim", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "model": backend.name}

    @app.get("/v1/models")
    def list_models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": backend.name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "local",
                },
            ],
        }

    @app.post("/v1/chat/completions")
    def chat_completions(request: ChatCompletionRequest) -> dict[str, Any]:
        if request.stream:
            raise HTTPException(status_code=400, detail="Streaming is not supported")
        system_prompt, user_prompt, images = _flatten_messages(request.messages)
        cfg = GenerationConfig(
            max_new_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
        )
        text = backend.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            images=images,
            config=cfg,
        )
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": backend.name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                },
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    return app


def _flatten_messages(messages: Sequence[Message]):
    from PIL import Image as _Image

    system_parts: list[str] = []
    user_parts: list[str] = []
    images: list[_Image.Image] = []
    for msg in messages:
        content = msg.content
        text = ""
        if isinstance(content, str):
            text = content
        else:
            for part in content:
                if part.type == "text" and part.text:
                    text += part.text
                elif part.type == "image_url" and part.image_url is not None:
                    images.append(_decode_image(part.image_url.url))
        if msg.role == "system":
            system_parts.append(text)
        elif msg.role in ("user", "assistant"):
            user_parts.append(text)
    return (
        "\n\n".join(p for p in system_parts if p),
        "\n\n".join(p for p in user_parts if p),
        images,
    )


def _decode_image(url: str):
    from fastapi import HTTPException
    from PIL import Image

    if url.startswith("data:"):
        _, _, b64 = url.partition(",")
        data = base64.b64decode(b64)
        return Image.open(io.BytesIO(data)).convert("RGB")
    if url.startswith(("http://", "https://")):
        if not _ALLOW_REMOTE_IMAGES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Remote image URLs are disabled; send images as data: URIs or set "
                    "VLM_SHIM_ALLOW_REMOTE_IMAGES=true to enable bounded fetching on a trusted network."
                ),
            )
        import urllib.request

        with urllib.request.urlopen(url, timeout=_REMOTE_IMAGE_TIMEOUT_S) as response:
            return Image.open(io.BytesIO(response.read())).convert("RGB")
    raise HTTPException(status_code=400, detail=f"Unsupported image_url scheme: {url[:32]}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vlm_judge.openai_shim")
    # Bind localhost by default; override with --host/VLM_SHIM_HOST only on a
    # trusted network (the shim performs no auth on /v1/chat/completions).
    parser.add_argument("--host", default=os.environ.get("VLM_SHIM_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VLM_SHIM_PORT", "8001")))
    parser.add_argument(
        "--model-id",
        default=os.environ.get("VLM_SHIM_MODEL_ID", "Qwen/Qwen3-VL-4B-Instruct"),
    )
    parser.add_argument(
        "--model-revision",
        default=os.environ.get("VLM_SHIM_MODEL_REVISION") or None,
        help="Immutable HF commit SHA to pin the model/processor download",
    )
    parser.add_argument("--device-map", default=os.environ.get("VLM_SHIM_DEVICE_MAP", "auto"))
    parser.add_argument("--dtype", default=os.environ.get("VLM_SHIM_DTYPE", "bfloat16"))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _LOGGER.info("Loading %s on device_map=%s dtype=%s", args.model_id, args.device_map, args.dtype)
    backend = Qwen3VLBackend(
        model_id=args.model_id,
        revision=args.model_revision,
        device_map=args.device_map,
        dtype=args.dtype,
    )
    app = build_app(backend)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
