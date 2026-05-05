"""Agentic VLM-as-judge harness.

``JudgeAgent`` orchestrates a multi-step judgment chain over a single episode:

1. **Outcome MCQ** with N-sample self-consistency (always runs).
2. **GVL process reward** (always runs, independent of outcome).
3. **Milestone decomposition** with frame-range citations — runs when the
   outcome is uncertain (low confidence) or on failure, mitigating the
   visual-grounding hallucination biases reported in Behavior Critic
   (arXiv:2402.04210).
4. **Failure-mode attribution** — only when the outcome is FAILURE.

The agent reuses ``score_episode`` for steps 1-2 and adds dedicated
backend calls for steps 3-4. All steps target the same backend so the
harness migrates from local Qwen3-VL to vLLM/NIM/Azure OpenAI without
prompt or schema changes.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .backend import GenerationConfig, JudgeBackend
from .judge import JudgeResult, score_episode
from .prompts import (
    FAILURE_SYSTEM_PROMPT,
    MILESTONE_SYSTEM_PROMPT,
    PROMPT_VERSION,
    parse_failure_response,
    parse_milestone_response,
    render_failure_prompt,
    render_milestone_prompt,
)

if TYPE_CHECKING:
    from PIL.Image import Image

_LOGGER = logging.getLogger("evaluation.vlm_judge")


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Tuning knobs for ``JudgeAgent``."""

    n_outcome_samples: int = 3
    outcome_temperature: float = 0.6
    outcome_top_p: float = 0.95
    process_seed: int = 0
    milestone_max_tokens: int = 768
    failure_max_tokens: int = 256
    milestone_threshold: float = 0.85
    """Run milestone decomposition when outcome confidence is below this."""


class JudgeAgent:
    """Multi-step judgment agent built on top of a single ``JudgeBackend``."""

    def __init__(self, backend: JudgeBackend, *, config: AgentConfig | None = None) -> None:
        self._backend = backend
        self._config = config or AgentConfig()

    @property
    def backend(self) -> JudgeBackend:
        return self._backend

    @property
    def config(self) -> AgentConfig:
        return self._config

    def judge(
        self,
        *,
        episode_id: str,
        instruction: str,
        frames: Sequence[Image],
    ) -> JudgeResult:
        """Run the full multi-step judgment chain on ``frames``."""
        cfg = self._config
        result = score_episode(
            backend=self._backend,
            episode_id=episode_id,
            instruction=instruction,
            frames=frames,
            n_outcome_samples=cfg.n_outcome_samples,
            outcome_temperature=cfg.outcome_temperature,
            outcome_top_p=cfg.outcome_top_p,
            process_seed=cfg.process_seed,
        )
        result.prompt_version = PROMPT_VERSION

        if self._should_run_milestones(result):
            milestones = self._run_milestones(
                instruction=instruction,
                frames=frames,
                outcome_success=result.outcome_success,
            )
            result.milestones = milestones

        if result.outcome_success is False:
            failure_mode = self._run_failure_attribution(instruction=instruction, frames=frames)
            result.failure_mode = failure_mode

        return result

    # ------------------------------------------------------------------
    # Step controllers
    # ------------------------------------------------------------------

    def _should_run_milestones(self, result: JudgeResult) -> bool:
        cfg = self._config
        if result.outcome_success is None:
            return True
        if result.outcome_success is False:
            return True
        return result.outcome_confidence < cfg.milestone_threshold

    def _run_milestones(
        self,
        *,
        instruction: str,
        frames: Sequence[Image],
        outcome_success: bool | None,
    ) -> list[dict[str, Any]]:
        prompt = render_milestone_prompt(
            instruction=instruction,
            n_frames=len(frames),
            outcome_success=outcome_success,
        )
        text = self._backend.generate(
            system_prompt=MILESTONE_SYSTEM_PROMPT,
            user_prompt=prompt,
            images=frames,
            config=GenerationConfig(
                max_new_tokens=self._config.milestone_max_tokens,
                temperature=0.0,
            ),
        )
        milestones = parse_milestone_response(text)
        if milestones is None:
            _LOGGER.warning(
                "Milestone decomposition format violation; truncated response: %r",
                (text or "")[:160],
            )
            return []
        return milestones

    def _run_failure_attribution(
        self,
        *,
        instruction: str,
        frames: Sequence[Image],
    ) -> str | None:
        prompt = render_failure_prompt(instruction=instruction)
        text = self._backend.generate(
            system_prompt=FAILURE_SYSTEM_PROMPT,
            user_prompt=prompt,
            images=frames,
            config=GenerationConfig(
                max_new_tokens=self._config.failure_max_tokens,
                temperature=0.0,
            ),
        )
        label = parse_failure_response(text)
        if label is None:
            _LOGGER.warning(
                "Failure-mode format violation; truncated response: %r",
                (text or "")[:160],
            )
        return label
