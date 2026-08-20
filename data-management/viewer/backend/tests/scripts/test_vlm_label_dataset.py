"""Unit tests for the generic VLM dataset labeling script helpers.

These cover the pure functions (prompt building, JSON parsing, coercion,
summarization, view resolution) without loading any model or GPU.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "vlm_label_dataset.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("vlm_label_dataset", _SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def test_parse_label_extracts_plain_json() -> None:
    result = mod.parse_label('{"pick_from": "front", "grasp_success": true}')
    assert result == {"pick_from": "front", "grasp_success": True}


def test_parse_label_strips_code_fences() -> None:
    text = '```json\n{"object": "red cube"}\n```'
    assert mod.parse_label(text) == {"object": "red cube"}


def test_parse_label_raises_without_json() -> None:
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
def test_as_bool_tristate(value: object, expected: bool | None) -> None:
    assert mod.as_bool(value) is expected


def test_build_user_prompt_lists_multiple_views_and_instruction() -> None:
    prompt = mod.build_user_prompt(
        n_frames=12,
        views=["observation.images.front", "observation.images.wrist"],
        instruction="Pick up the cube",
    )
    assert "12 images" in prompt
    assert "observation.images.front" in prompt
    assert "observation.images.wrist" in prompt
    assert "Pick up the cube" in prompt


def test_build_user_prompt_handles_single_view_without_instruction() -> None:
    prompt = mod.build_user_prompt(n_frames=8, views=["cam"], instruction=None)
    assert "single camera view: cam" in prompt
    assert "pick-and-place" in prompt


def test_build_user_prompt_injects_scene_context() -> None:
    prompt = mod.build_user_prompt(
        n_frames=8,
        views=["cam"],
        instruction=None,
        scene_context="Two bins: one in front, one to the right.",
    )
    assert "Two bins: one in front, one to the right." in prompt


def test_row_from_label_coerces_and_normalizes() -> None:
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


def test_summarize_counts_outcomes() -> None:
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


def test_resolve_views_defaults_to_all(tmp_path: Path) -> None:
    _write_min_dataset(tmp_path, ["obs.front", "obs.wrist"])
    assert set(mod.resolve_views(tmp_path, None)) == {"obs.front", "obs.wrist"}


def test_resolve_views_rejects_unknown(tmp_path: Path) -> None:
    _write_min_dataset(tmp_path, ["obs.front"])
    with pytest.raises(ValueError, match="not in dataset"):
        mod.resolve_views(tmp_path, ["obs.missing"])
