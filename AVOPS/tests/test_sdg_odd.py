# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Tests for the ODD step spliced between SDG curation and generation."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

AVOPS = Path(__file__).resolve().parents[1]
SCRIPTS = AVOPS / "scripts"
SCRIPT = SCRIPTS / "sdg.sh"

spec = importlib.util.spec_from_file_location("sdg_odd", SCRIPTS / "sdg_odd.py")
sdg_odd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdg_odd)

WFM = {
    "generation_gaps": {
        "sceneDetails": {
            "weatherType": {
                "values": {
                    "rainy": {
                        "required_count": 2,
                        "suitable_videos": [
                            {"scene_name": "a", "video_path": "/clips/a.mp4"},
                            {"scene_name": "b", "video_path": "/clips/b.mp4"},
                            {"scene_name": "c", "video_path": "/clips/c.mp4"},
                        ],
                    }
                }
            }
        },
        "roadDetails": {
            "roadParameters": {
                "values": {
                    "narrow": {
                        "required_count": 1,
                        "suitable_videos": [{"scene_name": "a", "video_path": "/clips/a.mp4"}],
                    }
                }
            }
        },
    }
}
PROMPTS = {"weatherType": "Make it {value}."}
BASE = {
    "input_video_path": "/in",
    "output_directory": "/out",
    "search_mode": "embedding",
    "query_video": "/in/q.mp4",
    "top_k": 3,
    "cosmos": {"enabled": False, "num_clips": 5, "fps": 10},
}


def test_curation_payload_disables_retrieval_and_generation():
    result = sdg_odd.curation_payload(BASE)
    assert result["search_mode"] == "none"
    assert "query_video" not in result
    assert result["cosmos"] == {"enabled": False}
    assert BASE["search_mode"] == "embedding"  # input is not mutated


def test_clips_dir_sanitizes_run_id():
    path = sdg_odd.clips_dir(BASE, "manual__2026-10-16T12:00:00+00:00")
    assert path == Path("/out/manual__2026-10-16T12-00-00-00-00/split/clips")


def test_clips_dir_rejects_remote_output():
    with pytest.raises(ValueError, match="local"):
        sdg_odd.clips_dir({"output_directory": "msc://p/out"}, "run")


def test_generation_payloads_use_required_count_and_skip_unmapped():
    runs = sdg_odd.generation_payloads(BASE, WFM, PROMPTS)
    assert [name for name, _ in runs] == ["weatherType_rainy_a", "weatherType_rainy_b"]
    payload = runs[0][1]
    assert payload["search_mode"] == "embedding"
    assert payload["query_video"] == "/clips/a.mp4"
    assert payload["cosmos"] == {"enabled": True, "num_clips": 3, "fps": 10, "prompt": "Make it rainy."}
    assert payload["output_directory"] == "/out"


def test_generation_payloads_only_weather_and_time_of_day_by_default(capfd):
    wfm = json.loads(json.dumps(WFM))
    wfm["generation_gaps"]["sceneDetails"]["timeOfDay"] = {
        "values": {"night": {"required_count": 1, "suitable_videos": [{"scene_name": "a", "video_path": "/c/a.mp4"}]}}
    }
    prompts = {"weatherType": "W {value}", "timeOfDay": "T {value}", "roadParameters": "R {value}"}
    runs = sdg_odd.generation_payloads(BASE, wfm, prompts)
    assert {name.split("_")[0] for name, _ in runs} == {"weatherType", "timeOfDay"}
    assert "roadParameters" in capfd.readouterr().err
    only_road = sdg_odd.generation_payloads(BASE, wfm, prompts, attributes=["roadParameters"])
    assert [name for name, _ in only_road] == ["roadParameters_narrow_a"]


def test_generation_payloads_max_runs():
    assert len(sdg_odd.generation_payloads(BASE, WFM, PROMPTS, max_runs=1)) == 1


def test_default_prompts_cover_core_attributes():
    prompts = json.loads(sdg_odd.DEFAULT_PROMPTS.read_text())
    assert "rainy" in sdg_odd.resolve_prompt(prompts, "weatherType", "rainy")
    assert sdg_odd.resolve_prompt(prompts, "timeOfDay", "night")
    assert set(prompts) == set(sdg_odd.COSMOS3_ATTRIBUTES)


