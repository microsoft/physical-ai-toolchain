# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Tests for the CARLA backend: task planning, task runner, stage helpers, and result collection."""

from __future__ import annotations

import ast
import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

AVOPS = Path(__file__).resolve().parents[1]
CARLA = AVOPS / "carla-cosmos"
SRC = CARLA / "src"


def load(name: str, path: Path, package: bool = False):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)] if package else None
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sdg_odd = load("sdg_odd_carla", AVOPS / "scripts" / "sdg_odd.py")
# The image installs src/ as the carla_cosmos package; load it under the same name.
load("carla_cosmos", SRC / "__init__.py", package=True)
from carla_cosmos.pipeline import run_tasks, task_plan, task_stages  # noqa: E402
from carla_cosmos.utils.cosmos import gpu_utils  # noqa: E402

SIX = list(sdg_odd.SIX_VIEWS)
SCENARIO_MAP = task_plan.load_scenario_map()


def gaps(**attributes: dict[str, int]) -> dict:
    return {
        "generation_gaps": {
            "any": {
                attribute: {"values": {value: {"required_count": count} for value, count in values.items()}}
                for attribute, values in attributes.items()
            }
        }
    }


def test_context_parallel_size_matches_view_count():
    assert [gpu_utils.choose_context_parallel_size(n) for n in (1, 2, 3, 4, 5, 6, 7)] == [1, 2, 4, 4, 8, 8, 8]


def test_six_views_skip_only_the_narrow_front_tele():
    assert set(sdg_odd.ALL_VIEWS) - set(SIX) == {"front_tele"}
    assert sdg_odd.parse_views("front_wide, rear") == ["front_wide", "rear"]
    assert sdg_odd.parse_views("all") == list(sdg_odd.ALL_VIEWS)
    with pytest.raises(ValueError, match="Unknown view"):
        sdg_odd.parse_views("front_wide,sideways")


def test_scenario_map_points_at_existing_files():
    for attribute, values in SCENARIO_MAP.items():
        for value, entry in values.items():
            assert value == value.lower(), (attribute, value)
            assert (CARLA / "scenarios" / entry["scenario"]).is_file(), (attribute, value)
            assert (CARLA / "prompts" / entry["prompts"]).read_text().strip(), (attribute, value)
            assert all(key.startswith("carla_") for key in entry["params"]), (attribute, value)


def test_plan_tasks_names_counts_and_skips():
    wfm = {
        "generation_gaps": {
            "roadDetails": {
                "roadParameters": {
                    "values": {
                        "Narrow": {"required_count": 2, "suitable_videos": [{"video_path": "/v/a b.mp4"}]},
                        "medium": {"required_count": 1},
                    }
                }
            },
            "sceneDetails": {"weatherType": {"values": {"rainy": {"required_count": 4}}}},
        }
    }
    tasks, skipped = task_plan.plan_tasks(wfm, SCENARIO_MAP)
    assert [t.name for t in tasks] == ["roadParameters_narrow_1_a-b", "roadParameters_narrow_2_scene"]
    assert tasks[0].scenario == "FollowLeadingVehicle.xosc"
    assert skipped == {"roadParameters=medium": 1, "weatherType=rainy": 4}


def test_scenario_params_select_xml_name_or_openscenario_path(tmp_path):
    assert task_plan.scenario_params("CutIn.xml", tmp_path) == {"carla_scenario": "CutIn"}
    assert task_plan.scenario_params("CutInSimple.xosc", tmp_path) == {
        "carla_openscenario": str(tmp_path / "CutInSimple.xosc")
    }


def test_pick_prompt_is_seeded_and_skips_blank_lines(tmp_path):
    prompts = tmp_path / "p.txt"
    prompts.write_text("one\n\n two \nthree\n")
    assert task_plan.pick_prompt(prompts, random.Random(1)) == task_plan.pick_prompt(prompts, random.Random(1))
    assert task_plan.pick_prompt(prompts, random.Random(2)) in {"one", "two", "three"}
    prompts.write_text("\n")
    with pytest.raises(ValueError, match="No prompts"):
        task_plan.pick_prompt(prompts, random.Random(0))


def test_gap_counts_split_supported_and_skipped():
    wfm = {
        "generation_gaps": {
            "roadDetails": {
                "roadParameters": {"values": {"Narrow": {"required_count": 2}, "medium": {"required_count": 1}}}
            },
            "sceneDetails": {"weatherType": {"values": {"rainy": {"required_count": 4}}}},
        }
    }
    assert sdg_odd.carla_gap_counts(wfm) == (2, {"roadParameters=medium": 1, "weatherType=rainy": 4})


