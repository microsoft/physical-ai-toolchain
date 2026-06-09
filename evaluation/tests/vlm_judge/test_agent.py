"""Behavioral tests for the multi-step JudgeAgent.

A scripted backend replays canned responses so we can exercise the agent's
controller logic without GPUs or network.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from vlm_judge.agent import AgentConfig, JudgeAgent
from vlm_judge.backend import GenerationConfig, JudgeBackend

if TYPE_CHECKING:
    from PIL.Image import Image


class ScriptedBackend(JudgeBackend):
    """Backend that returns canned responses keyed by prompt fragment."""

    def __init__(self, name: str = "scripted") -> None:
        self.name = name
        self.calls: list[tuple[str, str]] = []
        self._handlers: dict[str, list[str]] = {}

    def queue(self, marker: str, response: str) -> None:
        self._handlers.setdefault(marker, []).append(response)

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: Sequence[Image],
        config: GenerationConfig,
    ) -> str:
        del images, config
        self.calls.append((system_prompt, user_prompt))
        for marker, queue in self._handlers.items():
            if marker in user_prompt and queue:
                return queue.pop(0)
        return ""


def _make_frames(n: int):
    from PIL import Image as PILImage

    return [PILImage.new("RGB", (32, 32), color=(i * 8 % 255, 0, 0)) for i in range(n)]


class TestJudgeAgent:
    def test_success_high_confidence_skips_milestones(self) -> None:
        backend = ScriptedBackend()
        # 3 outcome votes -> all SUCCESS -> confidence 1.0
        for _ in range(3):
            backend.queue("SUCCESSFULLY completed", "<answer>A</answer>")
        # Process: 8 ascending values
        backend.queue("RANDOM ORDER", "[0, 14, 28, 42, 57, 71, 85, 100]")

        agent = JudgeAgent(
            backend,
            config=AgentConfig(n_outcome_samples=3, milestone_threshold=0.9),
        )
        result = agent.judge(
            episode_id="ep0",
            instruction="pick orange",
            frames=_make_frames(8),
        )
        assert result.outcome_success is True
        assert result.outcome_confidence == 1.0
        assert result.milestones == []
        assert result.failure_mode is None

    def test_failure_runs_milestones_and_attribution(self) -> None:
        backend = ScriptedBackend()
        for _ in range(3):
            backend.queue("SUCCESSFULLY completed", "<answer>B</answer>")
        backend.queue("RANDOM ORDER", "[0, 5, 12, 20, 28, 33, 38, 40]")
        backend.queue(
            "Decompose the task",
            (
                '{"milestones": ['
                '{"name": "approach", "completed": true, "frame_range": "0-3", "evidence": "moves to object"},'
                '{"name": "grasp", "completed": false, "frame_range": "3-5", "evidence": "fingers close on air"}'
                "]}"
            ),
        )
        backend.queue("Failure modes", "<answer>A</answer>")

        agent = JudgeAgent(backend, config=AgentConfig(n_outcome_samples=3))
        result = agent.judge(
            episode_id="ep1",
            instruction="pick orange",
            frames=_make_frames(8),
        )
        assert result.outcome_success is False
        assert len(result.milestones) == 2
        assert result.milestones[1]["completed"] is False
        assert result.failure_mode == "missed_grasp"

    def test_low_confidence_success_runs_milestones_only(self) -> None:
        backend = ScriptedBackend()
        # 2/3 success -> confidence ~0.667 -> below 0.85 threshold
        backend.queue("SUCCESSFULLY completed", "<answer>A</answer>")
        backend.queue("SUCCESSFULLY completed", "<answer>A</answer>")
        backend.queue("SUCCESSFULLY completed", "<answer>B</answer>")
        backend.queue("RANDOM ORDER", "[0, 12, 24, 38, 50, 62, 75, 88]")
        backend.queue(
            "Decompose the task",
            '{"milestones": [{"name": "approach", "completed": true, "frame_range": "0-3", "evidence": "x"}]}',
        )

        agent = JudgeAgent(
            backend,
            config=AgentConfig(n_outcome_samples=3, milestone_threshold=0.85),
        )
        result = agent.judge(
            episode_id="ep2",
            instruction="pick",
            frames=_make_frames(8),
        )
        assert result.outcome_success is True
        assert 0.5 <= result.outcome_confidence <= 0.7
        assert len(result.milestones) == 1
        # Successful outcome -> no failure attribution
        assert result.failure_mode is None

    def test_format_violations_do_not_crash(self) -> None:
        backend = ScriptedBackend()
        for _ in range(3):
            backend.queue("SUCCESSFULLY completed", "<answer>B</answer>")
        backend.queue("RANDOM ORDER", "garbage not an array")
        backend.queue("Decompose the task", "not json")
        backend.queue("Failure modes", "no answer tag")

        agent = JudgeAgent(backend, config=AgentConfig(n_outcome_samples=3))
        result = agent.judge(
            episode_id="ep3",
            instruction="pick",
            frames=_make_frames(6),
        )
        assert result.outcome_success is False
        assert result.progress_per_frame == [0] * 6
        assert result.voc == 0.0
        assert result.milestones == []
        assert result.failure_mode is None
