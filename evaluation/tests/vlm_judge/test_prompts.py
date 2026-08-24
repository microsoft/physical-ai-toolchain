"""Behavioral tests for prompt parsers."""

from __future__ import annotations

from vlm_judge.prompts import (
    FAILURE_MODES,
    parse_failure_response,
    parse_milestone_response,
    parse_outcome_response,
    parse_process_response,
    render_chronological_process_prompt,
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

    def test_pads_short_array_to_length(self) -> None:
        # Open models often emit n_frames-1 values; pad with the last value.
        assert parse_process_response("[0, 50, 100]", n_frames=5) == [0, 50, 100, 100, 100]

    def test_truncates_long_array_to_length(self) -> None:
        assert parse_process_response("[0, 10, 20, 30, 40]", n_frames=3) == [0, 10, 20]

    def test_strips_think_block(self) -> None:
        text = "<think>frame 8 looks done, 12 total</think>[0, 0, 50, 100]"
        assert parse_process_response(text, n_frames=4) == [0, 0, 50, 100]

    def test_recovers_from_code_fence(self) -> None:
        assert parse_process_response("```json\n[0, 33, 66, 100]\n```", n_frames=4) == [0, 33, 66, 100]

    def test_falls_back_to_bare_integers(self) -> None:
        assert parse_process_response("progress: 0, 50, 100", n_frames=3) == [0, 50, 100]

    def test_clamps_out_of_range(self) -> None:
        assert parse_process_response("[-10, 50, 250]", n_frames=3) == [0, 50, 100]

    def test_rounds_floats(self) -> None:
        assert parse_process_response("[0.0, 49.4, 100.0]", n_frames=3) == [0, 49, 100]

    def test_returns_none_without_numbers(self) -> None:
        assert parse_process_response('["a", "b"]', n_frames=2) is None
        assert parse_process_response("no numbers here", n_frames=3) is None
        assert parse_process_response("", n_frames=3) is None


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

    def test_chronological_process_prompt_is_in_order(self) -> None:
        text = render_chronological_process_prompt(instruction="task", n_frames=10)
        assert "CHRONOLOGICAL" in text
        assert "0-100" in text
        assert "RANDOM ORDER" not in text

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