@pytest.fixture
def stub_stages(monkeypatch, tmp_path):
    calls: list[task_stages.TaskParams] = []

    def fake_run_task(params: task_stages.TaskParams) -> Path:
        calls.append(params)
        if "fail" in params.prompt:
            raise task_stages.StageError("boom")
        params.out_dir.mkdir(parents=True, exist_ok=True)
        grid = params.out_dir / "multiview_grid.mp4"
        grid.write_bytes(b"x" * 2000)
        return grid

    monkeypatch.setattr(task_stages, "run_task", fake_run_task)
    monkeypatch.setattr(run_tasks, "PROMPTS_DIR", tmp_path / "prompts")
    (tmp_path / "prompts").mkdir()
    for entry in (e for values in SCENARIO_MAP.values() for e in values.values()):
        (tmp_path / "prompts" / entry["prompts"]).write_text("a prompt")
    return calls


def write_input(tmp_path: Path, **attributes: dict[str, int]) -> Path:
    path = tmp_path / "wfm.json"
    path.write_text(json.dumps(gaps(**attributes)))
    return path


def test_run_tasks_writes_outputs_and_manifest(stub_stages, tmp_path):
    run = tmp_path / "run"
    wfm = write_input(tmp_path, roadParameters={"narrow": 1}, trafficLights={"left": 1}, weatherType={"rainy": 1})
    args = ["--input", str(wfm), "--run-dir", str(run), "--views", "front_wide,rear", "--seed", "3"]
    assert run_tasks.main(args) == 0
    manifest = json.loads((run / "tasks.json").read_text())
    assert manifest["views"] == ["front_wide", "rear"]
    assert [t["status"] for t in manifest["tasks"]] == ["completed", "completed"]
    task_dir = run / "tasks" / "roadParameters_narrow_1_scene"
    assert (task_dir / "caption.txt").read_text() == "a prompt\n"
    metadata = json.loads((task_dir / "metadata.json").read_text())
    assert metadata["carla"]["carla_town"] == "Town01_Opt"
    assert metadata["carla"]["carla_openscenario"].endswith("FollowLeadingVehicle.xosc")
    assert stub_stages[1].carla_params["carla_scenario"] == "SignalizedJunctionLeftTurn"
    assert stub_stages[0].views == ["front_wide", "rear"]


def test_run_tasks_skips_finished_tasks_and_reports_failures(stub_stages, tmp_path):
    run = tmp_path / "run"
    wfm = write_input(tmp_path, roadParameters={"narrow": 1, "wide": 1})
    assert run_tasks.main(["--input", str(wfm), "--run-dir", str(run), "--views", "rear"]) == 0
    stub_stages.clear()
    (run / "tasks" / "roadParameters_wide_1_scene" / "multiview_grid.mp4").unlink()
    (tmp_path / "prompts" / "wide.txt").write_text("fail please")
    assert run_tasks.main(["--input", str(wfm), "--run-dir", str(run), "--views", "rear"]) == 1
    assert len(stub_stages) == 1
    statuses = {t["task"]: t["status"] for t in json.loads((run / "tasks.json").read_text())["tasks"]}
    assert statuses == {"roadParameters_narrow_1_scene": "completed", "roadParameters_wide_1_scene": "failed"}


def test_run_tasks_rejects_unmapped_gaps_and_unknown_views(stub_stages, tmp_path, capsys):
    wfm = write_input(tmp_path, weatherType={"rainy": 1})
    assert run_tasks.main(["--input", str(wfm), "--run-dir", str(tmp_path / "run")]) == 2
    assert "None of the ODD gaps map" in capsys.readouterr().err
    with pytest.raises(ValueError, match="Unknown view"):
        run_tasks.select_views("front_wide,sideways")


def test_collect_marks_unfinished_tasks_incomplete(tmp_path):
    run = tmp_path / "run"
    (run / "tasks" / "t1").mkdir(parents=True)
    (run / "tasks" / "t1" / "multiview_grid.mp4").write_text("x")
    (run / "tasks.json").write_text(
        json.dumps({"tasks": [{"task": "t1"}, {"task": "t2", "status": "failed", "error": "boom"}]})
    )
    index = sdg_odd.collect_carla_run(run, tmp_path / "out")
    assert [t["status"] for t in index["tasks"]] == ["completed", "incomplete"]
    assert index["tasks"][1]["error"] == "boom"
    assert (tmp_path / "out" / "t1" / "multiview_grid.mp4").is_file()


