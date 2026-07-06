from __future__ import annotations

import pickle
from pathlib import Path

import pytest
import torch

from training.utils.integrity import safe_load_checkpoint


def test_safe_load_checkpoint_success(tmp_path: Path) -> None:
    path = tmp_path / "model.pt"
    data = {"model_state_dict": {"actor.weight": torch.zeros(1)}}
    torch.save(data, path)

    loaded = safe_load_checkpoint(str(path))
    assert "model_state_dict" in loaded
    assert "actor.weight" in loaded["model_state_dict"]


def test_safe_load_checkpoint_rejects_malicious_payload(tmp_path: Path) -> None:
    path = tmp_path / "model.pt"

    class Malicious:
        def __reduce__(self):
            import os

            return (os.system, ("echo hacked",))

    with open(path, "wb") as f:
        pickle.dump(Malicious(), f)

    with pytest.raises(ValueError, match="could not be loaded under weights_only=True"):
        safe_load_checkpoint(str(path))
