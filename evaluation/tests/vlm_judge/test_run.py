"""Judge CLI preview, dry-run, and episode-processing contracts."""

from __future__ import annotations

import builtins
import json
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
    from vlm_judge import run as inference

    assert inference.main([*_arguments(jsonl_dataset), "--indices", "0", "--force"]) == 0
    result = json.loads((jsonl_dataset / "results.jsonl").read_text())
    assert result["outcome_success"] is True
    assert not (jsonl_dataset.parent / ".curation").exists()
    with pytest.raises(SystemExit) as error:
        inference.main([*_arguments(jsonl_dataset), "--operation", "list"])
    assert error.value.code == 2
