"""Unit tests for the generic VLM dataset labeling script helpers.

These cover the pure functions (prompt building, JSON parsing, coercion,
summarization, view resolution) without loading any model or GPU.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

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


@pytest.mark.parametrize("payload", [{}, {"object": "cube"}, {"grasp_success": "true"}])
def test_given_malformed_task_result_when_normalized_then_rejected(mod: ModuleType, payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"label|result"):
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


@pytest.mark.asyncio
async def test_given_partial_analysis_when_conditionally_saved_then_preserves_omitted_fields(tmp_path: Path) -> None:
    from evaluation.vlm_judge.curation import DatasetLabelsFile, EpisodeAnalysisRecord
    from evaluation.vlm_judge.curation_storage import LocalCurationStorage

    storage = LocalCurationStorage({"owner--dataset": tmp_path})
    labels = DatasetLabelsFile(dataset_id="owner--dataset")
    labels.apply_analysis(0, EpisodeAnalysisRecord(notes="Human note", smoothness=0.8, object="Old"), author_id="human")
    revision = await storage.save("owner--dataset", labels, if_none_match=True)
    loaded = await storage.load_versioned("owner--dataset")
    loaded.value.apply_analysis(0, EpisodeAnalysisRecord(object=None), author_id="human")
    await storage.save("owner--dataset", loaded.value, if_match=revision)

    record = (await storage.load_versioned("owner--dataset")).value.analysis["0"]
    assert record.notes == "Human note"
    assert record.smoothness == 0.8
    assert record.object is None


@pytest.mark.asyncio
async def test_given_concurrent_human_save_when_cli_storage_publishes_then_rejects_stale_analysis(
    tmp_path: Path,
) -> None:
    from evaluation.vlm_judge.curation import DatasetLabelsFile, EpisodeAnalysisRecord
    from evaluation.vlm_judge.curation_storage import LocalCurationStorage, RevisionConflictError

    storage = LocalCurationStorage({"dataset": tmp_path})
    labels = DatasetLabelsFile(dataset_id="dataset")
    revision = await storage.save("dataset", labels, if_none_match=True)
    concurrent = labels.model_copy(deep=True)
    concurrent.episodes["0"] = ["HUMAN"]
    await storage.save("dataset", concurrent, if_match=revision)
    labels.apply_analysis(0, EpisodeAnalysisRecord(object="Synthetic"), author_id="human")

    with pytest.raises(RevisionConflictError):
        await storage.save("dataset", labels, if_match=revision)
    saved = (await storage.load_versioned("dataset")).value
    assert saved.episodes == {"0": ["HUMAN"]}
    assert saved.analysis == {}


def test_given_resume_without_job_when_task_cli_submits_then_rejected(
    mod: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "dataset"
    _write_min_dataset(root, ["obs.front"])
    (root / "meta" / "episodes.jsonl").write_text(
        json.dumps({"episode_index": 3, "length": 30, "tasks": ["Move cube"]}) + "\n"
    )
    video = root / "videos" / "chunk-000" / "obs.front" / "episode_000003.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"synthetic-media-generation")

    code = mod.main(
        ["--dataset-root", str(root), "--job-dir", str(tmp_path / "jobs"), "--single", "--resume", "--detach"]
    )

    assert code == 2
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "jobs").exists()


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


def test_given_task_failure_when_cli_resumes_then_same_job_retries_with_safe_diagnostics(
    mod: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "dataset"
    _write_min_dataset(root, ["obs.front"])
    (root / "meta" / "episodes.jsonl").write_text(
        json.dumps({"episode_index": 3, "length": 30, "tasks": ["Private instruction"]}) + "\n"
    )
    video = root / "videos" / "chunk-000" / "obs.front" / "episode_000003.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"synthetic-media-generation")
    attempts = []

    class FakeBackend:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate(self, **kwargs: object) -> str:
            attempts.append(kwargs["user_prompt"])
            if len(attempts) == 1:
                raise RuntimeError("Private provider payload")
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
    monkeypatch.setattr(mod, "build_filmstrip", lambda *args, **kwargs: [object()])
    args = [
        "--dataset-root",
        str(root),
        "--job-dir",
        str(tmp_path / "jobs"),
        "--output-dir",
        str(tmp_path / "output"),
        "--single",
    ]

    assert mod.main(args) == 1
    failed = json.loads(capsys.readouterr().out)
    assert failed["status"] == "failed"
    assert "category=RuntimeError" in caplog.text
    assert "Private" not in caplog.text
    assert mod.main([*args, "--resume", "--job-id", failed["id"]]) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["id"] == failed["id"]
    assert completed["retry_count"] == 1
    assert completed["status"] == "succeeded"
    assert len(attempts) == 2
