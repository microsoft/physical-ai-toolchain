"""Tests for exporting LeRobot v3.0 episodes as a derived LeRobot v3.0 dataset."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.api.models.datasources import FrameInsertion
from src.api.services import lerobot_exporter
from src.api.services.episode_edits import EpisodeEditOperations, SubtaskSegment, TrajectoryAdjustment
from src.api.services.image_transform import CropRegion, ImageTransform, ResizeDimensions
from src.api.services.lerobot_exporter import (
    ADJUSTED_STATE,
    ADJUSTED_STATE_MASK,
    CLAIM_DIRECTORY,
    LOCK_FILE,
    PROVENANCE_FILE,
    LeRobotExporter,
    LeRobotExportError,
)
from src.api.services.lerobot_language import LanguageInstruction

from .lerobot_sources import (
    CAMERA,
    FPS,
    LENGTHS,
    frame_gray,
    frame_state,
    hold_lock,
    stop_export,
    write_source,
)

STATS_KEYS = {"min", "max", "mean", "std", "count", "q01", "q10", "q50", "q90", "q99"}
DATASET_ENTRIES = ["data", PROVENANCE_FILE, "meta", "videos"]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_source(tmp_path / "datasets/capture/lerobot")


def _export(source: Path, episodes: list[int], edits: dict[int, EpisodeEditOperations] | None = None) -> Path:
    output = source.parents[1] / "edited"
    result = LeRobotExporter(source, output, dataset_id="capture--lerobot").export_episodes(episodes, edits)
    assert result.success, result.error
    assert result.output_files == [str(output)]
    return output


def _edits(episode: int, **operations: Any) -> dict[int, EpisodeEditOperations]:
    return {episode: EpisodeEditOperations(dataset_id="capture--lerobot", episode_index=episode, **operations)}


def _decode(path: Path) -> list[tuple[float, np.ndarray]]:
    with av.open(str(path)) as container:
        return [(frame.time, frame.to_ndarray(format="rgb24")) for frame in container.decode(video=0)]


def _video(output: Path) -> Path:
    return output / f"videos/{CAMERA}/chunk-000/file-000.mp4"


def _info(output: Path) -> dict[str, Any]:
    return json.loads((output / "meta/info.json").read_text())


def _episodes(output: Path) -> list[dict[str, Any]]:
    return pq.read_table(output / "meta/episodes/chunk-000/file-000.parquet").to_pylist()


def _data(output: Path) -> pa.Table:
    return pq.read_table(output / "data/chunk-000/file-000.parquet")


def _digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


SPEECH = {
    "role": "assistant",
    "content": None,
    "style": None,
    "camera": None,
    "tool_calls": [{"type": "function", "function": {"name": "say", "arguments": {"text": "grasping"}}}],
}


def _row(style: str, content: str, timestamp: float, role: str = "assistant") -> dict[str, Any]:
    return {
        "role": role,
        "content": content,
        "style": style,
        "timestamp": timestamp,
        "camera": None,
        "tool_calls": None,
    }


def _add_language(
    source: Path, persistent: dict[int, list[dict[str, Any]]], events: dict[tuple[int, int], list[dict[str, Any]]]
) -> None:
    """Add language columns laid out as lerobot's annotation writer does: persistent rows on every episode frame."""
    path = source / "data/chunk-000/file-000.parquet"
    table = pq.read_table(path)
    keys = list(zip(table.column("episode_index").to_pylist(), table.column("frame_index").to_pylist(), strict=True))
    table = table.append_column("language_persistent", pa.array([persistent.get(episode, []) for episode, _ in keys]))
    table = table.append_column("language_events", pa.array([events.get(key, []) for key in keys]))
    pq.write_table(table, path)
    info = json.loads((source / "meta/info.json").read_text())
    for name in ("language_persistent", "language_events"):
        info["features"][name] = {"dtype": "language", "shape": [1], "names": None}
    (source / "meta/info.json").write_text(json.dumps(info))


def _language(output: Path, episode: int) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    rows = _data(output).to_pylist()
    return [(row["language_persistent"], row["language_events"]) for row in rows if row["episode_index"] == episode]


def _summary(persistent: list[dict[str, Any]]) -> list[tuple[str, str, float]]:
    return [(row["style"], row["content"], row["timestamp"]) for row in persistent]


