"""Tests for validation-only VLA dataset preflight."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from training.vla.scripts.preflight_dataset import build_dataset_record, run
from training.vla.scripts.vla_contracts import ContractError, dataset_identity_fingerprint

_CREATED_AT = "2026-09-28T00:00:00Z"


def _write_dataset(root: Path, *, total_episodes: int = 2) -> None:
    (root / "meta").mkdir(parents=True)
    (root / "data/chunk-000").mkdir(parents=True)
    info = {
        "codebase_version": "v3.0",
        "total_episodes": total_episodes,
        "features": {"observation.images.base": {"dtype": "video", "shape": [3, 224, 224]}},
    }
    (root / "meta/info.json").write_text(json.dumps(info), encoding="utf-8")
    (root / "data/chunk-000/episode_000000.parquet").write_bytes(b"dataset-content")


def test_given_valid_dataset_when_preflight_repeated_then_identity_is_deterministic(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    _write_dataset(dataset)

    first = build_dataset_record(dataset, "azureml:robot-data:7", "org/robot-data", _CREATED_AT)
    second = build_dataset_record(dataset, "azureml:robot-data:7", "org/robot-data", _CREATED_AT)

    assert dataset_identity_fingerprint(first) == dataset_identity_fingerprint(second)


@pytest.mark.parametrize("asset_id", ["azureml:robot-data:latest", "azureml:robot-data", "https://example/data"])
def test_given_mutable_asset_identity_when_preflight_runs_then_it_is_rejected(tmp_path: Path, asset_id: str) -> None:
    dataset = tmp_path / "dataset"
    _write_dataset(dataset)

    with pytest.raises(ContractError, match="azureml:name:version"):
        build_dataset_record(dataset, asset_id, "org/robot-data", _CREATED_AT)


def test_given_empty_dataset_when_preflight_runs_then_it_is_rejected(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    _write_dataset(dataset, total_episodes=0)

    with pytest.raises(ContractError, match="positive integer"):
        build_dataset_record(dataset, "azureml:robot-data:7", "org/robot-data", _CREATED_AT)


@pytest.mark.parametrize("dataset_repo_id", ["../escape", "/absolute/path"])
def test_given_unsafe_repo_id_when_preflight_runs_then_it_is_rejected(
    tmp_path: Path, dataset_repo_id: str
) -> None:
    dataset = tmp_path / "dataset"
    _write_dataset(dataset)

    with pytest.raises(ContractError, match="safe relative"):
        build_dataset_record(dataset, "azureml:robot-data:7", dataset_repo_id, _CREATED_AT)


def test_given_valid_dataset_when_cli_runs_then_only_manifest_is_written(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    output = tmp_path / "output"
    _write_dataset(dataset)
    args = type(
        "Args",
        (),
        {
            "dataset": dataset,
            "asset_id": "azureml:robot-data:7",
            "dataset_repo_id": "org/robot-data",
            "manifest_output": output,
        },
    )()

    assert run(args) == 0
    assert [path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()] == ["dataset.json"]
