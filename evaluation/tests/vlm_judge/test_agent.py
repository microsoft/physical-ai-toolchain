"""Behavioral tests for the multi-step JudgeAgent.

A scripted backend replays canned responses so we can exercise the agent's
controller logic without GPUs or network.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from typing import TYPE_CHECKING

import pytest

from evaluation.vlm_judge.agent import AgentConfig, JudgeAgent
from evaluation.vlm_judge.backend import GenerationConfig, JudgeBackend
from evaluation.vlm_judge.judge import _run_process
from evaluation.vlm_judge.prompts import shuffle_with_anchor

if TYPE_CHECKING:
    from PIL.Image import Image
    from pytest_mock import MockerFixture


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

    def test_chronological_process_method_uses_in_order_prompt(self) -> None:
        backend = ScriptedBackend()
        for _ in range(3):
            backend.queue("SUCCESSFULLY completed", "<answer>A</answer>")
        backend.queue("CHRONOLOGICAL", "[0, 20, 40, 60, 80, 100, 100, 100]")

        agent = JudgeAgent(
            backend,
            config=AgentConfig(n_outcome_samples=3, process_method="chronological"),
        )
        result = agent.judge(episode_id="epc", instruction="pick", frames=_make_frames(8))
        # Chronological method maps values directly (no shuffle/un-shuffle).
        assert result.progress_per_frame == [0, 20, 40, 60, 80, 100, 100, 100]
        assert any("CHRONOLOGICAL" in up for _, up in backend.calls)
        assert not any("RANDOM ORDER" in up for _, up in backend.calls)

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

    def test_format_violations_are_execution_errors(self) -> None:
        import pytest

        backend = ScriptedBackend()
        for _ in range(3):
            backend.queue("SUCCESSFULLY completed", "<answer>B</answer>")
        backend.queue("RANDOM ORDER", "garbage not an array")
        backend.queue("Decompose the task", "not json")
        backend.queue("Failure modes", "no answer tag")

        agent = JudgeAgent(backend, config=AgentConfig(n_outcome_samples=3))
        with pytest.raises(ValueError, match="process result"):
            agent.judge(episode_id="ep3", instruction="pick", frames=_make_frames(6))


class TestProcessFormatRepair:
    @pytest.mark.parametrize("method", ["gvl", "chronological"])
    def test_given_short_response_when_repairing_then_reuses_frame_order_and_strict_scores(
        self, method: str, mocker: MockerFixture
    ) -> None:
        backend = ScriptedBackend()
        backend.queue("TASK INSTRUCTION", "[0, 23, 36, 0, 18, 55, 45, 82, 12, 64, 100]")
        scores = list(range(12))
        backend.queue("TASK INSTRUCTION", json.dumps(scores))
        generate = mocker.spy(backend, "generate")
        frames = _make_frames(12)

        result = _run_process(backend=backend, instruction="pick", frames=frames, rng=random.Random(0), method=method)

        assert generate.call_count == 2
        first, repair = [call.kwargs for call in generate.call_args_list]
        assert all(before is after for before, after in zip(first["images"], repair["images"], strict=True))
        assert first["config"] == repair["config"]
        assert "exactly 12 integers" in repair["user_prompt"]
        assert "1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12" in repair["user_prompt"]
        order = shuffle_with_anchor(12, rng=random.Random(0)) if method == "gvl" else list(range(12))
        assert result["progress_per_frame"] == [scores[order.index(index)] for index in range(12)]

    @pytest.mark.parametrize("method", ["gvl", "chronological"])
    def test_given_valid_response_when_scoring_then_does_not_retry(self, method: str) -> None:
        backend = ScriptedBackend()
        backend.queue("TASK INSTRUCTION", "[0, 30, 60, 100]")

        _run_process(backend=backend, instruction="pick", frames=_make_frames(4), rng=random.Random(0), method=method)

        assert len(backend.calls) == 1

    @pytest.mark.parametrize("method", ["gvl", "chronological"])
    @pytest.mark.parametrize(
        "invalid", ["[0, 20, 100]", "[0, 20, 50, 100, 100]", "[0, 20.0, 50, 100]", "[0, 20, 50, 101]"]
    )
    def test_given_invalid_repair_when_scoring_then_fails_after_two_attempts(
        self, method: str, invalid: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        backend = ScriptedBackend()
        backend.queue("TASK INSTRUCTION", invalid)
        backend.queue("TASK INSTRUCTION", invalid)
        backend.queue("TASK INSTRUCTION", "[0, 30, 60, 100]")

        with pytest.raises(ValueError, match="Invalid judge process result"):
            _run_process(
                backend=backend, instruction="pick", frames=_make_frames(4), rng=random.Random(0), method=method
            )

        assert len(backend.calls) == 2
        assert "retry" in caplog.text.lower()
        assert invalid not in caplog.text

    @pytest.mark.parametrize("method", ["gvl", "chronological"])
    def test_given_backend_exception_when_scoring_then_does_not_retry(self, method: str, mocker: MockerFixture) -> None:
        backend = ScriptedBackend()
        generate = mocker.patch.object(backend, "generate", side_effect=RuntimeError("provider unavailable"))

        with pytest.raises(RuntimeError, match="provider unavailable"):
            _run_process(
                backend=backend, instruction="pick", frames=_make_frames(4), rng=random.Random(0), method=method
            )

        assert generate.call_count == 1
