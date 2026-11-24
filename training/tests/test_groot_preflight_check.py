from __future__ import annotations

from pathlib import Path

import pytest
from conftest import load_training_module

_MOD = load_training_module("groot_preflight_check", "training/vla/scripts/groot/preflight_check.py")


def _write_dataset(root: Path) -> Path:
    meta = root / "meta"
    meta.mkdir(parents=True)
    for name in _MOD.REQUIRED_METADATA:
        (meta / name).write_text("{}\n", encoding="utf-8")
    episode = root / "data" / "chunk-000" / "episode_000000.parquet"
    episode.parent.mkdir(parents=True)
    episode.write_bytes(b"parquet")
    video = root / "videos" / "chunk-000" / "observation.images.webcam" / "episode_000000.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"mp4")
    return root


def test_gpu_count_match_reports_status() -> None:
    assert _MOD.check_gpu_count(2, lambda: 2) == "[preflight] visible GPUs: 2 (expected: 2)"


def test_gpu_count_mismatch_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="expected 2 visible GPUs, found 1"):
        _MOD.check_gpu_count(2, lambda: 1)


def test_complete_dataset_passes(tmp_path: Path) -> None:
    _MOD.check_dataset(_write_dataset(tmp_path))


@pytest.mark.parametrize(
    ("relative_path", "message"),
    [
        ("meta/modality.json", "missing required metadata"),
        ("data/chunk-000/episode_000000.parquet", "no Parquet episodes"),
        ("videos/chunk-000/observation.images.webcam/episode_000000.mp4", "no MP4 videos"),
    ],
)
def test_incomplete_dataset_is_rejected(tmp_path: Path, relative_path: str, message: str) -> None:
    dataset = _write_dataset(tmp_path)
    (dataset / relative_path).unlink()

    with pytest.raises(FileNotFoundError, match=message):
        _MOD.check_dataset(dataset)


def test_main_dataset_executes_modality_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dataset = _write_dataset(tmp_path / "dataset")
    marker = tmp_path / "registered"
    config = tmp_path / "modality_config.py"
    config.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ok')\n", encoding="utf-8")

    assert _MOD.main(["dataset", "--dataset", str(dataset), "--modality-config", str(config)]) == 0
    assert marker.read_text(encoding="utf-8") == "ok"
    assert "modality registration: PASS" in capsys.readouterr().out


def test_main_dataset_reports_modality_import_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dataset = _write_dataset(tmp_path / "dataset")
    config = tmp_path / "modality_config.py"
    config.write_text("import gr00t_module_that_does_not_exist\n", encoding="utf-8")

    assert _MOD.main(["dataset", "--dataset", str(dataset), "--modality-config", str(config)]) == 1
    assert "gr00t_module_that_does_not_exist" in capsys.readouterr().err


def test_main_gpus_uses_injected_device_count(capsys: pytest.CaptureFixture[str]) -> None:
    assert _MOD.main(["gpus", "--expected", "1"], device_count=lambda: 2) == 1
    assert "expected 1 visible GPUs, found 2" in capsys.readouterr().err


def test_main_gpus_rejects_non_positive_expected() -> None:
    with pytest.raises(SystemExit):
        _MOD.main(["gpus", "--expected", "0"], device_count=lambda: 0)
