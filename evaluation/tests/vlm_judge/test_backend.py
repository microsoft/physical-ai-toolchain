"""Security contracts for native Qwen model selection."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_mock import MockerFixture
from vlm_judge.backend import Qwen3VLBackend
from vlm_judge.service import BackendConfig, JudgeService


@pytest.fixture
def forbid_model_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if rejected model selections reach heavyweight imports."""
    original_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".")[0] in {"torch", "transformers"}:
            pytest.fail(f"Model policy reached heavyweight import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


@pytest.mark.usefixtures("forbid_model_imports")
@pytest.mark.parametrize("revision", [None, "", " ", "main", "v1.0", "a" * 39, "g" * 40, "a" * 41])
@pytest.mark.parametrize("via_service", [False, True], ids=["constructor", "service"])
def test_given_invalid_hub_revision_when_loading_then_rejects_before_imports(
    revision: str | None,
    via_service: bool,
) -> None:
    with pytest.raises(ValueError, match="revision"):
        if via_service:
            JudgeService._build_backend(BackendConfig(revision=revision))
        else:
            Qwen3VLBackend(revision=revision)


@pytest.mark.usefixtures("forbid_model_imports")
def test_given_remote_code_enabled_when_loading_then_rejects_before_imports() -> None:
    with pytest.raises(ValueError, match="trust_remote_code"):
        Qwen3VLBackend(revision="a" * 40, trust_remote_code=True)


@pytest.mark.parametrize("source", ["hub", "absolute", "./model", "../model"])
@pytest.mark.parametrize("successful_candidate", range(4))
def test_given_native_model_when_loading_then_all_candidates_receive_safe_flags(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: str,
    successful_candidate: int,
) -> None:
    processor = mocker.Mock()
    candidates = [mocker.Mock() for _ in range(4)]
    for candidate in candidates[:successful_candidate]:
        candidate.from_pretrained.side_effect = RuntimeError("Unsupported model class")
    transformers = SimpleNamespace(
        AutoProcessor=processor,
        Qwen3VLForConditionalGeneration=candidates[0],
        Qwen3VLMoeForConditionalGeneration=candidates[1],
        AutoModelForImageTextToText=candidates[2],
        AutoModelForVision2Seq=candidates[3],
    )
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(bfloat16="fake-dtype"))
    local = source != "hub"
    directory = tmp_path / "model"
    directory.mkdir()
    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working if source == "../model" else tmp_path)
    model_id = str(directory) if source == "absolute" else source if local else "owner/model"
    revision = None if local else "AB" * 20
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    Qwen3VLBackend(model_id=model_id, revision=revision)

    expected = {"revision": None if local else "ab" * 20, "trust_remote_code": False, "local_files_only": local}
    expected_id = str(directory) if local else model_id
    processor.from_pretrained.assert_called_once_with(expected_id, **expected)
    for candidate in candidates[: successful_candidate + 1]:
        candidate.from_pretrained.assert_called_once_with(
            expected_id,
            **expected,
            dtype="fake-dtype",
            device_map="auto",
            attn_implementation="sdpa",
        )
    candidates[successful_candidate].from_pretrained.return_value.eval.assert_called_once_with()


@pytest.mark.usefixtures("forbid_model_imports")
@pytest.mark.parametrize("syntax", ["absolute", "./missing", "../missing"])
def test_given_missing_explicit_directory_when_loading_then_never_falls_back_to_hub(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    syntax: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    model_id = str(tmp_path / "missing") if syntax == "absolute" else syntax

    with pytest.raises(ValueError, match="directory"):
        Qwen3VLBackend(model_id=model_id, revision="a" * 40)


@pytest.mark.usefixtures("forbid_model_imports")
def test_given_local_directory_and_revision_when_loading_then_rejects_unverified_pin(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="revision"):
        Qwen3VLBackend(model_id=str(tmp_path), revision="a" * 40)


@pytest.mark.usefixtures("forbid_model_imports")
@pytest.mark.parametrize("revision", [None, "a" * 40])
def test_given_matching_working_directory_when_loading_hub_id_then_rejects_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    revision: str | None,
) -> None:
    (tmp_path / "owner" / "model").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match=r"revision|explicit"):
        Qwen3VLBackend(model_id="owner/model", revision=revision)


@pytest.mark.usefixtures("forbid_model_imports")
def test_given_unpinned_shim_when_starting_then_inherits_loader_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from vlm_judge.openai_shim import main

    monkeypatch.delenv("VLM_SHIM_MODEL_REVISION", raising=False)

    with pytest.raises(ValueError, match="revision"):
        main(["--model-id", "owner/model"])