CURL_STUB = f"""#!{sys.executable}
import json, os, re, sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith("http"))
method = args[args.index("-X") + 1] if "-X" in args else "GET"
body = args[args.index("-d") + 1] if "-d" in args else ""
root = os.environ["STUB_ROOT"]
if url.endswith("/auth/token"):
    print(json.dumps({{"access_token": "t"}}))
elif method == "POST":
    seq = len(os.listdir(os.path.join(root, "runs"))) + 1
    run_id = f"manual__{{seq}}"
    payload = json.loads(body)["conf"]["payload"]
    open(os.path.join(root, "runs", run_id), "w").write(json.dumps(payload))
    if seq == 1 and payload["output_directory"].startswith("/"):
        clips = os.path.join(payload["output_directory"], run_id, "split", "clips")
        os.makedirs(clips)
        open(os.path.join(clips, "a.mp4"), "w").close()
    print(json.dumps({{"dag_run_id": run_id}}))
else:
    print(json.dumps({{"state": "success"}}))
"""

DOCKER_STUB = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.path.join(os.environ["STUB_ROOT"], "docker.log"), "a") as log:
    log.write(" ".join(args) + "\\n")
if args[0] == "run":
    mounts = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
    out = next(m.split(":")[0] for m in mounts if m.endswith(":/output"))
    wfm = {json.dumps(WFM)}
    wfm["generation_gaps"]["sceneDetails"]["weatherType"]["values"]["rainy"]["suitable_videos"][0][
        "video_path"
    ] = os.environ["STUB_CLIP"]
    with open(os.path.join(out, "wfm_generation_input.json"), "w") as f:
        json.dump(wfm, f)
