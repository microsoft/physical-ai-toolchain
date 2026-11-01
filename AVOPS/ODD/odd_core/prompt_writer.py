# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

"""Summarize ODD scene variations into one text paragraph for Cosmos generation models.

Each variation file is a structured question/answer annotation of a base scene with the
gap attribute set to its target value. Cosmos Transfer-style models take a single text
prompt, so this module turns every (scene, gap attribute, target value) into one paragraph
using the same OpenAI-compatible endpoint as perception, and falls back to a deterministic
template when the endpoint is unavailable or returns an unusable answer.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .engine import ODDCoverageConfig, modify_annotation_for_attribute

logger = logging.getLogger(__name__)

PROMPTS_FILENAME = "generation_prompts.json"
PRESERVE_SENTENCE = "Preserve the camera viewpoint, road layout, and vehicle motion."
MIN_CHARS = 40
MAX_CHARS = 1500

SYSTEM_PROMPT = (
    "You write text prompts for a video world foundation model that edits driving videos. "
    "Reply with exactly one plain-text paragraph and nothing else: no lists, headings, "
    "quotes, or explanations."
)

USER_PROMPT = """Facts about a driving scene (question -> answer):
{facts}

Write one paragraph of 50 to 90 words that describes this driving scene as a video prompt.
The scene must show: {target}.
Describe every fact above consistently with that change, and end by saying the camera
viewpoint, road layout, and vehicle motion are preserved."""

Completion = Callable[[list[dict[str, str]]], str]


def humanize(name: str) -> str:
    """camelCase / snake_case attribute key -> lower-case words."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")
    return spaced.strip().lower()


def clean_answer(answer: Any) -> str:
    return str(answer).strip().rstrip(".").strip().lower()


def scene_facts(annotation: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Flatten an annotation into (attribute, question, answer) triples."""
    facts: list[tuple[str, str, str]] = []
    for attributes in annotation.values():
        if not isinstance(attributes, dict):
            continue
        for key, node in attributes.items():
            if isinstance(node, dict) and "answer" in node:
                facts.append((key, str(node.get("question", "")), clean_answer(node["answer"])))
    return facts


def target_phrase(attribute: str, value: str) -> str:
    return f"{humanize(attribute)} is {value}"


def strip_reasoning(text: str) -> str:
    """Remove reasoning blocks; an unterminated block means the answer was cut off."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<thought>.*?</thought>", "", text, flags=re.DOTALL)
    if "<think>" in text or "<thought>" in text:
        return ""
    return text.strip()


def normalize_paragraph(text: str) -> str:
    """Collapse model output into a single plain paragraph, or '' if unusable."""
    text = strip_reasoning(text)
    text = re.sub(r"^\s*(prompt|paragraph)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[*_`#>]+", "", text)
    text = " ".join(text.split()).strip(" \"'")
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        return ""
    if "preserve" not in text.lower():
        text = f"{text} {PRESERVE_SENTENCE}"
    return text


def template_paragraph(facts: list[tuple[str, str, str]], attribute: str, value: str) -> str:
    details = "; ".join(f"{humanize(key)}: {answer}" for key, _, answer in facts)
    target = target_phrase(attribute, value)
    return f"A driving scene where the {target}. Scene details - {details}. {PRESERVE_SENTENCE}"


class PromptWriter:
    """Writes one prompt paragraph per variation, preferring the LLM over the template."""

    def __init__(self, complete: Completion | None = None) -> None:
        self._complete = complete

    @classmethod
    def from_endpoint(cls, endpoint_url: str, model_name: str, api_key: str, timeout: float) -> PromptWriter:
        from openai import OpenAI

        client = OpenAI(base_url=endpoint_url.rstrip("/"), api_key=api_key, timeout=timeout)

        def complete(messages: list[dict[str, str]]) -> str:
            response = client.chat.completions.create(
                model=model_name, messages=messages, temperature=0.3, max_tokens=1024
            )
            return response.choices[0].message.content or ""

        return cls(complete)

    def write(self, annotation: dict[str, Any], attribute: str, value: str) -> tuple[str, str]:
        """Return (paragraph, source) where source is 'llm' or 'template'."""
        facts = scene_facts(annotation)
        if self._complete is not None:
            fact_lines = "\n".join(f"- {question or humanize(key)} -> {answer}" for key, question, answer in facts)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": USER_PROMPT.format(facts=fact_lines, target=target_phrase(attribute, value)),
                },
            ]
            try:
                paragraph = normalize_paragraph(self._complete(messages))
                if paragraph:
                    return paragraph, "llm"
                logger.warning("LLM returned no usable paragraph for %s=%s; using template", attribute, value)
            except Exception as exc:
                logger.warning(
                    "LLM prompt request failed for %s=%s (%s); using template",
                    attribute,
                    value,
                    exc,
                )
        return template_paragraph(facts, attribute, value), "template"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_generation_prompts(
    output_dir: Path, config: ODDCoverageConfig, writer: PromptWriter, model_name: str = ""
) -> dict[str, Any]:
    """Write a prompt for every (gap value, base video) in wfm_generation_input.json.

    Saves ``<scene>/<scene>_var_<category>_<attribute>_<value>_prompt.txt`` next to the
    variation files and an index, ``generation_prompts.json``, in ``output_dir``.
    """
    wfm = _load(output_dir / "wfm_generation_input.json")
    entries: list[dict[str, Any]] = []
    for category, attributes in (wfm.get("generation_gaps") or {}).items():
        for attribute, info in attributes.items():
            for value, spec in (info.get("values") or {}).items():
                videos = (spec.get("suitable_videos") or [])[: int(spec.get("required_count", 1))]
                for video in videos:
                    scene = video["scene_name"]
                    stem = f"{scene}_var_{category}_{attribute}_{value}"
                    variation = next(iter(sorted((output_dir / scene).glob(f"{stem}_run*.json"))), None)
                    if variation is not None:
                        annotation = _load(variation)
                    else:
                        # The solver only writes variation files for some scenes; derive this one.
                        base = output_dir / "Text_Annotations" / f"{scene}.json"
                        if not base.is_file():
                            logger.warning("No annotation for scene %s; skipping prompt", scene)
                            continue
                        annotation = modify_annotation_for_attribute(
                            _load(base), config.categories[category].attributes[attribute], value
                        )
                    paragraph, source = writer.write(annotation, attribute, value)
                    prompt_file = output_dir / scene / f"{stem}_prompt.txt"
                    prompt_file.parent.mkdir(parents=True, exist_ok=True)
                    prompt_file.write_text(paragraph + "\n", encoding="utf-8")
                    entries.append(
                        {
                            "category": category,
                            "attribute": attribute,
                            "value": value,
                            "scene_name": scene,
                            "video_path": video["video_path"],
                            "variation_file": (str(variation.relative_to(output_dir)) if variation else None),
                            "prompt_file": str(prompt_file.relative_to(output_dir)),
                            "source": source,
                            "prompt": paragraph,
                        }
                    )
    index = {"prompt_model": model_name, "prompts": entries}
    (output_dir / PROMPTS_FILENAME).write_text(json.dumps(index, indent=4), encoding="utf-8")
    return index
