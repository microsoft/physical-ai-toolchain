"""Behavioral tests for prompt parsers."""

from __future__ import annotations

from vlm_judge.prompts import (
    FAILURE_MODES,
    parse_failure_response,
    parse_milestone_response,
    parse_outcome_response,
    parse_process_response,
    render_failure_prompt,
    render_milestone_prompt,
    render_outcome_prompt,
    render_process_prompt,
    shuffle_with_anchor,
)


class TestOutcomeParser:
    def test_parses_success(self) -> None:
        assert parse_outcome_response("<think>ok</think><answer>A</answer>") is True

    def test_parses_failure(self) -> None:
        assert parse_outcome_response("<answer>B</answer> trailing text") is False

    def test_returns_none_on_format_violation(self) -> None:
        assert parse_outcome_response("just some prose") is None
        assert parse_outcome_response("") is None
        assert parse_outcome_response("<answer>Z</answer>") is None


class TestProcessParser:
    def test_parses_array(self) -> None:
        out = parse_process_response("[0, 25, 50, 75, 100]", n_frames=5)
        assert out == [0, 25, 50, 75, 100]

    def test_extracts_array_from_prose(self) -> None:
        text = "Some thinking [10, 20, 30, 40] and trailing text"
        assert parse_process_response(text, n_frames=4) == [10, 20, 30, 40]

    def test_rejects_wrong_length(self) -> None:
        assert parse_process_response("[1, 2, 3]", n_frames=5) is None

    def test_rejects_non_numeric(self) -> None:
        assert parse_process_response('["a", "b"]', n_frames=2) is None

    def test_rounds_floats(self) -> None:
        assert parse_process_response("[0.0, 49.5, 100.0]", n_frames=3) == [0, 50, 100]


class TestMilestoneParser:
    def test_parses_typical_payload(self) -> None:
        text = (
            '{"milestones": ['
            '{"name": "approach", "completed": true, "frame_range": "0-3", "evidence": "moves toward object"},'
            '{"name": "grasp", "completed": false, "frame_range": "3-5", "evidence": "fingers close on air"}'
            "]}"
        )
        out = parse_milestone_response(text)
        assert out is not None
        assert len(out) == 2
        assert out[0]["completed"] is True
        assert out[1]["frame_range"] == "3-5"

    def test_rejects_invalid_json(self) -> None:
        assert parse_milestone_response("not json") is None

    def test_rejects_missing_milestones(self) -> None:
        assert parse_milestone_response('{"steps": []}') is None


class TestFailureParser:
    def test_parses_letter(self) -> None:
        assert parse_failure_response("<answer>A</answer>") == "missed_grasp"
        assert parse_failure_response("<answer>g</answer>") == "other"

    def test_invalid_letter(self) -> None:
        assert parse_failure_response("<answer>Z</answer>") is None
        assert parse_failure_response("nothing here") is None


class TestShuffleAnchor:
    def test_first_index_is_anchored(self) -> None:
        order = shuffle_with_anchor(10)
        assert order[0] == 0
        assert sorted(order) == list(range(10))

    def test_deterministic_with_seed(self) -> None:
        import random

        a = shuffle_with_anchor(8, rng=random.Random(7))
        b = shuffle_with_anchor(8, rng=random.Random(7))
        assert a == b


class TestPromptRendering:
    def test_outcome_prompt_includes_instruction(self) -> None:
        text = render_outcome_prompt(instruction="Pick up cube", n_frames=5)
        assert "Pick up cube" in text
        assert "5 frames" in text

    def test_process_prompt_mentions_random_order(self) -> None:
        text = render_process_prompt(instruction="task", n_frames=10)
        assert "RANDOM ORDER" in text
        assert "0-100" in text

    def test_milestone_prompt_passes_outcome_label(self) -> None:
        success = render_milestone_prompt(
            instruction="t",
            n_frames=5,
            outcome_success=True,
        )
        failure = render_milestone_prompt(
            instruction="t",
            n_frames=5,
            outcome_success=False,
        )
        assert "SUCCESS" in success
        assert "FAILURE" in failure

    def test_failure_prompt_lists_modes(self) -> None:
        text = render_failure_prompt(instruction="t")
        for letter, label in FAILURE_MODES:
            assert f"{letter}) {label}" in text