def test_removed_frames_renumber_the_timeline_and_leave_the_source_unchanged(source: Path) -> None:
    before = _digests(source)

    output = _export(source, [0], _edits(0, removed_frames={2, 3, 50}))

    kept = [0, 1, *range(4, 12)]
    data = _data(output)
    assert data.column("frame_index").to_pylist() == list(range(10))
    assert data.column("index").to_pylist() == list(range(10))
    assert data.column("episode_index").to_pylist() == [0] * 10
    np.testing.assert_allclose(data.column("timestamp").to_numpy(), np.arange(10) / FPS, atol=1e-6)
    assert data.column("observation.state").to_pylist() == [frame_state(0, frame) for frame in kept]
    frames = _decode(_video(output))
    assert len(frames) == 10
    for (time, image), (position, frame) in zip(frames, enumerate(kept), strict=True):
        assert abs(time - position / FPS) < 1e-4
        assert abs(float(image.mean()) - frame_gray(0, frame)) < 4
    info = _info(output)
    assert (info["total_episodes"], info["total_frames"], info["splits"]) == (1, 10, {"train": "0:1"})
    assert not {"language_persistent", "language_events"} & (set(data.column_names) | set(info["features"]))
    provenance = json.loads((output / PROVENANCE_FILE).read_text())
    assert provenance["source"] == {"dataset_id": "capture--lerobot", "codebase_version": "v3.0", "fps": FPS}
    assert provenance["episodes"][0]["frame_sources"] == kept
    assert provenance["episodes"][0]["edits"]["removed_frames"] == [2, 3]
    assert _digests(source) == before


def test_two_episode_exports_carry_offsets_ranges_and_videos(source: Path) -> None:
    output = _export(source, [1, 0], _edits(1, removed_frames={0}))

    episodes = _episodes(output)
    assert [episode["episode_index"] for episode in episodes] == [0, 1]
    assert [(episode["dataset_from_index"], episode["dataset_to_index"]) for episode in episodes] == [(0, 7), (7, 19)]
    windows = [
        (episode[f"videos/{CAMERA}/from_timestamp"], episode[f"videos/{CAMERA}/to_timestamp"]) for episode in episodes
    ]
    assert windows == [(0.0, 0.7), (0.7, 1.9)]
    assert all(episode["tasks"] == ["pick the part"] for episode in episodes)
    data = _data(output)
    assert data.column("index").to_pylist() == list(range(19))
    assert data.column("episode_index").to_pylist() == [0] * 7 + [1] * 12
    assert data.column("frame_index").to_pylist() == list(range(7)) + list(range(12))
    expected = [(1, frame) for frame in range(1, 8)] + [(0, frame) for frame in range(12)]
    assert data.column("observation.state").to_pylist() == [frame_state(*pair) for pair in expected]
    frames = _decode(_video(output))
    assert len(frames) == 19
    for (episode, (start, _end)), first in zip(enumerate(windows), (0, 7), strict=True):
        time, image = frames[first]
        assert abs(time - start) < 1e-4
        assert abs(float(image.mean()) - frame_gray(*expected[first])) < 4, episode
    for (_time, image), pair in zip(frames, expected, strict=True):
        assert abs(float(image.mean()) - frame_gray(*pair)) < 4


def test_inserted_frames_interpolate_floats_and_hold_other_features(source: Path) -> None:
    insertion = [FrameInsertion(after_frame_index=4, interpolation_factor=0.25)]

    output = _export(source, [0], _edits(0, inserted_frames=insertion, removed_frames={5}))

    data = _data(output)
    assert data.num_rows == 12
    inserted = data.slice(5, 1).to_pylist()[0]
    np.testing.assert_allclose(
        inserted["observation.state"], 0.75 * np.array(frame_state(0, 4)) + 0.25 * np.array(frame_state(0, 6))
    )
    assert inserted["observation.phase"] == 1
    assert inserted["observation.flag"] is True
    _time, image = _decode(_video(output))[5]
    assert abs(float(image.mean()) - (0.75 * frame_gray(0, 4) + 0.25 * frame_gray(0, 6))) < 4
    provenance = json.loads((output / PROVENANCE_FILE).read_text())["episodes"][0]
    assert provenance["frame_sources"][4:7] == [4, None, 6]
    assert provenance["edits"]["inserted_frames"] == [{"after_frame_index": 4, "interpolation_factor": 0.25}]


def test_crop_and_resize_change_the_camera_video_and_feature_shape(source: Path) -> None:
    transform = ImageTransform(
        crop=CropRegion(x=4, y=2, width=16, height=12), resize=ResizeDimensions(width=8, height=6)
    )

    output = _export(source, [0], _edits(0, camera_transforms={CAMERA: transform}))

    feature = _info(output)["features"][CAMERA]
    assert feature["shape"] == [6, 8, 3]
    assert (feature["info"]["video.height"], feature["info"]["video.width"]) == (6, 8)
    frames = _decode(_video(output))
    assert len(frames) == 12
    assert frames[0][1].shape == (6, 8, 3)


def test_trajectory_adjustments_add_derived_state_beside_the_recorded_state(source: Path) -> None:
    adjustments = [
        TrajectoryAdjustment(frame_index=2, channel_deltas={0: 0.5}),
        TrajectoryAdjustment(frame_index=7, channel_values={2: -1.0}),
    ]

    output = _export(source, [0], _edits(0, trajectory_adjustments=adjustments, removed_frames={1}))

    data = _data(output)
    recorded = np.array(data.column("observation.state").to_pylist())
    adjusted = np.array(data.column(ADJUSTED_STATE).to_pylist())
    mask = np.array(data.column(ADJUSTED_STATE_MASK).to_pylist())
    kept = [0, *range(2, 12)]
    np.testing.assert_allclose(recorded, [frame_state(0, frame) for frame in kept])
    assert mask.tolist() == [frame in {2, 7} for frame in kept]
    np.testing.assert_allclose(adjusted[~mask], recorded[~mask])
    np.testing.assert_allclose(adjusted[1], [0.5, 2.0, 2.0])
    np.testing.assert_allclose(adjusted[6], [0.0, 7.0, -1.0])
    features = _info(output)["features"]
    assert features[ADJUSTED_STATE] == {"dtype": "float32", "shape": [3], "names": ["x", "y", "z"]}
    assert features[ADJUSTED_STATE_MASK] == {"dtype": "bool", "shape": [1], "names": None}
    edits = json.loads((output / PROVENANCE_FILE).read_text())["episodes"][0]["edits"]
    assert [adjustment["frame_index"] for adjustment in edits["trajectory_adjustments"]] == [2, 7]


