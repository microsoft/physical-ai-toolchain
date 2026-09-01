from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import torch

from training.utils.integrity import safe_load_checkpoint, safe_load_rsl_rl_checkpoint, safe_load_skrl_checkpoint


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

    def test_wraps_corrupt_checkpoint_restricted_load_error(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.pt"
        path.write_bytes(b"not a torch checkpoint")

        with pytest.raises(ValueError, match="could not be loaded under weights_only=True"):
            safe_load_checkpoint(str(path))

    def test_rejects_non_dictionary_checkpoint(self, tmp_path: Path) -> None:
        path = tmp_path / "tensor.pt"
        torch.save(torch.zeros(1), path)

        with pytest.raises(ValueError, match="must contain a dictionary"):
            safe_load_checkpoint(str(path))

    @pytest.mark.parametrize("protocol", [4, 5])
    def test_wraps_unsupported_pickle_protocol(self, tmp_path: Path, protocol: int) -> None:
        path = tmp_path / f"protocol-{protocol}.pt"
        torch.save({"weight": torch.zeros(1)}, path, pickle_protocol=protocol)

        with pytest.raises(ValueError, match="could not be loaded under weights_only=True"):
            safe_load_checkpoint(str(path))


class TestSafeLoadSkrlCheckpoint:
    def test_applies_known_modules_and_skips_unknown_modules(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        path = tmp_path / "model.pt"
        torch.save({"policy": {"weight": torch.zeros(1)}, "unknown": {}}, path)
        module = MagicMock()
        agent = SimpleNamespace(device="cpu", checkpoint_modules={"policy": module})

        safe_load_skrl_checkpoint(str(path), agent=agent)

        module.load_state_dict.assert_called_once()
        module.eval.assert_called_once_with()
        assert "unknown" in caplog.text

    def test_rejects_malicious_payload(self, tmp_path: Path) -> None:
        path, marker = _write_malicious_checkpoint(tmp_path)
        agent = SimpleNamespace(device="cpu", checkpoint_modules={})

        with pytest.raises(ValueError, match="could not be loaded under weights_only=True"):
            safe_load_skrl_checkpoint(str(path), agent=agent)

        assert not marker.exists()


class TestSafeLoadRslRlCheckpoint:
    def test_applies_algorithm_state_and_iteration(self, tmp_path: Path) -> None:
        path = tmp_path / "model.pt"
        checkpoint = {"actor_state_dict": {}, "optimizer_state_dict": {}, "iter": 42, "infos": {"score": 1}}
        torch.save(checkpoint, path)
        algorithm = MagicMock()
        algorithm.load.return_value = True
        runner = SimpleNamespace(device="cpu", alg=algorithm, current_learning_iteration=0)

        infos = safe_load_rsl_rl_checkpoint(str(path), runner=runner)

        loaded_checkpoint = algorithm.load.call_args.args[0]
        assert loaded_checkpoint["iter"] == 42
        algorithm.load.assert_called_once_with(loaded_checkpoint, None, True)
        assert runner.current_learning_iteration == 42
        assert infos == {"score": 1}

    def test_rejects_malicious_payload(self, tmp_path: Path) -> None:
        path, marker = _write_malicious_checkpoint(tmp_path)
        runner = SimpleNamespace(device="cpu", alg=MagicMock(), current_learning_iteration=0)

        with pytest.raises(ValueError, match="could not be loaded under weights_only=True"):
            safe_load_rsl_rl_checkpoint(str(path), runner=runner)

        assert not marker.exists()
