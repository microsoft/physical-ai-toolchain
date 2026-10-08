"""Unit tests for the generic VLM dataset labeling script helpers.

These cover the pure functions (prompt building, JSON parsing, coercion,
summarization, view resolution) without loading any model or GPU.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from evaluation.vlm_judge.dataset import EpisodeRecord

from src.api.storage.base import RevisionConflictError

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "vlm_label_dataset.py"


def _episode(episode_index: int, instruction: str, duration_s: float = 1.0) -> EpisodeRecord:
    return EpisodeRecord(f"episode_{episode_index:06d}", episode_index, instruction, 30, 30, {}, 0, duration_s)


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


@pytest.mark.parametrize("payload", [{}, {"object": "cube"}, {"grasp_success": "true"}])
def test_given_malformed_task_result_when_normalized_then_rejected(mod: ModuleType, payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="label|result"):
        mod._row_from_label(payload)


def test_given_camera_windows_when_filmstrip_built_then_each_window_preserved(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from evaluation.vlm_judge.dataset import EpisodeRecord

    record = EpisodeRecord(
        "synthetic/3",
        3,
        "Saved instruction",
        30,
        30,
        {"front": tmp_path / "front.mp4", "wrist": tmp_path / "wrist.mp4"},
        1,
        2,
        video_windows={"front": (10, 11), "wrist": (20, 22)},
    )
    windows = []

    def extract(window: object, **kwargs: object) -> list[object]:
        windows.append((window.from_s, window.to_s))
        return [object()]

    monkeypatch.setattr(mod, "extract_frames", extract)
    monkeypatch.setattr(mod, "tile_horizontally", lambda frames: frames[0])

    mod.build_filmstrip(record, views=("front", "wrist"), n_frames=4, frame_size=64)

    assert windows == [(10, 11), (20, 22)]


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


def test_given_partial_analysis_when_cli_merges_then_preserves_omitted_fields(
    mod: ModuleType,
    tmp_path: Path,
) -> None:
    path = tmp_path / "meta" / "episode_labels.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "dataset_id": "dataset",
                "analysis": {"0": {"notes": "Human note", "motion_score": 4, "object": "Old"}},
            }
        ),
        encoding="utf-8",
    )

    mod._write_analysis_records(tmp_path, [{"episode_index": 0, "object": None}], "synthetic")

    record = json.loads(path.read_text())["analysis"]["0"]
    assert record["notes"] == "Human note"
    assert record["motion_score"] == 4
    assert record["object"] is None


def test_given_concurrent_human_save_when_cli_publishes_then_rejects_stale_analysis(
    mod: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "meta" / "episode_labels.json"
    path.parent.mkdir()
    original = {"dataset_id": "dataset", "episodes": {"0": ["SUCCESS"]}, "analysis": {}}
    path.write_text(json.dumps(original), encoding="utf-8")
    read_bytes = Path.read_bytes
    concurrent = {**original, "episodes": {"0": ["HUMAN"]}}

    def interleaved_read(target: Path) -> bytes:
        content = read_bytes(target)
        if target == path:
            target.write_text(json.dumps(concurrent), encoding="utf-8")
        return content

    monkeypatch.setattr(Path, "read_bytes", interleaved_read)
    with pytest.raises(RevisionConflictError):
        mod._write_analysis_records(tmp_path, [{"episode_index": 0, "object": "Synthetic"}], "synthetic")

    assert json.loads(read_bytes(path)) == concurrent
    assert "revision conflict" in caplog.text


def test_label_dataset_normalizes_rows_and_preserves_nested_dataset_id(
    mod: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "owner" / "dataset"
    output_dir = tmp_path / "output"
    _write_min_dataset(dataset_root, ["obs.front"])
    labels_path = dataset_root / "meta" / "episode_labels.json"
    labels_path.write_text(
        json.dumps(
            {
                "dataset_id": "owner--dataset",
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
        _episode(0, "Pick the cube", 1.25),
        _episode(1, "Pick the sphere", 2.5),
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
                    "pick_from": "FRONT",
                    "object": "red cube",
                    "grasp_success": True,
                    "place_success": False,
                    "movement_quality": "Smooth approach.",
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
        dataset_id=None,
    )

    rows = [json.loads(line) for line in (output_dir / "labels.jsonl").read_text().splitlines()]
    with (output_dir / "labels.csv").open(newline="") as csv_file:
        csv_rows = list(csv.DictReader(csv_file))
    labels = json.loads(labels_path.read_text())

    assert summary == {"labeled": 1, "total": 2, "errors": 1, "grasp_success": 1, "place_success": 0}
    assert rows[0] == {
        "input_key": rows[0]["input_key"],
        "snapshot_id": rows[0]["snapshot_id"],
        "episode_index": 0,
        "episode_id": "episode_000000",
        "instruction": "Pick the cube",
        "duration_s": 1.25,
        "source": "fake/model",
        "pick_from": "front",
        "object": "red cube",
        "grasp_success": True,
        "place_success": False,
        "movement_quality": "Smooth approach.",
        "notes": "",
        "error": None,
    }
    assert rows[1]["error"] == "RuntimeError"
    assert len(csv_rows) == 2
    assert labels["episodes"] == {"9": ["SUCCESS"]}
    assert labels["dataset_id"] == "owner--dataset"
    assert labels["analysis"]["9"] == {"object": "existing"}
    assert labels["analysis"]["0"]["motion_score"] == 4
    assert labels["analysis"]["0"]["motion_flags"] == ["hesitant"]
    assert labels["analysis"]["0"] == {
        "pick_from": "front",
        "object": "red cube",
        "grasp_success": True,
        "place_success": False,
        "movement_quality": "Smooth approach.",
        "notes": "",
        "instruction": "Pick the cube",
        "duration_s": 1.25,
        "source": "fake/model",
        "motion_score": 4,
        "motion_flags": ["hesitant"],
    }
    assert "1" not in labels["analysis"]


def test_label_dataset_reexecutes_legacy_results_without_input_identity(
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
        _episode(0, "Pick the cube"),
        _episode(1, "Pick the ball"),
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

    assert generated == ["called", "called"]
    assert [row["episode_index"] for row in rows] == [0, 0, 1]
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
        _episode(0, "Pick the cube"),
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


def test_given_resumed_task_run_when_inputs_change_then_only_exact_identity_skipped(
    mod: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_min_dataset(tmp_path / "dataset", ["obs.front"])
    root = tmp_path / "dataset"
    generated = []

    class FakeBackend:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate(self, **kwargs: object) -> str:
            generated.append(kwargs["user_prompt"])
            return json.dumps(
                {
                    "pick_from": "table",
                    "object": "cube",
                    "grasp_success": False,
                    "place_success": False,
                    "movement_quality": "Incomplete",
                    "notes": "",
                }
            )

    monkeypatch.setattr(mod, "Qwen3VLBackend", FakeBackend)
    monkeypatch.setattr(mod, "iter_episodes", lambda *args, **kwargs: iter([_episode(1005, "Original instruction")]))
    monkeypatch.setattr(mod, "build_filmstrip", lambda *args, **kwargs: [object()])
    options = {
        "dataset_root": root,
        "output_dir": tmp_path / "output",
        "views": None,
        "n_frames": 4,
        "frame_size": 64,
        "model_id": "fake/model",
        "device_map": "cpu",
        "dtype": "float32",
        "limit": None,
        "resume": True,
    }

    mod.label_dataset(**options)
    mod.label_dataset(**options)
    assert len(generated) == 1
    mod.label_dataset(**{**options, "n_frames": 8})
    assert len(generated) == 2
    annotation = root / "annotations" / "episodes" / "episode_001005.json"
    annotation.parent.mkdir(parents=True)
    annotation.write_text(
        json.dumps(
            {
                "annotations": [
                    {"annotator_id": "human", "language_instruction": {"instruction": "Saved new instruction"}}
                ]
            }
        )
    )
    mod.label_dataset(**{**options, "n_frames": 8})
    assert len(generated) == 3
    assert "Saved new instruction" in generated[-1]


def test_given_task_cli_when_detached_then_backend_is_lazy_and_saved_job_resumes(
    mod: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "dataset"
    _write_min_dataset(root, ["obs.front"])
    (root / "meta" / "episodes.jsonl").write_text(
        json.dumps({"episode_index": 3, "length": 30, "tasks": ["Move cube"]}) + "\n"
    )
    (root / "meta" / "tasks.jsonl").write_text(json.dumps({"task_index": 0, "task": "Move cube"}) + "\n")
    video = root / "videos" / "chunk-000" / "obs.front" / "episode_000003.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"synthetic-media-generation")
    constructions = []

    class FakeBackend:
        def __init__(self, **kwargs: object) -> None:
            constructions.append("constructed")

        def generate(self, **kwargs: object) -> str:
            return json.dumps(
                {
                    "pick_from": "table",
                    "object": "cube",
                    "grasp_success": True,
                    "place_success": False,
                    "movement_quality": "One pause",
                    "notes": "",
                }
            )

    monkeypatch.setattr(mod, "Qwen3VLBackend", FakeBackend)
    monkeypatch.setattr(mod, "build_filmstrip", lambda *args, **kwargs: [object()])
    args = [
        "--dataset-root",
        str(root),
        "--output-dir",
        str(tmp_path / "output"),
        "--job-dir",
        str(tmp_path / "jobs"),
        "--single",
        "--request-id",
        "task-cli",
    ]
    assert mod.main([*args, "--detach"]) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["status"] == "queued"
    assert constructions == []
    assert mod.main(args) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["id"] == accepted["id"]
    assert completed["status"] == "succeeded"
    assert completed["result_kind"] == "task-findings"
    assert constructions == ["constructed"]
    row = json.loads((tmp_path / "output" / "labels.jsonl").read_text())
    assert row["instruction"] == "Move cube"
    assert row["grasp_success"] is True
    assert not (root / "meta" / "episode_labels.json").exists()
