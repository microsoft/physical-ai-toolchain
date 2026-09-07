"""Unit tests for the generic VLM dataset labeling script helpers.

These cover the pure functions (prompt building, JSON parsing, coercion,
summarization, view resolution) without loading any model or GPU.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "vlm_label_dataset.py"


@pytest.fixture(scope="session")
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vlm_label_dataset", _SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_label_extracts_plain_json(mod: ModuleType) -> None:
    result = mod.parse_label('{"pick_from": "front", "grasp_success": true}')
    assert result == {"pick_from": "front", "grasp_success": True}


def test_parse_label_strips_code_fences(mod: ModuleType) -> None:
    text = '```json\n{"object": "red cube"}\n```'
    assert mod.parse_label(text) == {"object": "red cube"}


def test_parse_label_raises_without_json(mod: ModuleType) -> None:
    with pytest.raises(ValueError, match="No JSON object"):
        mod.parse_label("the model refused to answer")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("No", False),
        ("yes", True),
        ("maybe", None),
        (None, None),
    ],
)
def test_as_bool_tristate(mod: ModuleType, value: object, expected: bool | None) -> None:
    assert mod.as_bool(value) is expected


def test_build_user_prompt_lists_multiple_views_and_instruction(mod: ModuleType) -> None:
    prompt = mod.build_user_prompt(
        n_frames=12,
        views=["observation.images.front", "observation.images.wrist"],
        instruction="Pick up the cube",
    )
    assert "12 images" in prompt
    assert "observation.images.front" in prompt
    assert "observation.images.wrist" in prompt
    assert "Pick up the cube" in prompt


def test_build_user_prompt_handles_single_view_without_instruction(mod: ModuleType) -> None:
    prompt = mod.build_user_prompt(n_frames=8, views=["cam"], instruction=None)
    assert "single camera view: cam" in prompt
    assert "pick-and-place" in prompt


def test_build_user_prompt_injects_scene_context(mod: ModuleType) -> None:
    prompt = mod.build_user_prompt(
        n_frames=8,
        views=["cam"],
        instruction=None,
        scene_context="Two bins: one in front, one to the right.",
    )
    assert "Two bins: one in front, one to the right." in prompt


def test_row_from_label_coerces_and_normalizes(mod: ModuleType) -> None:
    row = mod._row_from_label(
        {
            "pick_from": "FRONT",
            "object": "black cloth",
            "grasp_success": "true",
            "place_success": False,
            "movement_quality": "Smooth.",
        }
    )
    assert row["pick_from"] == "front"
    assert row["grasp_success"] is True
    assert row["place_success"] is False
    assert row["notes"] == ""
    assert row["error"] is None


def test_summarize_counts_outcomes(mod: ModuleType) -> None:
    rows = [
        {"grasp_success": True, "place_success": True, "error": None},
        {"grasp_success": True, "place_success": False, "error": None},
        {"grasp_success": None, "place_success": None, "error": "Boom"},
    ]
    summary = mod.summarize(rows)
    assert summary == {"labeled": 2, "total": 3, "errors": 1, "grasp_success": 2, "place_success": 1}


def _write_min_dataset(root: Path, views: list[str]) -> None:
    (root / "meta").mkdir(parents=True)
    features = {v: {"dtype": "video", "shape": [48, 64, 3], "videos_path": f"videos/{v}"} for v in views}
    info = {
        "codebase_version": "v2.1",
        "fps": 30,
        "total_episodes": 0,
        "chunks_size": 1000,
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "features": features,
    }
    (root / "meta" / "info.json").write_text(json.dumps(info))


def test_resolve_views_defaults_to_all(mod: ModuleType, tmp_path: Path) -> None:
    _write_min_dataset(tmp_path, ["obs.front", "obs.wrist"])
    assert set(mod.resolve_views(tmp_path, None)) == {"obs.front", "obs.wrist"}


def test_resolve_views_rejects_unknown(mod: ModuleType, tmp_path: Path) -> None:
    _write_min_dataset(tmp_path, ["obs.front"])
    with pytest.raises(ValueError, match="not in dataset"):
        mod.resolve_views(tmp_path, ["obs.missing"])


def test_label_dataset_writes_success_error_and_analysis_records(
    mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "dataset"
    output_dir = tmp_path / "output"
    _write_min_dataset(dataset_root, ["obs.front"])
    labels_path = dataset_root / "meta" / "episode_labels.json"
    labels_path.write_text(
        json.dumps(
            {
                "dataset_id": "dataset",
                "available_labels": ["SUCCESS"],
                "episodes": {"9": ["SUCCESS"]},
                "analysis": {
                    "0": {"motion_score": 4, "motion_flags": ["hesitant"]},
                    "9": {"object": "existing"},
                },
            }
        )
    )
    records = [
        SimpleNamespace(
            episode_index=0,
            episode_id="episode_000000",
            instruction="Pick the cube",
            duration_s=1.25,
        ),
        SimpleNamespace(
            episode_index=1,
            episode_id="episode_000001",
            instruction="Pick the sphere",
            duration_s=2.5,
        ),
    ]

    class FakeBackend:
        def __init__(self, **_kwargs: object) -> None:
            self.calls = 0

        def generate(self, **_kwargs: object) -> str:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("inference failed")
            return json.dumps(
                {
                    "pick_from": "front",
                    "object": "red cube",
                    "grasp_success": True,
                    "place_success": False,
                    "movement_quality": "Smooth approach.",
                    "notes": "Placement missed.",
                }
            )

    monkeypatch.setattr(mod, "Qwen3VLBackend", FakeBackend)
    monkeypatch.setattr(mod, "resolve_views", lambda *_args, **_kwargs: ("obs.front",))
    monkeypatch.setattr(mod, "iter_episodes", lambda *_args, **_kwargs: iter(records))
    monkeypatch.setattr(mod, "build_filmstrip", lambda *_args, **_kwargs: [object()])

    summary = mod.label_dataset(
        dataset_root=dataset_root,
        output_dir=output_dir,
        views=None,
        n_frames=4,
        frame_size=64,
        model_id="fake/model",
        device_map="cpu",
        dtype="float32",
        limit=None,
        write_analysis=True,
    )

    rows = [json.loads(line) for line in (output_dir / "labels.jsonl").read_text().splitlines()]
    with (output_dir / "labels.csv").open(newline="") as csv_file:
        csv_rows = list(csv.DictReader(csv_file))
    labels = json.loads(labels_path.read_text())

    assert summary == {"labeled": 1, "total": 2, "errors": 1, "grasp_success": 1, "place_success": 0}
    assert rows[0]["object"] == "red cube"
    assert rows[1]["error"] == "RuntimeError: inference failed"
    assert len(csv_rows) == 2
    assert labels["episodes"] == {"9": ["SUCCESS"]}
    assert labels["analysis"]["9"] == {"object": "existing"}
    assert labels["analysis"]["0"]["motion_score"] == 4
    assert labels["analysis"]["0"]["motion_flags"] == ["hesitant"]
    assert labels["analysis"]["0"] == {
        "pick_from": "front",
        "object": "red cube",
        "grasp_success": True,
        "place_success": False,
        "movement_quality": "Smooth approach.",
        "notes": "Placement missed.",
        "instruction": "Pick the cube",
        "duration_s": 1.25,
        "source": "fake/model",
        "motion_score": 4,
        "motion_flags": ["hesitant"],
    }
    assert "1" not in labels["analysis"]


def test_write_analysis_records_uses_explicit_nested_dataset_id(mod: ModuleType, tmp_path: Path) -> None:
    dataset_root = tmp_path / "owner" / "dataset"
    (dataset_root / "meta").mkdir(parents=True)
    rows = [
        {
            "episode_index": 0,
            "instruction": "Pick the cube",
            "duration_s": 1.0,
            "pick_from": "front",
            "object": "cube",
            "grasp_success": True,
            "place_success": True,
            "movement_quality": "Smooth.",
            "notes": "",
            "error": None,
        }
    ]

    mod._write_analysis_records(dataset_root, rows, "fake/model", "owner--dataset")

    labels = json.loads((dataset_root / "meta" / "episode_labels.json").read_text())
    assert labels["dataset_id"] == "owner--dataset"


def test_write_analysis_records_preserves_existing_nested_dataset_id(mod: ModuleType, tmp_path: Path) -> None:
    dataset_root = tmp_path / "owner" / "dataset"
    labels_path = dataset_root / "meta" / "episode_labels.json"
    labels_path.parent.mkdir(parents=True)
    labels_path.write_text(
        json.dumps(
            {
                "dataset_id": "owner--dataset",
                "available_labels": ["SUCCESS"],
                "episodes": {},
                "analysis": {},
            }
        )
    )
    rows = [
        {
            "episode_index": 0,
            "instruction": "Pick the cube",
            "duration_s": 1.0,
            "pick_from": "front",
            "object": "cube",
            "grasp_success": True,
            "place_success": True,
            "movement_quality": "Smooth.",
            "notes": "",
            "error": None,
        }
    ]

    mod._write_analysis_records(dataset_root, rows, "fake/model")

    labels = json.loads(labels_path.read_text())
    assert labels["dataset_id"] == "owner--dataset"


def test_label_dataset_resumes_completed_episode_without_duplication(
    mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "dataset"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_min_dataset(dataset_root, ["obs.front"])
    completed = {
        "episode_index": 0,
        "episode_id": "episode_000000",
        "instruction": "Pick the cube",
        "duration_s": 1.0,
        "pick_from": "front",
        "object": "cube",
        "grasp_success": True,
        "place_success": True,
        "movement_quality": "Smooth.",
        "notes": "",
        "error": None,
    }
    (output_dir / "labels.jsonl").write_text(json.dumps(completed) + "\n")
    records = [
        SimpleNamespace(episode_index=0, episode_id="episode_000000", instruction="Pick the cube", duration_s=1.0),
        SimpleNamespace(episode_index=1, episode_id="episode_000001", instruction="Pick the ball", duration_s=1.0),
    ]
    generated: list[str] = []

    class FakeBackend:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def generate(self, **_kwargs: object) -> str:
            generated.append("called")
            return json.dumps(
                {
                    "pick_from": "left",
                    "object": "ball",
                    "grasp_success": True,
                    "place_success": True,
                    "movement_quality": "Smooth.",
                    "notes": "",
                }
            )

    monkeypatch.setattr(mod, "Qwen3VLBackend", FakeBackend)
    monkeypatch.setattr(mod, "resolve_views", lambda *_args, **_kwargs: ("obs.front",))
    monkeypatch.setattr(mod, "iter_episodes", lambda *_args, **_kwargs: iter(records))
    monkeypatch.setattr(mod, "build_filmstrip", lambda *_args, **_kwargs: [object()])

    summary = mod.label_dataset(
        dataset_root=dataset_root,
        output_dir=output_dir,
        views=None,
        n_frames=4,
        frame_size=64,
        model_id="fake/model",
        device_map="cpu",
        dtype="float32",
        limit=None,
        resume=True,
    )

    rows = [json.loads(line) for line in (output_dir / "labels.jsonl").read_text().splitlines()]

    assert generated == ["called"]
    assert [row["episode_index"] for row in rows] == [0, 1]
    assert summary == {"labeled": 2, "total": 2, "errors": 0, "grasp_success": 2, "place_success": 2}


def test_label_dataset_resume_retries_error_rows_and_repairs_torn_tail(
    mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "dataset"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _write_min_dataset(dataset_root, ["obs.front"])
    failed = {
        "episode_index": 0,
        "episode_id": "episode_000000",
        "instruction": "Pick the cube",
        "duration_s": 1.0,
        "pick_from": None,
        "object": None,
        "grasp_success": None,
        "place_success": None,
        "movement_quality": None,
        "notes": None,
        "error": "RuntimeError: temporary failure",
    }
    jsonl_path = output_dir / "labels.jsonl"
    jsonl_path.write_text(json.dumps(failed) + '\n{"episode_index": 99')
    records = [
        SimpleNamespace(episode_index=0, episode_id="episode_000000", instruction="Pick the cube", duration_s=1.0),
    ]

    class FakeBackend:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def generate(self, **_kwargs: object) -> str:
            return json.dumps(
                {
                    "pick_from": "front",
                    "object": "cube",
                    "grasp_success": True,
                    "place_success": True,
                    "movement_quality": "Smooth.",
                    "notes": "",
                }
            )

    monkeypatch.setattr(mod, "Qwen3VLBackend", FakeBackend)
    monkeypatch.setattr(mod, "resolve_views", lambda *_args, **_kwargs: ("obs.front",))
    monkeypatch.setattr(mod, "iter_episodes", lambda *_args, **_kwargs: iter(records))
    monkeypatch.setattr(mod, "build_filmstrip", lambda *_args, **_kwargs: [object()])

    summary = mod.label_dataset(
        dataset_root=dataset_root,
        output_dir=output_dir,
        views=None,
        n_frames=4,
        frame_size=64,
        model_id="fake/model",
        device_map="cpu",
        dtype="float32",
        limit=None,
        resume=True,
    )

    rows = [json.loads(line) for line in jsonl_path.read_text().splitlines()]

    assert [row["episode_index"] for row in rows] == [0, 0]
    assert rows[-1]["error"] is None
    assert summary == {"labeled": 1, "total": 1, "errors": 0, "grasp_success": 1, "place_success": 1}
