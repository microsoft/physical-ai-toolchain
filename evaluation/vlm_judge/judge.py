"""Outcome + process scoring for the VLM judge."""

from __future__ import annotations

import logging
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import MISSING, asdict, dataclass, field, fields
from typing import TYPE_CHECKING, Any

from .backend import GenerationConfig, JudgeBackend
from .prompts import (
    OUTCOME_SYSTEM_PROMPT,
    PROCESS_SYSTEM_PROMPT,
    PROMPT_VERSION,
    parse_outcome_response,
    parse_process_response,
    render_chronological_process_prompt,
    render_outcome_prompt,
    render_process_prompt,
    shuffle_with_anchor,
)

if TYPE_CHECKING:
    from PIL.Image import Image

_LOGGER = logging.getLogger("evaluation.vlm_judge")


@dataclass(slots=True)
class JudgeResult:
    """Composite judgment for a single episode.

    ``milestones`` and ``failure_mode`` are populated by the agentic harness
    (``JudgeAgent``) and remain empty / ``None`` for raw ``score_episode``
    invocations.
    """

    episode_id: str
    instruction: str
    judge_model: str
    prompt_version: str
    n_frames: int
    outcome_success: bool | None
    outcome_confidence: float
    outcome_n_valid_votes: int
    progress_per_frame: list[int]
    voc: float
    milestones: list[dict[str, Any]] = field(default_factory=list)
    failure_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> JudgeResult:
        """Validate persisted or inferred evidence without coercing model output."""
        required = {item.name for item in fields(cls) if item.default is MISSING and item.default_factory is MISSING}
        if not isinstance(payload, Mapping) or not required.issubset(payload):
            raise ValueError("Judge result is missing required fields")
        for name in ("episode_id", "instruction", "judge_model", "prompt_version"):
            if not isinstance(payload[name], str) or not payload[name].strip():
                raise ValueError(f"Invalid judge result field: {name}")
        for name, minimum in (("n_frames", 2), ("outcome_n_valid_votes", 0)):
            if type(payload[name]) is not int or payload[name] < minimum:
                raise ValueError(f"Invalid judge result field: {name}")
        for name, minimum, maximum in (("outcome_confidence", 0, 1), ("voc", -1, 1)):
            value = payload[name]
            if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"Invalid judge result field: {name}")
        if payload["outcome_success"] is not None and type(payload["outcome_success"]) is not bool:
            raise ValueError("Invalid judge result outcome_success")
        progress = payload["progress_per_frame"]
        if not isinstance(progress, list) or any(type(value) is not int or not 0 <= value <= 100 for value in progress):
            raise ValueError("Invalid judge result progress_per_frame")
        if progress and len(progress) != payload["n_frames"]:
            raise ValueError("Judge result progress does not match frame count")
        milestones = payload.get("milestones", [])
        if not isinstance(milestones, list):
            raise ValueError("Invalid judge result milestones")
        for milestone in milestones:
            if (
                not isinstance(milestone, dict)
                or not all(isinstance(milestone.get(name), str) for name in ("name", "frame_range", "evidence"))
                or not milestone["name"].strip()
                or type(milestone.get("completed")) is not bool
            ):
                raise ValueError("Invalid judge result milestone")
        if payload.get("failure_mode") is not None and not isinstance(payload["failure_mode"], str):
            raise ValueError("Invalid judge result failure_mode")
        return cls(**{item.name: payload[item.name] for item in fields(cls) if item.name in payload})


def score_episode(
    *,
    backend: JudgeBackend,
    episode_id: str,
    instruction: str,
    frames: Sequence[Image],
    n_outcome_samples: int = 5,
    outcome_temperature: float = 0.6,
    outcome_top_p: float = 0.95,
    process_seed: int = 0,
    process_method: str = "gvl",
) -> JudgeResult:
    """Run outcome + process scoring for a single episode."""
    n_frames = len(frames)
    if n_frames < 2:
        raise ValueError(f"At least 2 frames required, got {n_frames}")

    outcome = _run_outcome(
        backend=backend,
        instruction=instruction,
        frames=frames,
        n_samples=n_outcome_samples,
        temperature=outcome_temperature,
        top_p=outcome_top_p,
    )
    process = _run_process(
        backend=backend,
        instruction=instruction,
        frames=frames,
        rng=random.Random(process_seed),
        method=process_method,
    )

    return JudgeResult(
        episode_id=episode_id,
        instruction=instruction,
        judge_model=backend.name,
        prompt_version=PROMPT_VERSION,
        n_frames=n_frames,
        outcome_success=outcome["success"],
        outcome_confidence=float(outcome["confidence"]),
        outcome_n_valid_votes=int(outcome["n_valid_votes"]),
        progress_per_frame=list(process["progress_per_frame"]),
        voc=float(process["voc"]),
    )


