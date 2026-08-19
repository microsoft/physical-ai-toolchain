from __future__ import annotations

import os
import pickle
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock, RLock
from typing import Any

import pytest
import torch

from training.utils import integrity
from training.utils.integrity import safe_load_checkpoint, safe_load_framework_checkpoint


def _touch_marker(path: str) -> None:
    Path(path).touch()


class _Malicious:
    def __init__(self, marker: Path) -> None:
        self._marker = marker

    def __reduce__(self) -> tuple[Callable[[str], None], tuple[str]]:
        return (_touch_marker, (str(self._marker),))


def _write_malicious_checkpoint(tmp_path: Path) -> tuple[Path, Path]:
    marker = tmp_path / "executed"
    path = tmp_path / "model.pt"
    torch.save(_Malicious(marker), path)
    return path, marker


def _unsafe_loader(checkpoint_path: str) -> Any:
    return torch.load(checkpoint_path, weights_only=False)


class TestSafeLoadCheckpoint:
    def test_success(self, tmp_path: Path) -> None:
        path = tmp_path / "model.pt"
        data = {"model_state_dict": {"actor.weight": torch.zeros(1)}}
        torch.save(data, path)

        loaded = safe_load_checkpoint(str(path))

        assert "model_state_dict" in loaded
        assert "actor.weight" in loaded["model_state_dict"]

    def test_rejects_malicious_payload(self, tmp_path: Path) -> None:
        path, marker = _write_malicious_checkpoint(tmp_path)

        with pytest.raises(ValueError, match="could not be loaded under weights_only=True"):
            safe_load_checkpoint(str(path))

        assert not marker.exists()

    def test_propagates_corrupt_checkpoint_error(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.pt"
        path.write_bytes(b"not a torch checkpoint")

        with pytest.raises(Exception) as exc_info:
            safe_load_checkpoint(str(path))

        assert not isinstance(exc_info.value, ValueError)

    def test_rejects_non_dictionary_checkpoint(self, tmp_path: Path) -> None:
        path = tmp_path / "tensor.pt"
        torch.save(torch.zeros(1), path)

        with pytest.raises(ValueError, match="must contain a dictionary"):
            safe_load_checkpoint(str(path))


class TestSafeLoadFrameworkCheckpoint:
    def test_forces_and_restores_environment(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "model.pt"
        torch.save({"weight": torch.zeros(1)}, path)
        monkeypatch.setenv("TORCH_FORCE_WEIGHTS_ONLY_LOAD", "previous-force")
        monkeypatch.setenv("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "previous-disable")
        observed: dict[str, str | None] = {}

        def loader(checkpoint_path: str) -> dict[str, Any]:
            observed["path"] = checkpoint_path
            observed["force"] = os.environ.get("TORCH_FORCE_WEIGHTS_ONLY_LOAD")
            observed["disable"] = os.environ.get("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD")
            return torch.load(checkpoint_path, weights_only=False)

        result = safe_load_framework_checkpoint(str(path), loader=loader)

        assert "weight" in result
        assert observed == {"path": str(path), "force": "1", "disable": None}
        assert os.environ["TORCH_FORCE_WEIGHTS_ONLY_LOAD"] == "previous-force"
        assert os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] == "previous-disable"

    def test_restores_environment_after_loader_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TORCH_FORCE_WEIGHTS_ONLY_LOAD", raising=False)
        monkeypatch.setenv("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")

        def loader(path: str) -> Any:
            assert path == "model.pt"
            assert os.environ["TORCH_FORCE_WEIGHTS_ONLY_LOAD"] == "1"
            assert "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD" not in os.environ
            raise RuntimeError("loader failed")

        with pytest.raises(RuntimeError, match="loader failed"):
            safe_load_framework_checkpoint("model.pt", loader=loader)

        assert "TORCH_FORCE_WEIGHTS_ONLY_LOAD" not in os.environ
        assert os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] == "1"

    def test_overrides_explicit_unsafe_load(self, tmp_path: Path) -> None:
        path, marker = _write_malicious_checkpoint(tmp_path)

        with pytest.raises(ValueError, match="could not be loaded under weights_only=True"):
            safe_load_framework_checkpoint(str(path), loader=_unsafe_loader)

        assert not marker.exists()
        torch.load(path, weights_only=False)
        assert marker.exists()

    def test_loads_benign_framework_checkpoint(self, tmp_path: Path) -> None:
        path = tmp_path / "model.pt"
        checkpoint = {
            "model_state_dict": {"weight": torch.zeros(2)},
            "optimizer_state_dict": {"state": {}, "param_groups": [{"lr": 1e-3, "params": [0]}]},
            "iteration": 100,
            "infos": None,
        }
        torch.save(checkpoint, path)

        loaded = safe_load_framework_checkpoint(str(path), loader=_unsafe_loader)

        assert loaded["iteration"] == 100
        assert torch.equal(loaded["model_state_dict"]["weight"], torch.zeros(2))

    def test_is_reentrant(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        inner_path = tmp_path / "inner.pt"
        torch.save({"weight": torch.zeros(1)}, inner_path)
        monkeypatch.delenv("TORCH_FORCE_WEIGHTS_ONLY_LOAD", raising=False)
        observed: list[str | None] = []

        def inner_loader(path: str) -> dict[str, Any]:
            observed.append(os.environ.get("TORCH_FORCE_WEIGHTS_ONLY_LOAD"))
            return torch.load(path, weights_only=False)

        def outer_loader(path: str) -> dict[str, Any]:
            result = safe_load_framework_checkpoint(str(inner_path), loader=inner_loader)
            observed.append(os.environ.get("TORCH_FORCE_WEIGHTS_ONLY_LOAD"))
            return result

        safe_load_framework_checkpoint("outer.pt", loader=outer_loader)

        assert observed == ["1", "1"]
        assert "TORCH_FORCE_WEIGHTS_ONLY_LOAD" not in os.environ

    def test_preserves_concurrent_environment_updates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "model.pt"
        torch.save({"weight": torch.zeros(1)}, path)
        monkeypatch.setenv("TORCH_FORCE_WEIGHTS_ONLY_LOAD", "previous-force")
        monkeypatch.setenv("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "previous-disable")

        def loader(checkpoint_path: str) -> dict[str, Any]:
            result = torch.load(checkpoint_path, weights_only=False)
            os.environ["TORCH_FORCE_WEIGHTS_ONLY_LOAD"] = "new-force"
            os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "new-disable"
            return result

        safe_load_framework_checkpoint(str(path), loader=loader)

        assert os.environ["TORCH_FORCE_WEIGHTS_ONLY_LOAD"] == "new-force"
        assert os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] == "new-disable"

    def test_preserves_concurrent_torch_load_update(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "model.pt"
        torch.save({"weight": torch.zeros(1)}, path)
        original_torch_load = torch.load

        def replacement(*args: Any, **kwargs: Any) -> Any:
            return original_torch_load(*args, **kwargs)

        def loader(checkpoint_path: str) -> dict[str, Any]:
            result = torch.load(checkpoint_path, weights_only=False)
            torch.load = replacement
            return result

        try:
            safe_load_framework_checkpoint(str(path), loader=loader)
            assert torch.load is replacement
        finally:
            torch.load = original_torch_load

    def test_wraps_explicit_pickle_module_error(self, tmp_path: Path) -> None:
        path = tmp_path / "model.pt"
        torch.save({"weight": torch.zeros(1)}, path)

        def loader(checkpoint_path: str) -> Any:
            return torch.load(checkpoint_path, pickle_module=pickle, weights_only=False)

        with pytest.raises(ValueError, match="explicit pickle_module"):
            safe_load_framework_checkpoint(str(path), loader=loader)

    def test_restores_environment_after_rejection(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path, marker = _write_malicious_checkpoint(tmp_path)
        monkeypatch.delenv("TORCH_FORCE_WEIGHTS_ONLY_LOAD", raising=False)
        monkeypatch.setenv("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")

        with pytest.raises(ValueError, match="could not be loaded under weights_only=True"):
            safe_load_framework_checkpoint(str(path), loader=_unsafe_loader)

        assert not marker.exists()
        assert "TORCH_FORCE_WEIGHTS_ONLY_LOAD" not in os.environ
        assert os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] == "1"

    def test_wraps_nested_weights_only_error(self, tmp_path: Path) -> None:
        path, marker = _write_malicious_checkpoint(tmp_path)

        def loader(checkpoint_path: str) -> Any:
            try:
                return torch.load(checkpoint_path, weights_only=False)
            except Exception as error:
                raise RuntimeError("framework load failed") from error

        with pytest.raises(ValueError, match="could not be loaded under weights_only=True") as exc_info:
            safe_load_framework_checkpoint(str(path), loader=loader)

        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert not marker.exists()

    def test_wraps_contextual_weights_only_error(self, tmp_path: Path) -> None:
        path, marker = _write_malicious_checkpoint(tmp_path)

        def loader(checkpoint_path: str) -> Any:
            try:
                return torch.load(checkpoint_path, weights_only=False)
            except Exception as error:
                wrapped = RuntimeError("framework load failed")
                wrapped.__context__ = error
                raise wrapped from None

        with pytest.raises(ValueError, match="could not be loaded under weights_only=True"):
            safe_load_framework_checkpoint(str(path), loader=loader)

        assert not marker.exists()

    def test_propagates_unrelated_error_untouched(self) -> None:
        error = FileNotFoundError("no such checkpoint")

        def loader(path: str) -> Any:
            del path
            raise error

        with pytest.raises(FileNotFoundError) as exc_info:
            safe_load_framework_checkpoint("missing.pt", loader=loader)

        assert exc_info.value is error

    def test_handles_cyclic_exception_chain(self) -> None:
        error = RuntimeError("cyclic loader failure")
        error.__context__ = error

        def loader(path: str) -> Any:
            del path
            raise error

        with pytest.raises(RuntimeError) as exc_info:
            safe_load_framework_checkpoint("model.pt", loader=loader)

        assert exc_info.value is error

    def test_rejects_loader_that_bypasses_torch_load(self) -> None:
        with pytest.raises(RuntimeError, match=r"did not call torch\.load synchronously"):
            safe_load_framework_checkpoint("model.pt", loader=lambda path: {"path": path})

    def test_serializes_concurrent_loads(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        first_path = tmp_path / "first.pt"
        second_path = tmp_path / "second.pt"
        torch.save({"weight": torch.zeros(1)}, first_path)
        torch.save({"weight": torch.ones(1)}, second_path)
        first_entered = Event()
        release_first = Event()
        second_entered = Event()
        second_lock_attempted = Event()

        class TrackingRLock:
            def __init__(self) -> None:
                self._lock = RLock()
                self._count_lock = Lock()
                self._attempts = 0

            def __enter__(self) -> None:
                with self._count_lock:
                    self._attempts += 1
                    if self._attempts == 2:
                        second_lock_attempted.set()
                self._lock.acquire()

            def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
                self._lock.release()

        monkeypatch.setattr(integrity, "_FORCE_WEIGHTS_ONLY_LOCK", TrackingRLock())

        def first_loader(path: str) -> dict[str, Any]:
            first_entered.set()
            assert release_first.wait(timeout=2)
            return torch.load(path, weights_only=False)

        def second_loader(path: str) -> dict[str, Any]:
            second_entered.set()
            return torch.load(path, weights_only=False)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(safe_load_framework_checkpoint, str(first_path), loader=first_loader)
            assert first_entered.wait(timeout=2)
            second = executor.submit(safe_load_framework_checkpoint, str(second_path), loader=second_loader)
            assert second_lock_attempted.wait(timeout=2)
            assert not second_entered.is_set()
            release_first.set()
            first.result(timeout=2)
            second.result(timeout=2)

        assert second_entered.is_set()