def test_episode_and_dataset_stats_match_the_exported_data(source: Path) -> None:
    output = _export(source, [0, 1], _edits(0, removed_frames={3}))

    data = _data(output)
    episodes = _episodes(output)
    stats = json.loads((output / "meta/stats.json").read_text())
    numeric = [name for name, feature in _info(output)["features"].items() if feature["dtype"] != "video"]
    assert set(stats) == {*numeric, CAMERA}
    for name in numeric:
        column = data.column(name).to_pylist()
        values = np.array([value if isinstance(value, list) else [value] for value in column], dtype=np.float64)
        assert set(stats[name]) == STATS_KEYS
        np.testing.assert_allclose(stats[name]["mean"], values.mean(axis=0))
        np.testing.assert_allclose(stats[name]["q90"], np.quantile(values, 0.9, axis=0))
        assert stats[name]["count"] == [len(values)]
        for episode in episodes:
            rows = values[episode["dataset_from_index"] : episode["dataset_to_index"]]
            np.testing.assert_allclose(episode[f"stats/{name}/min"], rows.min(axis=0))
            np.testing.assert_allclose(episode[f"stats/{name}/std"], rows.std(axis=0))
            assert episode[f"stats/{name}/count"] == [len(rows)]
    frames = np.stack([image for _time, image in _decode(_video(output))]).astype(np.float64) / 255
    assert set(stats[CAMERA]) == STATS_KEYS
    assert np.array(stats[CAMERA]["mean"]).shape == (3, 1, 1)
    np.testing.assert_allclose(np.array(stats[CAMERA]["mean"]).ravel(), frames.mean(axis=(0, 1, 2)), atol=0.02)
    assert stats[CAMERA]["count"] == [19]
    for episode in episodes:
        assert np.array(episode[f"stats/{CAMERA}/max"]).shape == (3, 1, 1)
        assert episode[f"stats/{CAMERA}/count"] == [episode["length"]]


def test_subtasks_are_remapped_to_output_frames_in_provenance(source: Path) -> None:
    subtasks = [
        SubtaskSegment(id="reach", label="Reach", frame_range=(0, 4), color="#ff0000", source="manual"),
        SubtaskSegment(id="grasp", label="Grasp", frame_range=(5, 11), color="#00ff00", source="manual"),
        SubtaskSegment(id="gone", label="Gone", frame_range=(2, 3), color="#0000ff", source="auto"),
    ]
    insertion = [FrameInsertion(after_frame_index=1, interpolation_factor=0.5)]

    output = _export(source, [0], _edits(0, subtasks=subtasks, inserted_frames=insertion, removed_frames={3}))

    edits = json.loads((output / PROVENANCE_FILE).read_text())["episodes"][0]["edits"]
    assert [(subtask["id"], subtask["frame_range"]) for subtask in edits["subtasks"]] == [
        ("reach", [0, 4]),
        ("grasp", [5, 11]),
        ("gone", [3, 3]),
    ]


