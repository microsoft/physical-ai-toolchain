"""Judge CLI preview, dry-run, and episode-processing contracts."""

from __future__ import annotations

import builtins
import json
from collections.abc import Callable
from pathlib import Path

import pytest


def _arguments(root: Path) -> list[str]:
    return [
        "--dataset",
        str(root),
        "--output",
        str(root / "results.jsonl"),
        "--backend",
        "echo",
        "--cache-dir",
        "",
        "--n-frames",
        "3",
    ]


def test_given_inference_only_command_when_scoring_then_needs_no_curation(
    jsonl_dataset: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("physical_ai_curation"):
            raise AssertionError("Inference-only command imported curation")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    from evaluation.vlm_judge import run as inference

    assert inference.main([*_arguments(jsonl_dataset), "--indices", "0", "--force"]) == 0
    result = json.loads((jsonl_dataset / "results.jsonl").read_text())
    assert result["outcome_success"] is True
    assert not (jsonl_dataset.parent / ".curation").exists()
    with pytest.raises(SystemExit) as error:
        inference.main([*_arguments(jsonl_dataset), "--operation", "list"])
    assert error.value.code == 2


def test_given_an_episode_that_cannot_be_judged_when_scoring_then_fails_and_keeps_judged_verdicts(
    jsonl_dataset: Path, monkeypatch: pytest.MonkeyPatch, judge_service_stub: Callable[..., object]
) -> None:
    # Arrange
    from evaluation.vlm_judge import run as inference

    monkeypatch.setattr(inference, "build_service", lambda _args: judge_service_stub(fail_calls={2}))

    # Act
    result = inference.main([*_arguments(jsonl_dataset), "--indices", "0,1"])

    # Assert
    rows = [json.loads(line) for line in (jsonl_dataset / "results.jsonl").read_text().splitlines()]
    assert result == 1
    assert [row["episode_id"] for row in rows] == ["dataset/episode_000000"]


def test_given_a_complete_batch_with_a_failed_outcome_when_scoring_then_succeeds(
    jsonl_dataset: Path, monkeypatch: pytest.MonkeyPatch, judge_service_stub: Callable[..., object]
) -> None:
    # Arrange
    from evaluation.vlm_judge import run as inference

    monkeypatch.setattr(inference, "build_service", lambda _args: judge_service_stub(outcome=False))

    # Act
    result = inference.main([*_arguments(jsonl_dataset), "--indices", "0,1"])

    # Assert
    rows = [json.loads(line) for line in (jsonl_dataset / "results.jsonl").read_text().splitlines()]
    assert result == 0
    assert [row["outcome_success"] for row in rows] == [False, False]