def test_stage_view_and_gpu_defaults(monkeypatch):
    monkeypatch.delenv("CARLA_COSMOS_VIEWS", raising=False)
    monkeypatch.delenv("COSMOS_GPU_IDS", raising=False)
    assert task_stages.default_views() == task_stages.ALL_VIEWS
    assert task_stages.cosmos_gpu_ids(7) == "0,1,2,3,4,5,6,7"
    assert task_stages.cosmos_gpu_ids(4) == "1,2,3,4"
    monkeypatch.setenv("CARLA_COSMOS_VIEWS", ",".join(SIX))
    monkeypatch.setenv("COSMOS_GPU_IDS", "0, 1,2,3,4,5")
    assert task_stages.default_views() == SIX
    assert task_stages.cosmos_gpu_ids(6) == "0,1,2,3,4,5"
    monkeypatch.setenv("CARLA_COSMOS_VIEWS", "front_wide,sideways")
    with pytest.raises(ValueError, match="Unknown CARLA_COSMOS_VIEWS"):
        task_stages.default_views()


def test_chunks_follow_requested_seconds():
    assert [task_stages.chunks_for(s) for s in (2, 3.5, 6, 7, 9, 12)] == [1, 1, 2, 2, 3, 5]


def test_stage_env_passes_the_token_only_through_the_environment(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", ' "abc" ')
    monkeypatch.setenv("COSMOS_GPU_IDS", "2,3")
    env = task_stages.stage_env(2)
    assert env["HF_TOKEN"] == env["HUGGING_FACE_HUB_TOKEN"] == "abc"
    assert env["CUDA_VISIBLE_DEVICES"] == "2,3"


def test_stage_failure_keeps_the_error_log(tmp_path, monkeypatch):
    class Result:
        returncode = 3
        stderr = "kaput"

    monkeypatch.setattr(task_stages.subprocess, "run", lambda *a, **kw: Result())
    with pytest.raises(task_stages.StageError, match="kaput"):
        task_stages.run_stage("Stage 2: prepare", ["x"], {}, tmp_path)
    assert (tmp_path / "stage_2_error.log").read_text() == "kaput"
    task_stages.run_stage("Stage 1: capture", ["x"], {}, tmp_path, tolerate="kaput")


def test_stage_stitches_a_six_camera_grid(tmp_path, monkeypatch):
    for name in (
        "camera_front_wide_120fov",
        "camera_cross_left_120fov",
        "camera_cross_right_120fov",
        "camera_rear_left_70fov",
        "camera_rear_right_70fov",
        "camera_rear_tele_30fov",
    ):
        (tmp_path / f"{name}.mp4").write_text("x")
    calls = []
    monkeypatch.setattr(task_stages.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    task_stages.generate_carla_grid(str(tmp_path), str(tmp_path / "grid.mp4"))
    assert calls and "xstack=inputs=6" in calls[0][calls[0].index("-filter_complex") + 1]


def test_relative_imports_resolve_within_src():
    for source in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text())):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            base = source.parent
            for _ in range(node.level - 1):
                base = base.parent
            assert SRC in (base, *base.parents), (source, node.lineno)
            target = base.joinpath(*(node.module or "").split(".")) if node.module else base
            names = [alias.name for alias in node.names]
            assert target.with_suffix(".py").is_file() or any(
                target.joinpath(name).with_suffix(".py").is_file() or (target / name).is_dir() for name in names
            ), f"{source.relative_to(SRC)}:{node.lineno} imports {node.module} from a missing module"


def test_stage_modules_are_launched_by_package_name():
    assert task_stages.PACKAGE == "carla_cosmos"
    stages = {p.stem for p in (SRC / "tasks").glob("*.py")}
    assert {"run_carla_capture", "prepare_cosmos_inputs", "run_cosmos_inference"} <= stages
    assert "carla_cosmos_pipeline" not in "".join(p.read_text() for p in SRC.rglob("*.py"))


def test_src_stays_compatible_with_the_cosmos_python():
    # The pipeline image runs Python 3.10, older than the repo's ruff target.
    for source in SRC.rglob("*.py"):
        tree = ast.parse(source.read_text(), feature_version=(3, 10))
        for node in ast.walk(tree):
            assert not (isinstance(node, ast.Attribute) and node.attr == "UTC"), (
                f"{source.relative_to(SRC)}:{node.lineno} uses datetime.UTC, added in Python 3.11"
            )