"""


@pytest.fixture
def pipeline_env(tmp_path: Path) -> dict:
    repo = tmp_path / "sdg-workflow"
    (repo / "scripts").mkdir(parents=True)
    (repo / "Makefile").write_text("")
    (repo / "scripts" / "setup_airflow_secrets.sh").write_text("")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("curl", CURL_STUB), ("docker", DOCKER_STUB)):
        stub = bin_dir / name
        stub.write_text(text)
        stub.chmod(0o755)
    for tool in (
        "python3",
        "bash",
        "env",
        "grep",
        "mktemp",
        "rm",
        "cat",
        "dirname",
        "basename",
        "id",
        "mkdir",
        "cp",
        "date",
    ):
        real = shutil.which(tool)
        if real:
            (bin_dir / tool).symlink_to(real)

    stub_root = tmp_path / "stub"
    (stub_root / "runs").mkdir(parents=True)
    out = tmp_path / "out"
    out.mkdir()
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({**BASE, "output_directory": str(out), "top_k": 3}))
    env_file = tmp_path / "sdg.env"
    env_file.write_text(f"SDG_WORKFLOW_DIR={repo}\n")
    env = {
        "PATH": str(bin_dir),
        "HOME": str(tmp_path),
        "SDG_ENV_FILE": str(env_file),
        "STUB_ROOT": str(stub_root),
        "SDG_POLL_INTERVAL": "0",
    }
    return {"env": env, "payload": payload, "out": out, "stub_root": stub_root}


def run_sdg(ctx: dict, *args: str, **overrides: str) -> subprocess.CompletedProcess:
    env = {**ctx["env"], **overrides}
    return subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, check=False)


def submitted_payloads(ctx: dict) -> list[dict]:
    runs = sorted((ctx["stub_root"] / "runs").iterdir(), key=lambda p: int(p.name.split("__")[1]))
    return [json.loads(p.read_text()) for p in runs]


def test_pipeline_runs_curation_then_odd_then_generation(pipeline_env):
    clip = pipeline_env["out"] / "manual__1" / "split" / "clips" / "a.mp4"
    result = run_sdg(pipeline_env, "pipeline", str(pipeline_env["payload"]), STUB_CLIP=str(clip))
    assert result.returncode == 0, result.stderr

    curation, generation, second = submitted_payloads(pipeline_env)
    assert second["query_video"] == "/clips/b.mp4"  # rainy requires two base videos
    assert curation["search_mode"] == "none"
    assert curation["cosmos"] == {"enabled": False}
    assert generation["search_mode"] == "embedding"
    assert generation["query_video"] == str(clip)
    assert generation["cosmos"]["enabled"] is True
    assert "rainy" in generation["cosmos"]["prompt"]

    docker_log = (pipeline_env["stub_root"] / "docker.log").read_text()
    clips_dir = clip.parent
    assert f"{clips_dir}:/input:ro" in docker_log
    assert f"{pipeline_env['out'] / 'manual__1' / 'odd'}:/output" in docker_log
    assert f"--host-video-dir {clips_dir}" in docker_log
    assert (pipeline_env["out"] / "manual__1" / "odd" / "wfm_generation_input.json").is_file()


def test_pipeline_rejects_remote_output_directory(pipeline_env):
    payload = json.loads(pipeline_env["payload"].read_text())
    payload["output_directory"] = "msc://profile/out"
    pipeline_env["payload"].write_text(json.dumps(payload))
    result = run_sdg(pipeline_env, "pipeline", str(pipeline_env["payload"]))
    assert result.returncode != 0
    assert "local" in result.stderr


def test_pipeline_requires_payload(pipeline_env):
    result = run_sdg(pipeline_env, "pipeline")
    assert result.returncode != 0
    assert "requires a payload" in result.stderr


def test_pipeline_dry_run_shows_all_stages_without_side_effects(pipeline_env):
    result = run_sdg(pipeline_env, "--dry-run", "pipeline", str(pipeline_env["payload"]))
    assert result.returncode == 0, result.stderr
    assert "Stage 1/3" in result.stdout and "Stage 2/3" in result.stdout and "Stage 3/3" in result.stdout
    assert "[DRY-RUN] docker run" in result.stdout
    assert not list((pipeline_env["stub_root"] / "runs").iterdir())


def test_odd_command_runs_container_with_host_paths(pipeline_env, tmp_path):
    videos = tmp_path / "videos"
    videos.mkdir()
    result = run_sdg(pipeline_env, "odd", str(videos), str(tmp_path / "odd-out"), STUB_CLIP="x")
    assert result.returncode == 0, result.stderr
    log = (pipeline_env["stub_root"] / "docker.log").read_text()
    assert f"{videos}:/input:ro" in log
    assert "--network host" in log


def test_odd_build_uses_the_odd_dockerfile(pipeline_env):
    result = run_sdg(pipeline_env, "--dry-run", "odd-build")
    assert result.returncode == 0, result.stderr
    assert "docker build -t odd-component:latest" in result.stdout
    assert os.path.join("AVOPS", "ODD", "Dockerfile") in result.stdout


# --- CARLA backend -------------------------------------------------------------------------

CARLA_PIPELINE_STUB = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
run_dir = args[args.index("--run-dir") + 1]
host_run = os.path.join(os.environ["CARLA_DIR"], run_dir.removeprefix("/workspace/"))
assert os.path.isfile(os.path.join(host_run, "odd_input", "wfm_generation_input.json"))
with open(os.path.join(os.environ["STUB_ROOT"], "carla_call.json"), "w") as f:
    json.dump({{
        "args": args,
        "views": os.environ.get("CARLA_COSMOS_VIEWS"),
        "gpus": os.environ.get("COSMOS_GPU_IDS"),
        "carla_gpu": os.environ.get("CARLA_GPU_ID"),
    }}, f)
second_done = os.environ.get("STUB_FAIL") != "1"
tasks = ("roadParameters_narrow_1_a", "roadSurface_asphalt_1_b")
with open(os.path.join(host_run, "tasks.json"), "w") as f:
    json.dump({{"tasks": [{{"task": task}} for task in tasks]}}, f)
for task, done in zip(tasks, (True, second_done)):
    if done:
        out = os.path.join(host_run, "tasks", task)
        os.makedirs(os.path.join(out, "cosmos_synthesized"))
        for name in ("multiview_grid.mp4", "caption.txt", "metadata.json", "cosmos_synthesized/front_wide.mp4"):
            with open(os.path.join(out, name), "w") as f:
                f.write("x")
sys.exit(1 if os.environ.get("STUB_FAIL") == "1" else 0)
"""


@pytest.fixture
def carla_env(pipeline_env, tmp_path: Path) -> dict:
    carla_dir = tmp_path / "carla-cosmos"
    for sub in ("pipeline", "utils/cosmos"):
        (carla_dir / "src" / sub).mkdir(parents=True)
    source = AVOPS / "carla-cosmos"
    for module in ("pipeline/task_plan.py", "utils/cosmos/gpu_utils.py"):
        shutil.copy(source / "src" / module, carla_dir / "src" / module)
    shutil.copy(source / "scenario_map.json", carla_dir / "scenario_map.json")
    stub = carla_dir / "run_pipeline.py"
    stub.write_text(CARLA_PIPELINE_STUB)
    stub.chmod(0o755)
    odd = tmp_path / "odd-output"
    odd.mkdir()
    (odd / "wfm_generation_input.json").write_text(
        json.dumps(
            {
                "generation_gaps": {
                    "roadDetails": {
                        "roadParameters": {"values": {"narrow": {"required_count": 1, "suitable_videos": []}}}
                    },
                    "sceneDetails": {
                        "weatherType": {"values": {"rainy": {"required_count": 3, "suitable_videos": []}}}
                    },
                }
            }
        )
    )
    pipeline_env["env"]["CARLA_DIR"] = str(carla_dir)
    return {**pipeline_env, "carla_dir": carla_dir, "odd": odd}


