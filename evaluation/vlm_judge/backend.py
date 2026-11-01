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
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

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
    ) -> str:
        raise NotImplementedError


# -------------------------------------------------------------------------
# Local Hugging Face Qwen3-VL backend
# -------------------------------------------------------------------------


class Qwen3VLBackend(JudgeBackend):
    """Local Qwen3-VL judge backed by Hugging Face transformers.

    Defaults to ``Qwen/Qwen3-VL-4B-Instruct`` which fits on a 24GB GPU in
    bf16. Larger variants (``8B``, ``30B-A3B``) work the same way; pass the
    model id via ``model_id`` and adjust ``dtype`` / ``device_map`` as needed.
    Hub models require ``revision`` as a full commit SHA. Explicit absolute,
    ``./`` or ``../`` directories load offline without a revision. Repository
    Python code is never enabled.
    """

    def __init__(
        self,
        *,
        model_id: str = "Qwen/Qwen3-VL-4B-Instruct",
        revision: str | None = None,
        device_map: str = "auto",
        dtype: str = "bfloat16",
        attn_implementation: str | None = "sdpa",
        trust_remote_code: bool = False,
    ) -> None:
        if trust_remote_code:
            raise ValueError("trust_remote_code=True is unsupported; use a native model implementation")
        local_files_only = Path(model_id).is_absolute() or model_id.startswith(("./", "../"))
        if local_files_only:
            directory = Path(model_id).resolve()
            if not directory.is_dir():
                raise ValueError("Explicit model directory must exist; no Hub fallback is allowed")
            if revision is not None:
                raise ValueError("Omit revision for a local model directory; a Hub pin cannot verify local artifacts")
            model_id = str(directory)
        else:
            if revision is None or re.fullmatch(r"[0-9a-fA-F]{40}", revision) is None:
                raise ValueError("Hub model revision must be a full 40-hex commit SHA")
            if Path(model_id).exists():
                raise ValueError(
                    "Hub ID shadows a local path; use an explicit ./ directory or a different working directory"
                )
            revision = revision.lower()

        import torch
        from transformers import AutoProcessor

        self.name = model_id
        self._torch = torch
        self._dtype = getattr(torch, dtype)
        self._processor = AutoProcessor.from_pretrained(
            model_id,
            revision=revision,
            trust_remote_code=trust_remote_code,
            local_files_only=local_files_only,
        )
        self._model = _load_qwen3_vl_model(
            model_id=model_id,
            revision=revision,
            device_map=device_map,
            dtype=self._dtype,
            attn_implementation=attn_implementation,
            trust_remote_code=trust_remote_code,
            local_files_only=local_files_only,
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
    revision: str | None,
    device_map: str,
    dtype: Any,
    attn_implementation: str | None,
    trust_remote_code: bool,
    local_files_only: bool,
) -> Any:
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
                revision=revision,
                dtype=dtype,
                device_map=device_map,
                attn_implementation=attn_implementation,
                trust_remote_code=trust_remote_code,
                local_files_only=local_files_only,
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

    Azure endpoints use refreshable Azure CLI bearer tokens. Other endpoints
    resolve the API key from ``api_key`` or the ``OPENAI_API_KEY`` env var.
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

        endpoint = urlsplit(base_url)
        credential = api_key or os.environ.get("OPENAI_API_KEY", "EMPTY")
        if (endpoint.hostname or "").endswith(
            (".openai.azure.com", ".services.ai.azure.com", ".cognitiveservices.azure.com")
        ):
            if endpoint.scheme != "https" or endpoint.username is not None or endpoint.password is not None:
                raise ValueError("Azure endpoints require HTTPS without URL credentials")
            from azure.identity import AzureCliCredential, get_bearer_token_provider

            credential = get_bearer_token_provider(AzureCliCredential(), "https://cognitiveservices.azure.com/.default")

        self.name = model
        self._client = OpenAI(
            base_url=base_url,
            api_key=credential,
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
