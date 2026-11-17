"""Filesystem-backed contracts for judge dataset metadata variants."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from physical_ai_dataset_io.lerobot import _resolve_instruction_v3, iter_episodes, load_dataset_spec


@pytest.mark.parametrize("layout", ["nested", "flat"])
def test_given_jsonl_dataset_when_resolving_then_normalizes_tasks_and_skips_missing_media(
    jsonl_dataset: Path, layout: str
) -> None:
    if layout == "flat":
        (jsonl_dataset / "meta/episodes/chunk-000/episode_000000.jsonl").rename(jsonl_dataset / "meta/episodes.jsonl")

    records = list(iter_episodes(jsonl_dataset))

    assert [record.episode_index for record in records] == [0, 1]
    assert [record.instruction for record in records] == ["Pick the block", "Place the block"]
    assert records[0].duration_s == 1
    assert records[0].from_timestamp is None
    assert all(record.video_paths["front"].is_file() for record in records)


def test_given_requested_indices_and_limit_when_resolving_then_filters_episodes(jsonl_dataset: Path) -> None:
    assert [record.episode_index for record in iter_episodes(jsonl_dataset, indices=[1], limit=1)] == [1]
    assert [record.episode_index for record in iter_episodes(jsonl_dataset, limit=1)] == [0]


def test_given_unknown_view_when_resolving_then_reports_available_views(jsonl_dataset: Path) -> None:
    with pytest.raises(ValueError, match="Requested views not present"):
        list(iter_episodes(jsonl_dataset, views=("missing",)))


def test_given_missing_video_template_when_resolving_then_reports_metadata_error(jsonl_dataset: Path) -> None:
    path = jsonl_dataset / "meta/info.json"
    info = json.loads(path.read_text())
    del info["features"]["front"]["videos_path"]
    path.write_text(json.dumps(info))

    with pytest.raises(KeyError, match="No videos_path template"):
        list(iter_episodes(jsonl_dataset))


def test_given_no_video_features_when_loading_then_rejects_dataset(jsonl_dataset: Path) -> None:
    path = jsonl_dataset / "meta/info.json"
    info = json.loads(path.read_text())
    info["features"] = {}
    path.write_text(json.dumps(info))

    with pytest.raises(ValueError, match="No video features"):
        load_dataset_spec(jsonl_dataset)


def test_given_missing_episode_metadata_when_resolving_then_reports_missing_file(jsonl_dataset: Path) -> None:
    (jsonl_dataset / "meta/episodes/chunk-000/episode_000000.jsonl").unlink()

    with pytest.raises(FileNotFoundError, match="No jsonl v3 episode metadata"):
        list(iter_episodes(jsonl_dataset))


def test_given_malformed_encoded_tasks_when_resolving_then_falls_back_to_task_index(jsonl_dataset: Path) -> None:
    path = jsonl_dataset / "meta/episodes/chunk-000/episode_000000.jsonl"
    path.write_text(json.dumps({"episode_index": 0, "length": 12, "tasks": "[broken", "task_index": 3}))

    [record] = iter_episodes(jsonl_dataset)

    assert record.instruction == "Place the block"


def test_given_v21_metadata_when_resolving_then_uses_global_tasks_and_empty_instruction(jsonl_dataset: Path) -> None:
    path = jsonl_dataset / "meta/info.json"
    info = json.loads(path.read_text())
    info.update(codebase_version="v2.1", video_path="videos/front/episode_{episode_index:06d}.mp4")
    path.write_text(json.dumps(info))
    episodes = [{"episode_index": 0, "length": 12, "task_index": 3}, {"episode_index": 1, "length": 12}]
    (jsonl_dataset / "meta/episodes.jsonl").write_text("\n".join(json.dumps(row) for row in episodes))

    records = list(iter_episodes(jsonl_dataset))

    assert [record.instruction for record in records] == ["Place the block", ""]
    assert all(record.video_paths["front"].exists() for record in records)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"tasks": np.array(["Pick", "Place"])}, "Pick | Place"),
        ({"tasks": "Pick"}, "Pick"),
        ({"tasks": [], "task_index": 3}, "Place"),
        ({"tasks": None}, ""),
    ],
)
def test_given_v3_task_representation_when_resolving_instruction_then_preserves_text(
    row: dict[str, object], expected: str
) -> None:
    assert _resolve_instruction_v3(row, {3: "Place"}) == expected
