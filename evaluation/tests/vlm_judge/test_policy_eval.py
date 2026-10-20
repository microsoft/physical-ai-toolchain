"""Real-media tests for policy-rollout discovery and echo scoring."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from vlm_judge import policy_eval


def _arguments(root: Path, instruction: str = "Pick the block") -> list[str]:
    return [
        "--rollout-root",
        str(root),
        "--instruction",
        instruction,
        "--output",
        str(root / "results" / "judgments.jsonl"),
        "--backend",
        "echo",
        "--cache-dir",
        "",
        "--n-frames",
        "3",
    ]


def test_given_overlapping_roots_when_discovering_then_deduplicates_and_applies_overrides(
    rollout_video: Path, tmp_path: Path
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    second = nested / "second.mp4"
    second.write_bytes(rollout_video.read_bytes())
    overrides = tmp_path / "instructions.json"
    overrides.write_text(json.dumps({"nested/second": "Place the block"}))

    rollouts = list(
        policy_eval.discover_rollouts(
            [tmp_path, nested, tmp_path / "missing"],
            instruction="Pick the block",
            instructions_file=overrides,
        )
    )

    assert [(rollout.episode_id, rollout.instruction) for rollout in rollouts] == [
        ("nested/second", "Place the block"),
        ("rollout", "Pick the block"),
    ]


def test_given_missing_overrides_when_discovering_then_uses_default(rollout_video: Path, tmp_path: Path) -> None:
    rollouts = list(
        policy_eval.discover_rollouts(
            [tmp_path],
            instruction="Pick",
            instructions_file=tmp_path / "missing.json",
        )
    )

    assert [(rollout.video_path, rollout.instruction) for rollout in rollouts] == [(rollout_video, "Pick")]


@pytest.mark.parametrize("payload", [[], "instruction", 5])
def test_given_non_object_overrides_when_discovering_then_rejects_payload(tmp_path: Path, payload: object) -> None:
    overrides = tmp_path / "instructions.json"
    overrides.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="must be a JSON object"):
        list(policy_eval.discover_rollouts([tmp_path], instruction="Pick", instructions_file=overrides))


def test_given_config_preview_when_running_then_prints_config_without_scoring(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = policy_eval.main([*_arguments(tmp_path, ""), "--config-preview"])

    configuration = json.loads(capsys.readouterr().out)
    assert result == 0
    assert configuration["backend"] == "echo"
    assert "config_preview" not in configuration
    assert not (tmp_path / "results").exists()


def test_given_blank_instruction_when_running_then_rejects_before_creating_output(tmp_path: Path) -> None:
    assert policy_eval.main(_arguments(tmp_path, "  ")) == 2
    assert not (tmp_path / "results").exists()


def test_given_no_rollouts_when_running_then_reports_failure(tmp_path: Path) -> None:
    assert policy_eval.main(_arguments(tmp_path)) == 1
    assert not (tmp_path / "results/judgments.jsonl").exists()


@pytest.mark.parametrize("dry_run", [False, True], ids=["echo-scoring", "discovery-only"])
def test_given_valid_rollout_when_running_then_writes_selected_result(
    rollout_video: Path, tmp_path: Path, dry_run: bool
) -> None:
    (tmp_path / "second.mp4").write_bytes(rollout_video.read_bytes())
    arguments = [*_arguments(tmp_path), "--limit", "1", "--force"]
    if dry_run:
        arguments.append("--dry-run")

    result = policy_eval.main(arguments)

    rows = [json.loads(line) for line in (tmp_path / "results/judgments.jsonl").read_text().splitlines()]
    assert result == 0
    assert len(rows) == 1
    assert rows[0]["episode_id"] == "rollout"
    assert rows[0]["instruction"] == "Pick the block"
    assert rows[0]["video_path"] == str(rollout_video)
    if dry_run:
        assert rows[0]["dry_run"] is True
    else:
        assert rows[0]["judge_model"] == "echo"
        assert rows[0]["outcome_success"] is True
        assert len(rows[0]["progress_per_frame"]) == 3


def test_given_corrupt_and_valid_rollouts_when_running_then_continues_after_failure(
    rollout_video: Path, tmp_path: Path
) -> None:
    (tmp_path / "000-corrupt.mp4").write_bytes(b"not an MP4")

    result = policy_eval.main(_arguments(tmp_path))

    rows = [json.loads(line) for line in (tmp_path / "results/judgments.jsonl").read_text().splitlines()]
    assert result == 0
    assert [row["episode_id"] for row in rows] == ["rollout"]


def test_given_only_corrupt_rollout_when_running_then_returns_failure(tmp_path: Path) -> None:
    (tmp_path / "corrupt.mp4").write_bytes(b"not an MP4")

    result = policy_eval.main(_arguments(tmp_path))

    assert result == 1
    assert (tmp_path / "results/judgments.jsonl").read_text() == ""


def test_given_sidecar_instruction_when_running_then_scores_without_default(
    rollout_video: Path, tmp_path: Path
) -> None:
    overrides = tmp_path / "instructions.json"
    overrides.write_text(json.dumps({"rollout": "Place the block"}))

    result = policy_eval.main([*_arguments(tmp_path, ""), "--instructions-file", str(overrides)])

    row = json.loads((tmp_path / "results/judgments.jsonl").read_text())
    assert result == 0
    assert row["instruction"] == "Place the block"


def test_given_require_all_when_a_rollout_fails_then_returns_failure_and_keeps_judged_verdicts(
    rollout_video: Path, tmp_path: Path
) -> None:
    # Arrange
    (tmp_path / "000-corrupt.mp4").write_bytes(b"not an MP4")

    # Act
    result = policy_eval.main([*_arguments(tmp_path), "--require-all"])

    # Assert
    rows = [json.loads(line) for line in (tmp_path / "results/judgments.jsonl").read_text().splitlines()]
    assert result == 1
    assert [row["episode_id"] for row in rows] == ["rollout"]


def test_given_require_all_when_limit_leaves_out_a_failing_rollout_then_succeeds(
    rollout_video: Path, tmp_path: Path
) -> None:
    # Arrange
    (tmp_path / "zz-corrupt.mp4").write_bytes(b"not an MP4")

    # Act
    result = policy_eval.main([*_arguments(tmp_path), "--require-all", "--limit", "1"])

    # Assert
    rows = [json.loads(line) for line in (tmp_path / "results/judgments.jsonl").read_text().splitlines()]
    assert result == 0
    assert [row["episode_id"] for row in rows] == ["rollout"]


def test_given_require_all_when_complete_batch_has_a_failed_outcome_then_succeeds(
    rollout_video: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    judge_service_stub: Callable[..., object],
) -> None:
    # Arrange
    monkeypatch.setattr(policy_eval, "_build_service", lambda _args: judge_service_stub(outcome=False))

    # Act
    result = policy_eval.main([*_arguments(tmp_path), "--require-all"])

    # Assert
    row = json.loads((tmp_path / "results/judgments.jsonl").read_text())
    assert result == 0
    assert row["outcome_success"] is False
