"""VLM backends for the judge harness.

The harness talks to a single ``JudgeBackend`` interface so the same prompts
and scoring logic work against:

* a local Hugging Face Qwen3-VL model (``Qwen3VLBackend``);
* any OpenAI-compatible HTTP server (vLLM, NVIDIA NIM, Azure OpenAI) via
  ``OpenAICompatibleBackend``.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PIL.Image import Image

_LOGGER = logging.getLogger("evaluation.vlm_judge")


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    max_new_tokens: int = 512
    temperature: float = 0.0
    top_p: float = 1.0


class JudgeBackend(ABC):
    """Abstract VLM backend that returns text given a prompt + frame stream."""

    name: str

    @abstractmethod
    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: Sequence[Image],
        config: GenerationConfig,
    ) -> str: ...


# -------------------------------------------------------------------------
# Local Hugging Face Qwen3-VL backend
# -------------------------------------------------------------------------


class Qwen3VLBackend(JudgeBackend):
    """Local Qwen3-VL judge backed by Hugging Face transformers.

    Defaults to ``Qwen/Qwen3-VL-4B-Instruct`` which fits on a 24GB GPU in
    bf16. Larger variants (``8B``, ``30B-A3B``) work the same way; pass the
    model id via ``model_id`` and adjust ``dtype`` / ``device_map`` as needed.
    """

    def __init__(
        self,
        *,
        model_id: str = "Qwen/Qwen3-VL-4B-Instruct",
        device_map: str = "auto",
        dtype: str = "bfloat16",
        attn_implementation: str | None = "sdpa",
        trust_remote_code: bool = True,
    ) -> None:
        import torch
        from transformers import AutoProcessor

        self.name = model_id
        self._torch = torch
        self._dtype = getattr(torch, dtype)
        self._processor = AutoProcessor.from_pretrained(
            model_id,
            trust_remote_code=trust_remote_code,
        )
        self._model = _load_qwen3_vl_model(
            model_id=model_id,
            device_map=device_map,
            dtype=self._dtype,
            attn_implementation=attn_implementation,
            trust_remote_code=trust_remote_code,
        )
        self._model.eval()

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: Sequence[Image],
        config: GenerationConfig,
    ) -> str:
        torch = self._torch
        image_list = list(images)
        messages = [
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            {
                "role": "user",
                "content": [
                    *[{"type": "image", "image": img} for img in image_list],
                    {"type": "text", "text": user_prompt},
                ],
            },
        ]
        text = self._processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        processor_kwargs: dict[str, Any] = {
            "text": [text],
            "return_tensors": "pt",
            "padding": True,
        }
        if image_list:
            processor_kwargs["images"] = image_list
        inputs = self._processor(**processor_kwargs)
        inputs = {k: v.to(self._model.device) for k, v in inputs.items()}

        gen_kwargs: dict[str, Any] = {
            "max_new_tokens": config.max_new_tokens,
            "do_sample": config.temperature > 0.0,
        }
        if config.temperature > 0.0:
            gen_kwargs["temperature"] = config.temperature
            gen_kwargs["top_p"] = config.top_p

        with torch.inference_mode():
            output_ids = self._model.generate(**inputs, **gen_kwargs)
        prompt_len = inputs["input_ids"].shape[1]
        new_tokens = output_ids[:, prompt_len:]
        decoded = self._processor.batch_decode(new_tokens, skip_special_tokens=True)
        return decoded[0] if decoded else ""


def _load_qwen3_vl_model(
    *,
    model_id: str,
    device_map: str,
    dtype,
    attn_implementation: str | None,
    trust_remote_code: bool,
):
    """Resolve the correct Qwen3-VL model class against the installed transformers."""
    import transformers

    candidates = (
        "Qwen3VLForConditionalGeneration",
        "Qwen3VLMoeForConditionalGeneration",
        "AutoModelForImageTextToText",
        "AutoModelForVision2Seq",
    )
    last_err: Exception | None = None
    for cls_name in candidates:
        cls = getattr(transformers, cls_name, None)
        if cls is None:
            continue
        try:
            return cls.from_pretrained(
                model_id,
                dtype=dtype,
                device_map=device_map,
                attn_implementation=attn_implementation,
                trust_remote_code=trust_remote_code,
            )
        except Exception as err:
            last_err = err
            _LOGGER.debug("Qwen3-VL load via %s failed: %s", cls_name, err)
            continue
    raise RuntimeError(
        f"Unable to load {model_id} with installed transformers; last error: {last_err}",
    )


# -------------------------------------------------------------------------
# OpenAI-compatible HTTP backend (vLLM / NIM / Azure OpenAI)
# -------------------------------------------------------------------------


class OpenAICompatibleBackend(JudgeBackend):
    """Drives any OpenAI-compatible chat-completions endpoint.

    Resolves the API key from ``api_key`` or the ``OPENAI_API_KEY`` env var.
    Frames are JPEG-encoded and embedded as ``image_url`` data URIs.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        from openai import OpenAI

        self.name = model
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or os.environ.get("OPENAI_API_KEY", "EMPTY"),
            timeout=timeout_s,
        )
        self._model = model

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: Sequence[Image],
        config: GenerationConfig,
    ) -> str:
        from .frames import encode_jpeg_b64

        b64_frames = encode_jpeg_b64(images)
        content = [
            *[
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b}"},
                }
                for b in b64_frames
            ],
            {"type": "text", "text": user_prompt},
        ]
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            temperature=config.temperature,
            top_p=config.top_p,
            max_tokens=config.max_new_tokens,
        )
        return response.choices[0].message.content or ""


# -------------------------------------------------------------------------
# Echo backend — for offline smoke tests without a real model
# -------------------------------------------------------------------------


class EchoBackend(JudgeBackend):
    """Deterministic backend that emits canned valid responses.

    Lets the CLI run end-to-end on any host (no GPU, no network) for shape
    validation and CI smoke tests. Outcome always returns SUCCESS; process
    returns a strictly increasing 0-100 array.
    """

    name = "echo"

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: Sequence[Image],
        config: GenerationConfig,
    ) -> str:
        del system_prompt, config
        if "SUCCESSFULLY completed" in user_prompt:
            return "<think>Looks completed.</think><answer>A</answer>"
        n = len(images)
        if n == 0:
            return "[]"
        values = [round(100.0 * i / max(1, n - 1)) for i in range(n)]
        return str(values)