def carla_call(ctx: dict) -> dict:
    return json.loads((ctx["stub_root"] / "carla_call.json").read_text())


def test_carla_command_runs_six_views_on_eight_gpus_and_collects(carla_env):
    out = carla_env["odd"] / "carla"
    result = run_sdg(carla_env, "carla", str(carla_env["odd"]), str(out))
    assert result.returncode == 0, result.stderr
    assert "CARLA has no scenario for weatherType=rainy" in result.stderr
    call = carla_call(carla_env)
    assert call["views"] == "front_wide,cross_left,cross_right,rear_left,rear_right,rear"
    assert (call["gpus"], call["carla_gpu"]) == ("0,1,2,3,4,5,6,7", "7")
    index = json.loads((out / "carla_results.json").read_text())
    assert (index["completed"], index["incomplete"]) == (2, 0)
    assert (out / "roadParameters_narrow_1_a" / "multiview_grid.mp4").is_file()
    assert "roadParameters_narrow_1_a/cosmos_synthesized/front_wide.mp4" in index["tasks"][0]["synthesized_views"]


def test_carla_command_overrides(carla_env):
    result = run_sdg(
        carla_env,
        "carla",
        str(carla_env["odd"]),
        CARLA_COSMOS_VIEWS="front_wide,cross_left,cross_right,rear",
        COSMOS_GPU_IDS="1,2,3,4",
        CARLA_GPU_ID="0",
    )
    assert result.returncode == 0, result.stderr
    call = carla_call(carla_env)
    assert (call["gpus"], call["carla_gpu"]) == ("1,2,3,4", "0")
    assert (carla_env["odd"] / "carla" / "carla_results.json").is_file()


@pytest.mark.parametrize("views", ["all", "front_wide,cross_left,cross_right,rear_left,rear_right,rear"])
def test_carla_five_to_eight_views_need_eight_gpus_not_six(carla_env, views):
    result = run_sdg(carla_env, "carla", str(carla_env["odd"]), CARLA_COSMOS_VIEWS=views, COSMOS_GPU_IDS="0,1,2,3,4,5")
    assert result.returncode != 0
    assert "need 8 GPUs for Cosmos but COSMOS_GPU_IDS lists 6" in result.stderr
    assert not (carla_env["stub_root"] / "carla_call.json").exists()


def test_carla_rejects_odd_output_with_no_carla_gaps(carla_env):
    (carla_env["odd"] / "wfm_generation_input.json").write_text(
        json.dumps({"generation_gaps": {"sceneDetails": {"weatherType": {"values": {"rainy": {"required_count": 2}}}}}})
    )
    result = run_sdg(carla_env, "carla", str(carla_env["odd"]))
    assert result.returncode != 0
    assert "None of the ODD gaps map to a CARLA scenario" in result.stderr


def test_carla_failed_task_fails_run_but_keeps_finished_results(carla_env):
    out = carla_env["odd"] / "carla"
    result = run_sdg(carla_env, "carla", str(carla_env["odd"]), str(out), STUB_FAIL="1")
    assert result.returncode != 0
    assert "CARLA backend failed" in result.stderr
    index = json.loads((out / "carla_results.json").read_text())
    assert (index["completed"], index["incomplete"]) == (1, 1)


def test_pipeline_carla_backend_replaces_dag_generation_runs(carla_env):
    clip = carla_env["out"] / "manual__1" / "split" / "clips" / "a.mp4"
    result = run_sdg(carla_env, "--backend", "carla", "pipeline", str(carla_env["payload"]), STUB_CLIP=str(clip))
    assert result.returncode == 0, result.stderr
    assert len(submitted_payloads(carla_env)) == 1  # curation only; no per-gap generation DAG runs
    index = json.loads((carla_env["out"] / "manual__1" / "carla" / "carla_results.json").read_text())
    assert index["completed"] == 2
    assert carla_call(carla_env)["args"][-1].endswith("/data/runs/manual__1")


def test_backend_flag_validation_and_dry_run(carla_env):
    assert run_sdg(carla_env, "--backend", "bogus", "pipeline", "x").returncode != 0
    result = run_sdg(carla_env, "--dry-run", "--backend=carla", "carla", str(carla_env["odd"]))
    assert result.returncode == 0, result.stderr
    assert (
        "[DRY-RUN] env CARLA_COSMOS_VIEWS=front_wide,cross_left,cross_right,rear_left,rear_right,rear" in result.stdout
    )
    assert not (carla_env["stub_root"] / "carla_call.json").exists()
