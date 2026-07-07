from __future__ import annotations

from pathlib import Path

import pytest

from training.utils.hashing import verify_sha256_sidecar, write_sha256_sidecar


def test_verify_sha256_sidecar_success(tmp_path: Path) -> None:
    model_path = tmp_path / "model.pt"
    model_path.write_text("dummy model data")
    write_sha256_sidecar(str(model_path))

    # Should not raise
    verify_sha256_sidecar(str(model_path))


def test_verify_sha256_sidecar_missing(tmp_path: Path) -> None:
    model_path = tmp_path / "model.pt"
    model_path.write_text("dummy model data")

    with pytest.raises(FileNotFoundError, match="Missing integrity manifest"):
        verify_sha256_sidecar(str(model_path))


def test_verify_sha256_sidecar_empty(tmp_path: Path) -> None:
    model_path = tmp_path / "model.pt"
    model_path.write_text("dummy model data")
    sidecar = tmp_path / "model.pt.sha256"
    sidecar.write_text("")

    with pytest.raises(ValueError, match="Empty integrity manifest"):
        verify_sha256_sidecar(str(model_path))


def test_verify_sha256_sidecar_mismatch(tmp_path: Path) -> None:
    model_path = tmp_path / "model.pt"
    model_path.write_text("dummy model data")
    write_sha256_sidecar(str(model_path))

    # Tamper with model
    model_path.write_text("tampered model data")

    with pytest.raises(ValueError, match="SHA256 mismatch"):
        verify_sha256_sidecar(str(model_path))