def test_source_language_rows_follow_the_edited_timeline(source: Path) -> None:
    plan = _row("plan", "reach then grasp", 0.0)
    _add_language(
        source,
        {
            0: [plan, _row("subtask", "reach", 0.0), _row("subtask", "grasp", 0.5), _row("subtask", "lift", 1.1)],
            1: [_row("subtask", "place", 0.0)],
        },
        {(0, 1): [SPEECH], (0, 3): [SPEECH]},
    )
    insertion = [FrameInsertion(after_frame_index=3, interpolation_factor=0.5)]

    output = _export(source, [0, 1], _edits(0, removed_frames={1, 2, 11}, inserted_frames=insertion))

    frames = _language(output, 0)
    expected = [
        ("plan", "reach then grasp", 0.0),
        ("subtask", "reach", 0.0),
        ("subtask", "grasp", float(np.float32(0.4))),
    ]
    assert [_summary(persistent) for persistent, _ in frames] == [expected] * 10
    assert [len(events) for _, events in frames] == [0, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    assert [_summary(persistent) for persistent, _ in _language(output, 1)] == [[("subtask", "place", 0.0)]] * LENGTHS[
        1
    ]


def test_source_language_rows_collapsed_by_removals_keep_the_latest(source: Path) -> None:
    variants = [
        _row("task_aug", "pick the part", 0.0, role="user"),
        _row("task_aug", "grab the part", 0.0, role="user"),
    ]
    _add_language(source, {0: [*variants, _row("subtask", "reach", 0.1), _row("subtask", "grasp", 0.2)]}, {})

    output = _export(source, [0], _edits(0, removed_frames={1, 2}))

    persistent, _ = _language(output, 0)[0]
    assert _summary(persistent) == [
        ("task_aug", "pick the part", 0.0),
        ("task_aug", "grab the part", 0.0),
        ("subtask", "grasp", float(np.float32(0.1))),
    ]


def _subtask(label: str, frames: tuple[int, int]) -> SubtaskSegment:
    return SubtaskSegment(id=label.lower(), label=label, frame_range=frames, color="#ff0000", source="manual")


def test_subtasks_are_written_as_lerobot_subtask_rows(source: Path) -> None:
    subtasks = [_subtask("Reach", (0, 4)), _subtask("Grasp", (5, 11))]

    output = _export(source, [0, 1], _edits(0, removed_frames={0, 1}, subtasks=subtasks))

    frames = _language(output, 0)
    assert [_summary(persistent) for persistent, _ in frames] == [
        [("subtask", "Reach", 0.0), ("subtask", "Grasp", float(np.float32(0.3)))]
    ] * 10
    assert frames[0][0][0] | {"timestamp": 0.0} == _row("subtask", "Reach", 0.0)
    assert all(events == [] for _, events in frames)
    assert _language(output, 1) == [([], [])] * LENGTHS[1]
    language = {"dtype": "language", "shape": [1], "names": None}
    features = _info(output)["features"]
    assert (features["language_persistent"], features["language_events"]) == (language, language)
    assert not any("language" in key for key in json.loads((output / "meta/stats.json").read_text()))
    assert not any("language" in key for row in _episodes(output) for key in row)


def test_subtasks_sharing_an_output_start_write_one_row(source: Path) -> None:
    subtasks = [_subtask("Reach", (0, 3)), _subtask("Grasp", (2, 6)), _subtask("Lift", (7, 11))]

    output = _export(source, [0], _edits(0, removed_frames={0, 1, 2}, subtasks=subtasks))

    persistent, _ = _language(output, 0)[0]
    assert _summary(persistent) == [("subtask", "Grasp", 0.0), ("subtask", "Lift", float(np.float32(0.4)))]


def test_subtasks_replace_source_subtask_rows_and_keep_other_styles(source: Path) -> None:
    _add_language(
        source,
        {
            0: [
                _row("plan", "reach then grasp", 0.0),
                _row("subtask", "old reach", 0.0),
                _row("subtask", "old grasp", 0.5),
            ]
        },
        {},
    )
    subtasks = [_subtask("Reach", (0, 4)), _subtask("Grasp", (5, 11))]

    output = _export(source, [0], _edits(0, removed_frames={1}, subtasks=subtasks))

    persistent, _ = _language(output, 0)[0]
    assert _summary(persistent) == [
        ("plan", "reach then grasp", 0.0),
        ("subtask", "Reach", 0.0),
        ("subtask", "Grasp", float(np.float32(0.4))),
    ]


def test_an_empty_subtask_list_removes_recorded_subtask_rows(source: Path) -> None:
    _add_language(
        source,
        {0: [_row("plan", "reach then grasp", 0.0), _row("subtask", "old reach", 0.0)]},
        {},
    )

    removed = _export(source, [0], _edits(0, subtasks=[]))
    kept = LeRobotExporter(source, source.parents[1] / "kept").export_episodes([0], _edits(0, removed_frames={1}))

    assert _summary(_language(removed, 0)[0][0]) == [("plan", "reach then grasp", 0.0)]
    assert kept.success, kept.error
    kept_rows = _language(source.parents[1] / "kept", 0)[0][0]
    assert _summary(kept_rows) == [("plan", "reach then grasp", 0.0), ("subtask", "old reach", 0.0)]
    assert json.loads((removed / PROVENANCE_FILE).read_text())["episodes"][0]["edits"]["subtasks"] == []
    kept_provenance = json.loads((source.parents[1] / "kept" / PROVENANCE_FILE).read_text())
    assert kept_provenance["episodes"][0]["edits"]["subtasks"] is None


def test_language_instructions_become_task_phrasings_and_a_plan(source: Path) -> None:
    recorded = [_row("task_aug", "old phrasing", 0.0, role="user"), _row("plan", "1. old step", 0.0)]
    _add_language(source, {0: [*recorded, _row("subtask", "Reach", 0.0)]}, {})
    language = LanguageInstruction(
        instruction="Pick up the gear",
        paraphrases=["Grab the gear", " Pick up the gear ", "Lift the gear"],
        subtask_instructions=["Approach the gear", "Grasp it"],
        annotator_id="local",
        saved_at="2026-10-01T12:00:00+00:00",
    )
    output = source.parents[1] / "edited"

    result = LeRobotExporter(source, output).export_episodes([0], _edits(0, removed_frames={0}), language={0: language})

    assert result.success, result.error
    persistent, _ = _language(output, 0)[0]
    assert [(row["style"], row["role"], row["content"], row["timestamp"]) for row in persistent] == [
        ("plan", "assistant", "1. Approach the gear\n2. Grasp it", 0.0),
        ("subtask", "assistant", "Reach", 0.0),
        ("task_aug", "user", "Pick up the gear", 0.0),
        ("task_aug", "user", "Grab the gear", 0.0),
        ("task_aug", "user", "Lift the gear", 0.0),
    ]
    provenance = json.loads((output / PROVENANCE_FILE).read_text())
    assert provenance["episodes"][0]["language_instruction"] == {
        "annotator_id": "local",
        "saved_at": "2026-10-01T12:00:00+00:00",
    }


def test_writes_use_standard_paths_even_when_source_templates_point_elsewhere(source: Path) -> None:
    info = json.loads((source / "meta/info.json").read_text())
    info["data_path"] = "../lerobot/data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
    (source / "meta/info.json").write_text(json.dumps(info))

    output = _export(source, [0])

    assert (output / "data/chunk-000/file-000.parquet").is_file()
    assert not (source.parents[1] / "lerobot").exists()
    assert _info(output)["data_path"] == "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"


def test_variable_length_vectors_interpolate_and_keep_their_list_type(tmp_path: Path) -> None:
    source = write_source(tmp_path / "datasets/capture/lerobot", vector=pa.list_(pa.float32()))
    insertion = [FrameInsertion(after_frame_index=4, interpolation_factor=0.5)]

    output = _export(source, [0], _edits(0, inserted_frames=insertion))

    data = _data(output)
    assert data.schema.field("observation.state").type == pa.list_(pa.float32())
    expected = 0.5 * np.array(frame_state(0, 4)) + 0.5 * np.array(frame_state(0, 5))
    np.testing.assert_allclose(data.column("observation.state")[5].as_py(), expected)
    stats = json.loads((output / "meta/stats.json").read_text())
    assert stats["observation.state"]["count"] == [13]
    assert len(stats["observation.state"]["mean"]) == 3


def test_unrecorded_encoder_settings_fall_back_to_lerobot_defaults(source: Path) -> None:
    info = json.loads((source / "meta/info.json").read_text())
    for key in ("video.g", "video.crf"):
        del info["features"][CAMERA]["info"][key]
    (source / "meta/info.json").write_text(json.dumps(info))

    output = _export(source, [0])

    video_info = _info(output)["features"][CAMERA]["info"]
    assert (video_info["video.g"], video_info["video.crf"]) == (2, 30)
    with av.open(str(_video(output))) as container:
        keyframes = sum(frame.key_frame for frame in container.decode(video=0))
    assert keyframes >= 6


def test_export_root_mode_follows_the_umask_or_the_existing_directory(source: Path, tmp_path: Path) -> None:
    probe = tmp_path / "probe"
    probe.mkdir()

    output = _export(source, [0])

    assert stat.S_IMODE(output.stat().st_mode) == stat.S_IMODE(probe.stat().st_mode)
    existing = source.parents[1] / "existing"
    existing.mkdir()
    existing.chmod(0o750)
    assert LeRobotExporter(source, existing).export_episodes([0]).success
    assert stat.S_IMODE(existing.stat().st_mode) == 0o750


def test_an_existing_empty_directory_is_kept_and_receives_the_export(source: Path) -> None:
    output = source.parents[1] / "edited"
    output.mkdir()
    inode = output.stat().st_ino

    _export(source, [0])

    assert output.stat().st_ino == inode
    assert (output / "meta/info.json").is_file()
    assert [path.name for path in output.iterdir() if path.name.startswith(".")] == []
    assert [path.name for path in source.parents[1].iterdir() if path.name.endswith(".partial")] == []


@pytest.mark.skipif(os.geteuid() == 0, reason="permission bits don't restrict root")
def test_an_existing_directory_in_a_read_only_parent_receives_the_export(source: Path) -> None:
    parent = source.parents[1] / "protected"
    output = parent / "edited"
    output.mkdir(parents=True)
    parent.chmod(0o555)
    try:
        result = LeRobotExporter(source, output).export_episodes([0])
    finally:
        parent.chmod(0o755)

    assert result.success, result.error
    assert (output / "meta/info.json").is_file()


def _break_version(source: Path) -> None:
    info = json.loads((source / "meta/info.json").read_text())
    (source / "meta/info.json").write_text(json.dumps({**info, "codebase_version": "v2.1"}))


def _escape_camera_key(source: Path) -> None:
    info = json.loads((source / "meta/info.json").read_text())
    info["features"]["../escape"] = info["features"].pop(CAMERA)
    (source / "meta/info.json").write_text(json.dumps(info))


def _shorten_video_window(source: Path) -> None:
    path = source / "meta/episodes/chunk-000/file-000.parquet"
    rows = pq.read_table(path).to_pylist()
    rows[0][f"videos/{CAMERA}/to_timestamp"] = 1.0
    pq.write_table(pa.Table.from_pylist(rows), path)


@pytest.mark.parametrize(
    ("prepare", "episodes", "edits", "message"),
    [
        (_break_version, [0], None, "supports v3.0 datasets, not v2.1"),
        (
            None,
            [0, 1],
            _edits(0, global_transform=ImageTransform(resize=ResizeDimensions(width=16, height=12))),
            "different sizes",
        ),
        (
            None,
            [0],
            _edits(0, global_transform=ImageTransform(resize=ResizeDimensions(width=15, height=12))),
            "even width",
        ),
        (_shorten_video_window, [0], None, "video ends after 10 frames, before frame 10"),
        (
            _shorten_video_window,
            [0],
            _edits(0, removed_frames={10, 11}),
            "video window holds 10 frames for 12 data rows",
        ),
        (None, [0, 0], None, "only once"),
        (_escape_camera_key, [0], None, "is not a plain name"),
    ],
)
@pytest.mark.parametrize("existing", [False, True], ids=["new-output", "existing-output"])
def test_failed_exports_leave_nothing_behind(
    source: Path,
    prepare: Any,
    episodes: list[int],
    edits: dict[int, EpisodeEditOperations] | None,
    message: str,
    existing: bool,
) -> None:
    if prepare is not None:
        prepare(source)
    parent = source.parents[1]
    output = parent / "edited"
    if existing:
        output.mkdir()
    before = sorted(path.name for path in parent.iterdir())

    result = LeRobotExporter(source, output).export_episodes(episodes, edits)

    assert result.success is False
    assert message in result.error
    assert sorted(path.name for path in parent.iterdir()) == before
    if existing:
        assert list(output.iterdir()) == []


def test_a_non_empty_output_directory_is_refused(source: Path) -> None:
    output = source.parents[1] / "edited"
    output.mkdir()
    (output / "keep.txt").write_text("existing")

    result = LeRobotExporter(source, output).export_episodes([0])

    assert result.success is False
    assert "new or empty" in result.error
    assert [path.name for path in output.iterdir()] == ["keep.txt"]


def _stage_a_claim(output: Path) -> None:
    staged = output / CLAIM_DIRECTORY / "dataset"
    staged.mkdir(parents=True)
    (staged / "staged.bin").write_bytes(b"staged")


@pytest.mark.parametrize("existing", [False, True], ids=["new-output", "existing-output"])
def test_a_failure_while_moving_into_the_output_leaves_nothing_behind(
    source: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    parent = source.parents[1]
    output = parent / "edited"
    if existing:
        output.mkdir()
    before = sorted(path.name for path in parent.iterdir())
    rename = Path.rename
    moved: list[str] = []

    def fail_second_move(self: Path, target: Path) -> Path:
        if Path(target).parent == output:
            moved.append(self.name)
            if len(moved) == 2:
                raise OSError("simulated rename failure")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_second_move)

    result = LeRobotExporter(source, output).export_episodes([0])

    assert result.success is False
    assert "simulated rename failure" in result.error
    assert sorted(path.name for path in parent.iterdir()) == before
    if existing:
        assert list(output.iterdir()) == []


@pytest.mark.parametrize("meanwhile", ["another export finished", "another export holds the lock"])
def test_an_export_that_loses_the_race_for_an_existing_directory_leaves_it_alone(
    source: Path, monkeypatch: pytest.MonkeyPatch, held_locks: list[int], meanwhile: str
) -> None:
    output = source.parents[1] / "edited"
    output.mkdir()
    video_sizes = LeRobotExporter._video_sizes
    paused = False

    # Both exports pass the emptiness check; the other one acts while this one is paused before it locks.
    def pause_before_locking(self: LeRobotExporter, *args: Any) -> Any:
        nonlocal paused
        if not paused:
            paused = True
            if meanwhile == "another export finished":
                other = LeRobotExporter(source, output, dataset_id="winner").export_episodes([1])
                assert other.success, other.error
            else:
                held_locks.append(hold_lock(output))
                _stage_a_claim(output)
        return video_sizes(self, *args)

    monkeypatch.setattr(LeRobotExporter, "_video_sizes", pause_before_locking)

    result = LeRobotExporter(source, output, dataset_id="loser").export_episodes([0])

    assert paused
    assert result.success is False
    if meanwhile == "another export finished":
        assert "new or empty" in result.error
        assert json.loads((output / PROVENANCE_FILE).read_text())["source"]["dataset_id"] == "winner"
        assert sorted(path.name for path in output.iterdir()) == DATASET_ENTRIES
    else:
        assert "another export is writing to this directory" in result.error
        assert sorted(path.name for path in output.iterdir()) == sorted([CLAIM_DIRECTORY, LOCK_FILE])
        assert (output / CLAIM_DIRECTORY / "dataset/staged.bin").read_bytes() == b"staged"
        assert os.path.samestat(os.stat(output / LOCK_FILE), os.fstat(held_locks[0]))


def _published(output: Path) -> dict[str, str]:
    return {name: digest for name, digest in _digests(output).items() if not name.startswith(".")}


@pytest.mark.parametrize(
    ("stop", "foreign"),
    [
        ("before publishing", False),
        ("after the first move", False),
        (f"after {PROVENANCE_FILE} moved", True),
        ("after meta moved", False),
    ],
    ids=["before-publishing", "after-the-first-move", "with-foreign-content", "after-meta"],
)
def test_the_next_export_cleans_up_after_a_hard_stop(source: Path, stop: str, foreign: bool) -> None:
    output = source.parents[1] / "edited"
    stop_export(source, output, stop)
    assert (output / CLAIM_DIRECTORY).is_dir()
    moved = output / "data"
    if foreign:
        moved_identity = moved.stat()
        (output / "notes.txt").write_text("mine")
        (moved / "keep.txt").write_text("mine too")
        recorded = moved / "chunk-000/file-000.parquet"
        recorded.unlink()
        recorded.write_bytes(b"replaced")
    before = _published(output)

    result = LeRobotExporter(source, output, dataset_id="retry").export_episodes([1])

    names = sorted(path.name for path in output.iterdir())
    if foreign:
        assert result.success is False
        assert "new or empty" in result.error
        assert names == ["data", "notes.txt"]
        assert os.path.samestat(moved.stat(), moved_identity)
        assert (output / "notes.txt").read_text() == "mine"
        assert (moved / "keep.txt").read_text() == "mine too"
        assert (moved / "chunk-000/file-000.parquet").read_bytes() == b"replaced"
    elif stop == "after meta moved":
        assert result.success is False
        assert "new or empty" in result.error
        assert names == DATASET_ENTRIES
        assert _published(output) == before
        assert json.loads((output / PROVENANCE_FILE).read_text())["source"]["dataset_id"] == "stopped"
    else:
        assert result.success, result.error
        assert names == DATASET_ENTRIES
        assert json.loads((output / PROVENANCE_FILE).read_text())["source"]["dataset_id"] == "retry"
        assert _data(output).num_rows == LENGTHS[1]


def test_publication_moves_meta_last(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = source.parents[1] / "edited"
    rename = Path.rename
    moved: list[str] = []

    def record_moves(self: Path, target: Path) -> Path:
        if Path(target).parent == output:
            moved.append(Path(target).name)
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", record_moves)

    _export(source, [0])

    assert moved[-1] == "meta"
    assert sorted(moved) == DATASET_ENTRIES


@pytest.mark.parametrize("peer", [False, True], ids=["alone", "peer-holds-the-lock"])
@pytest.mark.parametrize("existing", [False, True], ids=["new-output", "existing-output"])
def test_an_export_that_cannot_take_the_lock_removes_nothing(
    source: Path, monkeypatch: pytest.MonkeyPatch, held_locks: list[int], existing: bool, peer: bool
) -> None:
    output = source.parents[1] / "edited"
    if existing:
        output.mkdir()
    try_lock = lerobot_exporter._try_lock
    seen: dict[str, os.stat_result] = {}

    # Export A created the lock file; a peer may lock that same file and start staging before A's attempt fails.
    def fail_to_lock(fd: int) -> bool:
        monkeypatch.setattr(lerobot_exporter, "_try_lock", try_lock)
        if peer:
            held_locks.append(hold_lock(output))
            _stage_a_claim(output)
            seen["output"] = output.stat()
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(lerobot_exporter, "_try_lock", fail_to_lock)

    failed = LeRobotExporter(source, output, dataset_id="a").export_episodes([0])

    assert failed.success is False
    assert "can't be locked" in failed.error
    if peer:
        assert os.path.samestat(os.stat(output / LOCK_FILE), os.fstat(held_locks[0]))
        assert os.path.samestat(output.stat(), seen["output"])
        assert (output / CLAIM_DIRECTORY / "dataset/staged.bin").read_bytes() == b"staged"
        excluded = LeRobotExporter(source, output, dataset_id="c").export_episodes([0])
        assert excluded.success is False
        assert "another export is writing to this directory" in excluded.error
        os.close(held_locks.pop())
    else:
        assert [path.name for path in output.iterdir()] == [LOCK_FILE]

    retried = LeRobotExporter(source, output, dataset_id="c").export_episodes([0])

    assert retried.success, retried.error
    assert sorted(path.name for path in output.iterdir()) == DATASET_ENTRIES


@pytest.mark.parametrize("held", [False, True], ids=["replacement-free", "replacement-held"])
def test_an_export_whose_lock_file_is_replaced_locks_the_current_one(
    source: Path, monkeypatch: pytest.MonkeyPatch, held_locks: list[int], held: bool
) -> None:
    output = source.parents[1] / "edited"
    output.mkdir()
    try_lock = lerobot_exporter._try_lock

    # The previous holder removes the lock file after this export opened it; another export may lock a new one.
    def replace_then_lock(fd: int) -> bool:
        monkeypatch.setattr(lerobot_exporter, "_try_lock", try_lock)
        (output / LOCK_FILE).unlink()
        if held:
            held_locks.append(hold_lock(output))
        return try_lock(fd)

    monkeypatch.setattr(lerobot_exporter, "_try_lock", replace_then_lock)

    result = LeRobotExporter(source, output).export_episodes([0])

    if held:
        assert result.success is False
        assert "another export is writing to this directory" in result.error
        assert [path.name for path in output.iterdir()] == [LOCK_FILE]
        assert os.path.samestat(os.stat(output / LOCK_FILE), os.fstat(held_locks[0]))
    else:
        assert result.success, result.error
        assert sorted(path.name for path in output.iterdir()) == DATASET_ENTRIES


@pytest.mark.parametrize("failure", ["rollback", "recovery", "unreadable"])
def test_an_incomplete_rollback_or_recovery_keeps_the_claim_for_the_next_export(
    source: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    output = source.parents[1] / "edited"
    moved = output / "data"
    if failure in ("rollback", "unreadable"):
        rename, unlink, lstat = Path.rename, Path.unlink, Path.lstat
        moves: list[str] = []

        # The second move fails after `data` has moved; an unreadable `data` can't be checked from then on.
        def fail_second_move(self: Path, target: Path) -> Path:
            if Path(target).parent == output:
                moves.append(self.name)
                if len(moves) == 2:
                    raise OSError("simulated rename failure")
            return rename(self, target)

        def refuse_to_unlink_moved_files(self: Path, missing_ok: bool = False) -> None:
            if self.is_relative_to(moved):
                raise PermissionError(errno.EACCES, "simulated permission error", str(self))
            unlink(self, missing_ok=missing_ok)

        def refuse_to_read_the_moved_folder(self: Path) -> os.stat_result:
            if len(moves) == 2 and self == moved:
                raise PermissionError(errno.EACCES, "simulated permission error", str(self))
            return lstat(self)

        monkeypatch.setattr(Path, "rename", fail_second_move)
        if failure == "rollback":
            monkeypatch.setattr(Path, "unlink", refuse_to_unlink_moved_files)
        else:
            monkeypatch.setattr(Path, "lstat", refuse_to_read_the_moved_folder)
    else:
        stop_export(source, output, "after the first move")
        rmdir = Path.rmdir

        def refuse_to_remove_the_moved_folder(self: Path) -> None:
            if self == moved:
                raise PermissionError(errno.EACCES, "simulated permission error", str(self))
            rmdir(self)

        monkeypatch.setattr(Path, "rmdir", refuse_to_remove_the_moved_folder)

    failed = LeRobotExporter(source, output, dataset_id="first").export_episodes([0])

    assert failed.success is False
    assert (output / CLAIM_DIRECTORY / "publication.json").is_file()
    assert moved.is_dir()
    monkeypatch.undo()

    retried = LeRobotExporter(source, output, dataset_id="retry").export_episodes([1])

    assert retried.success, retried.error
    assert sorted(path.name for path in output.iterdir()) == DATASET_ENTRIES


def test_an_error_while_checking_the_lock_releases_it(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = source.parents[1] / "edited"
    real_os = lerobot_exporter.os

    class FailingLockCheck:
        """The exporter's ``os`` with an I/O error when it checks the lock file's identity."""

        def __getattr__(self, name: str) -> Any:
            return getattr(real_os, name)

        def stat(self, path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
            if Path(path).name == LOCK_FILE:
                raise OSError(errno.EIO, "simulated I/O error")
            return real_os.stat(path, *args, **kwargs)

    monkeypatch.setattr(lerobot_exporter, "os", FailingLockCheck())

    failed = LeRobotExporter(source, output).export_episodes([0])

    assert failed.success is False
    assert "can't be locked" in failed.error
    monkeypatch.undo()

    retried = LeRobotExporter(source, output).export_episodes([0])

    assert retried.success, retried.error


@pytest.mark.parametrize(
    ("refused", "outcome"),
    [(LOCK_FILE, "success"), (LOCK_FILE, "failure"), ("publication.json", "success")],
)
def test_a_cleanup_error_keeps_the_exports_own_result(
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    held_locks: list[int],
    refused: str,
    outcome: str,
) -> None:
    output = source.parents[1] / "edited"
    unlink = os.unlink

    def refuse_to_unlink(path: str | os.PathLike[str], *args: Any, **kwargs: Any) -> None:
        if os.path.basename(path) == refused:
            raise PermissionError(errno.EACCES, "simulated permission error", str(path))
        unlink(path, *args, **kwargs)

    def fail_to_write(self: LeRobotExporter, *args: Any) -> None:
        raise LeRobotExportError("simulated write failure")

    monkeypatch.setattr(os, "unlink", refuse_to_unlink)
    if outcome == "failure":
        monkeypatch.setattr(LeRobotExporter, "_write", fail_to_write)
    caplog.set_level(logging.WARNING, logger=lerobot_exporter.__name__)

    result = LeRobotExporter(source, output).export_episodes([0])

    if outcome == "success":
        assert result.success, result.error
    else:
        assert result.success is False
        assert "simulated write failure" in result.error
    refusals = [
        record.exc_info[1]
        for record in caplog.records
        if record.exc_info and isinstance(record.exc_info[1], PermissionError)
    ]
    assert [os.path.basename(error.filename) for error in refusals] == [refused]
    held_locks.append(hold_lock(output))