# -------------------------------------------------------------------------
# Outcome MCQ with N-sample self-consistency voting
# -------------------------------------------------------------------------


def _run_outcome(
    *,
    backend: JudgeBackend,
    instruction: str,
    frames: Sequence[Image],
    n_samples: int,
    temperature: float,
    top_p: float,
) -> dict[str, Any]:
    user_prompt = render_outcome_prompt(instruction=instruction, n_frames=len(frames))
    config = GenerationConfig(
        max_new_tokens=384,
        temperature=temperature,
        top_p=top_p,
    )
    votes: list[bool] = []
    for i in range(max(1, n_samples)):
        text = backend.generate(
            system_prompt=OUTCOME_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            images=frames,
            config=config,
        )
        decision = parse_outcome_response(text)
        if decision is None:
            _LOGGER.warning("Judge outcome format violation sample=%d", int(i))
            continue
        votes.append(decision)

    if not votes:
        raise ValueError("Judge outcome has no valid decisions")

    success_rate = sum(votes) / len(votes)
    if sum(votes) * 2 == len(votes):
        return {"success": None, "confidence": 0.5, "n_valid_votes": len(votes)}
    success = success_rate >= 0.5
    confidence = success_rate if success else 1.0 - success_rate
    return {
        "success": bool(success),
        "confidence": confidence,
        "n_valid_votes": len(votes),
    }


# -------------------------------------------------------------------------
# Process — GVL shuffle-and-rank
# -------------------------------------------------------------------------


def _run_process(
    *,
    backend: JudgeBackend,
    instruction: str,
    frames: Sequence[Image],
    rng: random.Random,
    method: str = "gvl",
) -> dict[str, Any]:
    n = len(frames)
    config = GenerationConfig(max_new_tokens=512, temperature=0.0)

    if method == "chronological":
        # Frames shown in true order; the model rates each in place (no un-shuffle).
        text = backend.generate(
            system_prompt=PROCESS_SYSTEM_PROMPT,
            user_prompt=render_chronological_process_prompt(instruction=instruction, n_frames=n),
            images=list(frames),
            config=config,
        )
        values = parse_process_response(text, n_frames=n)
        if values is None:
            _LOGGER.warning("Judge process format violation")
            raise ValueError("Invalid judge process result")
        return {"progress_per_frame": list(values), "voc": value_order_correlation(values)}

    order = shuffle_with_anchor(n, rng=rng)
    shuffled_frames = [frames[i] for i in order]
    text = backend.generate(
        system_prompt=PROCESS_SYSTEM_PROMPT,
        user_prompt=render_process_prompt(instruction=instruction, n_frames=n),
        images=shuffled_frames,
        config=config,
    )
    shuffled_values = parse_process_response(text, n_frames=n)
    if shuffled_values is None:
        _LOGGER.warning("Judge process format violation")
        raise ValueError("Invalid judge process result")

    chronological = [0] * n
    for shuffled_idx, original_idx in enumerate(order):
        chronological[original_idx] = shuffled_values[shuffled_idx]
    return {"progress_per_frame": chronological, "voc": value_order_correlation(chronological)}


def value_order_correlation(values: Sequence[int]) -> float:
    """Spearman rank correlation between predicted progress and chronological order."""
    n = len(values)
    if n < 2:
        return 0.0
    if len(set(values)) < 2:
        return 0.0
    ranks_by_value = sorted(range(n), key=lambda i: values[i])
    rank_of = [0] * n
    for r, i in enumerate(ranks_by_value):
        rank_of[i] = r
    d2 = sum((rank_of[i] - i) ** 2 for i in range(n))
    return 1.0 - (6.0 * d2) / (n * (n * n - 1))
