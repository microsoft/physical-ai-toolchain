"""CPU generation and security contracts for the model integration boundary."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import torch
from PIL import Image
from pytest_mock import MockerFixture
from vlm_judge.backend import GenerationConfig, OpenAICompatibleBackend, Qwen3VLBackend, _load_qwen3_vl_model
from vlm_judge.service import BackendConfig, JudgeService


@pytest.mark.parametrize("temperature", [0.0, 0.4])
@pytest.mark.parametrize("with_image", [False, True])
@pytest.mark.parametrize("decoded", [["generated answer"], []], ids=["text", "empty-decoding"])
def test_given_cpu_model_boundary_when_generating_then_preserves_options_and_decodes_only_new_tokens(
    monkeypatch: pytest.MonkeyPatch, temperature: float, with_image: bool, decoded: list[str]
) -> None:
    calls: dict[str, Any] = {}

    class Processor:
        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs: Any) -> Processor:
            calls["processor_load"] = (model_id, kwargs)
            return cls()

        def apply_chat_template(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
            calls["messages"] = messages
            calls["template_options"] = kwargs
            return "formatted prompt"

        def __call__(self, **kwargs: Any) -> dict[str, torch.Tensor]:
            calls["processor_input"] = kwargs
            return {"input_ids": torch.tensor([[1, 2, 3]])}

        def batch_decode(self, tokens: torch.Tensor, **kwargs: Any) -> list[str]:
            calls["decoded_tokens"] = tokens.tolist()
            calls["decode_options"] = kwargs
            return decoded

    class Model:
        device = torch.device("cpu")

        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs: Any) -> Model:
            calls["model_load"] = (model_id, kwargs)
            return cls()

        def eval(self) -> None:
            calls["eval"] = True

        def generate(self, **kwargs: Any) -> torch.Tensor:
            calls["generation"] = kwargs
            calls["inference_mode"] = torch.is_inference_mode_enabled()
            return torch.tensor([[1, 2, 3, 4, 5]])

    transformers = ModuleType("transformers")
    transformers.AutoProcessor = Processor
    transformers.Qwen3VLForConditionalGeneration = Model
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    image = Image.new("RGB", (8, 8), "red")
    images = [image] if with_image else []
    backend = Qwen3VLBackend(model_id="synthetic-model", revision="a" * 40, device_map="cpu")

    answer = backend.generate(
        system_prompt="Judge the task",
        user_prompt="Pick",
        images=images,
        config=GenerationConfig(max_new_tokens=7, temperature=temperature, top_p=0.8),
    )

    assert answer == (decoded[0] if decoded else "")
    assert calls["processor_load"] == (
        "synthetic-model",
        {"revision": "a" * 40, "trust_remote_code": False, "local_files_only": False},
    )
    assert calls["model_load"][1]["device_map"] == "cpu"
    assert calls["model_load"][1]["revision"] == "a" * 40
    assert calls["model_load"][1]["trust_remote_code"] is False
    assert calls["model_load"][1]["local_files_only"] is False
    assert calls["model_load"][1]["dtype"] == torch.bfloat16
    assert calls["eval"] and calls["inference_mode"]
    assert calls["decoded_tokens"] == [[4, 5]]
    assert calls["decode_options"] == {"skip_special_tokens": True}
    assert calls["processor_input"]["text"] == ["formatted prompt"]
    assert ("images" in calls["processor_input"]) == with_image
    assert calls["generation"]["max_new_tokens"] == 7
    assert calls["generation"]["do_sample"] == (temperature > 0)
    assert ("temperature" in calls["generation"]) == (temperature > 0)
    if temperature > 0:
        assert calls["generation"]["temperature"] == temperature
        assert calls["generation"]["top_p"] == 0.8
    assert calls["messages"][0]["content"][0]["text"] == "Judge the task"
    assert calls["messages"][1]["content"][-1] == {"type": "text", "text": "Pick"}


@pytest.mark.parametrize("failure", ["no-model-classes", "all-loaders-fail", "fallback-succeeds"])
def test_given_model_loader_variants_when_loading_then_falls_back_or_reports_failure(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    transformers = ModuleType("transformers")

    class UnsupportedModel:
        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs: Any) -> object:
            raise ValueError("unsupported checkpoint")

    expected = object()

    class SupportedModel:
        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs: Any) -> object:
            return expected

    if failure != "no-model-classes":
        transformers.Qwen3VLForConditionalGeneration = UnsupportedModel
    if failure == "fallback-succeeds":
        transformers.AutoModelForImageTextToText = SupportedModel
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    kwargs = {
        "model_id": "synthetic-model",
        "revision": None,
        "device_map": "cpu",
        "dtype": torch.float32,
        "attn_implementation": None,
        "trust_remote_code": False,
        "local_files_only": False,
    }

    if failure == "fallback-succeeds":
        assert _load_qwen3_vl_model(**kwargs) is expected
    else:
        with pytest.raises(RuntimeError, match="Unable to load synthetic-model"):
            _load_qwen3_vl_model(**kwargs)


@pytest.mark.parametrize("api_key", [None, "explicit-test-key"])
@pytest.mark.parametrize("content", [None, "judgment"])
def test_given_openai_sdk_boundary_when_generating_then_sends_image_and_generation_contract(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture, api_key: str | None, content: str | None
) -> None:
    module = ModuleType("openai")
    constructor = mocker.Mock()
    client = constructor.return_value
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    module.OpenAI = constructor
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-test-key")
    image = Image.new("RGB", (8, 8), "red")
    backend = OpenAICompatibleBackend(
        model="synthetic-model", base_url="https://example.invalid/v1", api_key=api_key, timeout_s=7
    )

    result = backend.generate(
        system_prompt="Judge",
        user_prompt="Pick",
        images=[image],
        config=GenerationConfig(max_new_tokens=12, temperature=0.2, top_p=0.8),
    )

    assert result == (content or "")
    constructor.assert_called_once_with(
        base_url="https://example.invalid/v1",
        api_key=api_key or "environment-test-key",
        timeout=7,
    )
    request = client.chat.completions.create.call_args.kwargs
    assert request["model"] == "synthetic-model"
    assert request["max_tokens"] == 12 and request["temperature"] == 0.2 and request["top_p"] == 0.8
    assert request["messages"][0] == {"role": "system", "content": "Judge"}
    assert request["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert request["messages"][1]["content"][-1] == {"type": "text", "text": "Pick"}


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
