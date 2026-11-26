# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Unit tests for the zero-NGC OSMO closed-loop AVOps workflow."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import urllib.error
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import yaml

AVOPS_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = AVOPS_DIR / "scripts"
WORKFLOWS_DIR = AVOPS_DIR / "workflows" / "osmo"
CL_SCRIPT = SCRIPTS_DIR / "avops-closed-loop.sh"

_spec = importlib.util.spec_from_file_location("cosmos3_closed_loop", SCRIPTS_DIR / "cosmos3_closed_loop.py")
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)

SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
SHA256_IMAGE_RE = re.compile(r"^vllm/vllm-omni:[^@\s]+@sha256:[0-9a-fA-F]{64}$")

SAMPLE_WFM = {
    "generation_gaps": {
        "sceneDetails": {
            "weatherType": {
                "values": {
                    "cloudy": {
                        "required_count": 1,
                        "suitable_videos": [
                            {"scene_name": "clip_01", "video_path": "/clips/clip_01.mp4"},
                            {"scene_name": "clip_02", "video_path": "/clips/clip_02.mp4"},
                        ],
                    },
                    "rainy": {
                        "required_count": 3,
                        "suitable_videos": [
                            {"scene_name": "clip_01", "video_path": "/clips/clip_01.mp4"},
                            {"scene_name": "clip_02", "video_path": "/clips/clip_02.mp4"},
                            {"scene_name": "clip_03", "video_path": "/clips/clip_03.mp4"},
                        ],
                    },
                    "foggy": {
                        "required_count": 2,
                        "suitable_videos": [
                            {"scene_name": "clip_02", "video_path": "/clips/clip_02.mp4"},
                            {"scene_name": "clip_03", "video_path": "/clips/clip_03.mp4"},
                        ],
                    },
                    "snowy": {
                        "required_count": 2,
                        "suitable_videos": [
                            {"scene_name": "clip_01", "video_path": "/clips/clip_01.mp4"},
                        ],
                    },
                }
            },
            "timeOfDay": {
                "values": {
                    "night": {
                        "required_count": 2,
                        "suitable_videos": [
                            {"scene_name": "clip_03", "video_path": "/clips/clip_03.mp4"},
                        ],
                    }
                }
            },
        },
        "roadDetails": {
            "roadSurface": {
                "values": {
                    "concrete": {
                        "required_count": 4,
                        "suitable_videos": [{"scene_name": "clip_01", "video_path": "/clips/clip_01.mp4"}],
                    }
                }
            }
        },
    }
}

SAMPLE_PROMPTS = {
    "prompts": [
        {
            "category": "sceneDetails",
            "attribute": "weatherType",
            "value": "rainy",
            "scene_name": "clip_01",
            "source": "llm",
            "prompt": "Heavy rain falls across a multi-lane highway while vehicles hold lanes.",
        },
        {
            "category": "sceneDetails",
            "attribute": "timeOfDay",
            "value": "night",
            "scene_name": "clip_03",
            "source": "llm",
            "prompt": "Nighttime highway driving illuminated by bright headlights and taillights.",
        },
    ]
}


class _FakeHTTPResponse(io.BytesIO):
    def __init__(self, payload: bytes, content_type: str = "video/mp4") -> None:
        super().__init__(payload)
        self.headers = {"Content-Type": content_type} if content_type else {}

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _render_jinja_if_blocks(raw: str, use_hf_cred: bool) -> str:
    """Render simple ``{% if use_huggingface_credential == "true" %}`` blocks for YAML validation."""
    out = []
    keep = True
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped == '{% if use_huggingface_credential == "true" %}':
            keep = use_hf_cred
            continue
        if stripped == "{% endif %}":
            keep = True
            continue
        if keep:
            out.append(line)
    return "\n".join(out) + "\n"


def _make_sample_plan(
    tmp_path: Path,
    attribute: str = "weatherType",
    target_value: str = "rainy",
    ref_frame: Path | None = None,
) -> Path:
    plan_path = tmp_path / "jobs.json"
    plan_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "job_id": f"synth_01_{attribute}_{target_value}_clip_01",
                        "category": "sceneDetails",
                        "attribute": attribute,
                        "target_value": target_value,
                        "base_scene_name": "clip_01",
                        "base_video_path": "/clips/clip_01.mp4",
                        "reference_frame_path": str(ref_frame) if ref_frame is not None else None,
                        "odd_prompt_text": f"{target_value} highway driving.",
                        "cosmos3_prompt": cl.build_cosmos3_prompt_payload(
                            attribute, target_value, f"{target_value} highway driving.", "clip_01"
                        ),
                        "negative_prompt": cl.DEFAULT_NEGATIVE_PROMPT,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return plan_path


def test_probe_clips_metadata_fallback_and_errors(tmp_path: Path) -> None:
    clips_dir = tmp_path / "clips"
    clips_dir.mkdir()
    (clips_dir / "scene_a.mp4").write_bytes(b"0123456789")
    (clips_dir / "scene_b.MOV").write_bytes(b"abcdef")
    (clips_dir / "ignore.txt").write_text("not a video", encoding="utf-8")

    with mock.patch.object(cl.shutil, "which", return_value=None):
        manifest = cl.probe_clips(clips_dir, tmp_path / "manifest.json")

    assert manifest["clip_count"] == 2
    assert manifest["total_size_bytes"] == 16
    assert [c["filename"] for c in manifest["clips"]] == ["scene_a.mp4", "scene_b.MOV"]
    assert (tmp_path / "manifest.json").is_file()

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ValueError, match="No video files found"):
        cl.probe_clips(empty_dir, tmp_path / "empty_manifest.json")

    with pytest.raises(FileNotFoundError, match="Input directory not found"):
        cl.probe_clips(tmp_path / "missing", tmp_path / "missing_manifest.json")


def test_select_diverse_gap_jobs_shortfall_sorting_and_scene_diversification(tmp_path: Path) -> None:
    odd_dir = tmp_path / "odd-before"
    odd_dir.mkdir()
    (odd_dir / cl.WFM_FILENAME).write_text(json.dumps(SAMPLE_WFM), encoding="utf-8")
    (odd_dir / cl.PROMPTS_FILENAME).write_text(json.dumps(SAMPLE_PROMPTS), encoding="utf-8")

    plan = cl.build_jobs(
        odd_output_dir=odd_dir,
        out_path=tmp_path / "jobs.json",
        max_clips=5,
        extract_frames=False,
    )
    assert plan["job_count"] == 5
    targets = [(j["attribute"], j["target_value"]) for j in plan["jobs"]]
    # Sorted by descending required_count: rainy(3), foggy(2), snowy(2), night(2), cloudy(1)
    assert targets == [
        ("weatherType", "rainy"),
        ("weatherType", "foggy"),
        ("weatherType", "snowy"),
        ("timeOfDay", "night"),
        ("weatherType", "cloudy"),
    ]
    # First three scenes should maximize base-clip diversity across available scenes.
    first_three_scenes = [j["base_scene_name"] for j in plan["jobs"][:3]]
    assert len(set(first_three_scenes)) >= 2
    assert plan["jobs"][0]["prompt_source"] == "llm"
    assert "Heavy rain falls" in plan["jobs"][0]["cosmos3_prompt"]["background_setting"]
    assert all(j["attribute"] in cl.COSMOS3_ATTRIBUTES for j in plan["jobs"])


def test_build_jobs_mode_t2v_skips_frame_extraction_and_missing_file_error(tmp_path: Path) -> None:
    odd_dir = tmp_path / "odd-before"
    odd_dir.mkdir()
    with pytest.raises(FileNotFoundError, match=cl.WFM_FILENAME):
        cl.build_jobs(odd_output_dir=odd_dir, out_path=tmp_path / "jobs.json")

    (odd_dir / cl.WFM_FILENAME).write_text(json.dumps(SAMPLE_WFM), encoding="utf-8")
    with mock.patch.object(cl, "extract_reference_frame") as extract_mock:
        plan = cl.build_jobs(
            odd_output_dir=odd_dir,
            out_path=tmp_path / "jobs_t2v.json",
            max_clips=5,
            extract_frames=True,
            mode="t2v",
        )
    extract_mock.assert_not_called()
    assert all(j["planned_mode"] == "t2v" for j in plan["jobs"])
    assert all(j["reference_frame_path"] is None for j in plan["jobs"])

    # In auto mode, only non-extreme shifts (cloudy) trigger frame extraction.
    with mock.patch.object(cl, "extract_reference_frame") as extract_auto_mock:
        auto_plan = cl.build_jobs(
            odd_output_dir=odd_dir,
            out_path=tmp_path / "jobs_auto.json",
            max_clips=5,
            extract_frames=True,
            mode="auto",
        )
    assert extract_auto_mock.call_count == 1
    cloudy_jobs = [j for j in auto_plan["jobs"] if j["target_value"] == "cloudy"]
    assert len(cloudy_jobs) == 1
    assert cloudy_jobs[0]["planned_mode"] == "i2v"
    assert cloudy_jobs[0]["reference_frame_path"] is not None


def test_extract_reference_frame_falls_back_to_zero_timestamp(tmp_path: Path) -> None:
    video = tmp_path / "short.mp4"
    video.write_bytes(b"shortclip")
    frame_out = tmp_path / "frames" / "short.png"
    timestamps = []

    def _fake_run(cmd: list[str], check: bool = True, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        ss_idx = cmd.index("-ss")
        ts = cmd[ss_idx + 1]
        timestamps.append(ts)
        if ts == "0.0":
            frame_out.write_bytes(b"\x89PNG\r\n\x1a\nFRAME")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        mock.patch.object(cl.shutil, "which", return_value="/usr/bin/ffmpeg"),
        mock.patch.object(cl.subprocess, "run", side_effect=_fake_run),
    ):
        result = cl.extract_reference_frame(video, frame_out, timestamp_sec=1.0)

    assert result == frame_out
    assert timestamps == ["1.0", "0.0"]
    assert frame_out.read_bytes() == b"\x89PNG\r\n\x1a\nFRAME"


def test_synthesize_jobs_modes_and_multipart_sanitization(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\nFAKEPNG")
    plan_path = _make_sample_plan(tmp_path, attribute="weatherType", target_value="rainy", ref_frame=ref)

    captured = {}

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeHTTPResponse:
        captured["url"] = req.full_url
        captured["content_type"] = req.get_header("Content-type")
        captured["body"] = req.data
        return _FakeHTTPResponse(b"\x00\x00\x00\x18ftypmp42FAKEVIDEO", content_type="video/mp4")

    with mock.patch.object(cl.urllib.request, "urlopen", side_effect=_fake_urlopen):
        manifest = cl.synthesize_jobs(
            jobs_plan_path=plan_path,
            endpoint_url="http://127.0.0.1:8000/",
            out_dir=tmp_path / "out-i2v",
            size="854x480",
            num_frames=49,
            mode="i2v",
        )

    assert captured["url"] == "http://127.0.0.1:8000/v1/videos/sync"
    assert "multipart/form-data; boundary=" in str(captured["content_type"])
    assert b'name="input_reference"' in captured["body"]
    assert b'name="num_frames"\r\n\r\n49' in captured["body"]
    assert manifest["generated_count"] == 1
    assert manifest["clips"][0]["mode"] == "i2v"
    assert (tmp_path / "out-i2v" / "synth_01_weatherType_rainy_clip_01.mp4").is_file()

    # Auto mode switches extreme domain shifts (rainy) to t2v without input_reference.
    captured.clear()
    with mock.patch.object(cl.urllib.request, "urlopen", side_effect=_fake_urlopen):
        auto_manifest = cl.synthesize_jobs(
            jobs_plan_path=plan_path,
            endpoint_url="http://127.0.0.1:8000",
            out_dir=tmp_path / "out-auto",
            mode="auto",
        )
    assert b'name="input_reference"' not in captured["body"]
    assert auto_manifest["clips"][0]["mode"] == "t2v"

    # Explicit t2v mode omits input_reference even for subtle shifts (cloudy).
    cloudy_plan = _make_sample_plan(tmp_path, attribute="weatherType", target_value="cloudy", ref_frame=None)
    captured.clear()
    with mock.patch.object(cl.urllib.request, "urlopen", side_effect=_fake_urlopen):
        t2v_manifest = cl.synthesize_jobs(
            jobs_plan_path=cloudy_plan,
            endpoint_url="http://127.0.0.1:8000",
            out_dir=tmp_path / "out-t2v",
            mode="t2v",
        )
    assert b'name="input_reference"' not in captured["body"]
    assert t2v_manifest["clips"][0]["mode"] == "t2v"

    # Multipart header filename sanitization strips quotes, CR, and LF.
    dirty_path = mock.MagicMock(spec=Path)
    dirty_path.name = 'evil"\r\nX-Injected: 1\nname.png'
    dirty_path.read_bytes.return_value = b"PNG"
    dirty_path.__str__.return_value = "evil.png"
    body, _ = cl.encode_multipart_form({"prompt": "ok"}, file_field="input_reference", file_path=dirty_path)
    header_section = body.split(b"\r\n\r\n")[1]
    assert b'filename="evilX-Injected: 1name.png"' in header_section
    assert b"\r\nX-Injected:" not in body


def test_synthesize_jobs_error_handling_and_payload_validation(tmp_path: Path) -> None:
    plan_path = _make_sample_plan(tmp_path, attribute="weatherType", target_value="rainy", ref_frame=None)

    with pytest.raises(ValueError, match="http or https"):
        cl.synthesize_jobs(plan_path, "file:///etc/passwd", tmp_path / "out", mode="t2v")

    with (
        mock.patch.object(
            cl.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ),
        pytest.raises(RuntimeError, match="connection refused"),
    ):
        cl.synthesize_jobs(plan_path, "http://127.0.0.1:8000", tmp_path / "out", mode="t2v")

    http_err = urllib.error.HTTPError(
        url="http://127.0.0.1:8000/v1/videos/sync",
        code=503,
        msg="Service Unavailable",
        hdrs=None,
        fp=io.BytesIO(b'{"error":"overloaded"}'),
    )
    with (
        mock.patch.object(cl.urllib.request, "urlopen", side_effect=http_err),
        pytest.raises(RuntimeError, match="HTTP 503"),
    ):
        cl.synthesize_jobs(plan_path, "http://127.0.0.1:8000", tmp_path / "out", mode="t2v")

    with (
        mock.patch.object(cl.urllib.request, "urlopen", side_effect=TimeoutError("timed out")),
        pytest.raises(RuntimeError, match="timed out"),
    ):
        cl.synthesize_jobs(plan_path, "http://127.0.0.1:8000", tmp_path / "out", mode="t2v")

    with (
        mock.patch.object(
            cl.urllib.request,
            "urlopen",
            return_value=_FakeHTTPResponse(b"", content_type="video/mp4"),
        ),
        pytest.raises(RuntimeError, match="empty video payload"),
    ):
        cl.synthesize_jobs(plan_path, "http://127.0.0.1:8000", tmp_path / "out", mode="t2v")

    with (
        mock.patch.object(
            cl.urllib.request,
            "urlopen",
            return_value=_FakeHTTPResponse(b'{"detail":"bad prompt"}', content_type="application/json"),
        ),
        pytest.raises(RuntimeError, match="non-video response"),
    ):
        cl.synthesize_jobs(plan_path, "http://127.0.0.1:8000", tmp_path / "out", mode="t2v")

    with (
        mock.patch.object(
            cl.urllib.request,
            "urlopen",
            return_value=_FakeHTTPResponse(b'{"error":"no content-type header"}', content_type=""),
        ),
        pytest.raises(RuntimeError, match="non-video payload"),
    ):
        cl.synthesize_jobs(plan_path, "http://127.0.0.1:8000", tmp_path / "out", mode="t2v")


def test_compare_odd_reports_honest_closed_gap_and_adherence(tmp_path: Path) -> None:
    before_dir = tmp_path / "before"
    after_dir = tmp_path / "after"
    (after_dir / "Text_Annotations").mkdir(parents=True)
    before_dir.mkdir(parents=True)

    (before_dir / cl.FINAL_GAP_FILENAME).write_text(
        json.dumps(
            {
                "dataset_summary": {
                    "total_scenes": 12,
                    "overall_odd_score": 0.4551,
                    "odd_score_threshold": 0.8,
                    "total_gaps_detected": 2,
                    "total_variations_generated": 27,
                },
                "category_scores": {"sceneDetails": 0.4245, "roadDetails": 0.4458},
                "detected_gaps": [
                    {
                        "category": "sceneDetails",
                        "attribute": "weatherType",
                        "odd_score": 0.4615,
                        "threshold": 0.9,
                        "total_needed": 15,
                    },
                    {
                        "category": "sceneDetails",
                        "attribute": "timeOfDay",
                        "odd_score": 0.3875,
                        "threshold": 0.85,
                        "total_needed": 12,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (after_dir / cl.FINAL_GAP_FILENAME).write_text(
        json.dumps(
            {
                "dataset_summary": {
                    "total_scenes": 18,
                    "overall_odd_score": 0.6120,
                    "odd_score_threshold": 0.8,
                    "total_gaps_detected": 1,
                    "total_variations_generated": 6,
                },
                "category_scores": {"sceneDetails": 0.7420, "roadDetails": 0.4820},
                "detected_gaps": [
                    {
                        "category": "sceneDetails",
                        "attribute": "weatherType",
                        "odd_score": 0.7850,
                        "threshold": 0.9,
                        "total_needed": 6,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (after_dir / "Text_Annotations" / "synth_01_rainy.json").write_text(
        json.dumps({"sceneDetails": {"weatherType": {"answer": "Rainy"}}}),
        encoding="utf-8",
    )
    (after_dir / "Text_Annotations" / "synth_02_night.json").write_text(
        json.dumps({"sceneDetails": {"timeOfDay": {"answer": "Night"}}}),
        encoding="utf-8",
    )

    syn_manifest = tmp_path / "synthesis_manifest.json"
    syn_manifest.write_text(
        json.dumps(
            {
                "clips": [
                    {
                        "category": "sceneDetails",
                        "attribute": "weatherType",
                        "target_value": "rainy",
                        "output_video_path": "/data/synth_01_rainy.mp4",
                    },
                    {
                        "category": "sceneDetails",
                        "attribute": "timeOfDay",
                        "target_value": "night",
                        "output_video_path": "/data/synth_02_night.mp4",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    report = cl.compare_odd_reports(before_dir, after_dir, syn_manifest, tmp_path / "report")
    assert report["synthetic_clips_added"] == 6
    assert report["overall_odd_score"]["delta"] == pytest.approx(0.1569)
    assert report["category_deltas"]["sceneDetails"]["delta"] == pytest.approx(0.3175)
    assert report["condition_adherence"]["matched"] == 2
    assert report["condition_adherence"]["rate"] == 1.0

    by_attr = {item["attribute"]: item for item in report["attribute_deltas"]}
    assert by_attr["timeOfDay"]["gap_closed"] is True
    assert by_attr["timeOfDay"]["after_score"] is None
    assert by_attr["timeOfDay"]["delta"] is None
    assert by_attr["timeOfDay"]["threshold"] == 0.85

    assert by_attr["weatherType"]["gap_closed"] is False
    assert by_attr["weatherType"]["after_score"] == pytest.approx(0.7850)
    assert by_attr["weatherType"]["delta"] == pytest.approx(0.3235)

    summary_md = (tmp_path / "report" / "closed_loop_summary.md").read_text(encoding="utf-8")
    assert ">= 0.85 (closed)" in summary_md


def test_safe_env_var_parsing_handles_empty_and_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSMOS3_URL", "   ")
    monkeypatch.setenv("COSMOS3_NUM_FRAMES", "")
    monkeypatch.setenv("COSMOS3_FPS", "  \t ")
    monkeypatch.setenv("COSMOS3_STEPS", "")
    monkeypatch.setenv("COSMOS3_GUIDANCE", "   ")
    monkeypatch.setenv("COSMOS3_FLOW_SHIFT", "")
    monkeypatch.setenv("COSMOS3_SEED", " ")
    monkeypatch.setenv("COSMOS3_TIMEOUT", "")
    monkeypatch.setenv("COSMOS3_MODE", "  ")
    monkeypatch.setenv("AVOPS_MAX_CLIPS", " ")

    assert cl._env_str("COSMOS3_URL", "http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert cl._env_int("COSMOS3_NUM_FRAMES", 97) == 97
    assert cl._env_float("COSMOS3_GUIDANCE", 6.0) == 6.0

    args = cl.parse_args(["synthesize", "--jobs", "jobs.json", "--out-dir", "out"])
    assert args.endpoint_url == "http://127.0.0.1:8000"
    assert args.num_frames == 97
    assert args.fps == 24
    assert args.steps == 35
    assert args.guidance_scale == 6.0
    assert args.flow_shift == 10.0
    assert args.seed == 17
    assert args.timeout == 900.0
    assert args.mode == "auto"


def test_osmo_workflows_schema_invariants_and_zero_ngc() -> None:
    expected = {"avops-closed-loop.yaml", "cosmos-reason2-odd.yaml", "cosmos3-nano-generate.yaml"}
    actual = {p.name for p in WORKFLOWS_DIR.glob("*.yaml")}
    assert actual == expected

    for wf_path in sorted(WORKFLOWS_DIR.glob("*.yaml")):
        raw = wf_path.read_text(encoding="utf-8")
        assert "NGC_API_KEY" not in raw
        assert "nvcr.io" not in raw
        assert "curl http" not in raw
        assert "curl -fsS http" not in raw

        for use_hf in (True, False):
            rendered = _render_jinja_if_blocks(raw, use_hf_cred=use_hf)
            parsed = yaml.safe_load(rendered)
            assert "workflow" in parsed
            assert "default-values" in parsed
            wf = parsed["workflow"]
            assert wf["timeout"]["exec_timeout"]

            tasks = []
            if "groups" in wf:
                for group in wf["groups"]:
                    group_tasks = group["tasks"]
                    lead_tasks = [t for t in group_tasks if t.get("lead") is True]
                    assert len(lead_tasks) == 1, f"{wf_path.name} group {group['name']} must have exactly one lead"
                    tasks.extend(group_tasks)
            else:
                tasks.extend(wf["tasks"])

            for task in tasks:
                if "credentials" in task:
                    assert use_hf is True
                    assert isinstance(task["credentials"], dict)
                    assert task["credentials"] == {"huggingface": {"HF_TOKEN": "hf_token"}}
                res_name = task.get("resource", "default")
                res_spec = wf["resources"].get(res_name, {})
                if res_spec.get("gpu"):
                    assert task["environment"]["NVIDIA_DRIVER_CAPABILITIES"] == "all"

            defaults = parsed["default-values"]
            for key, value in defaults.items():
                if key in {"vlm_image", "cosmos3_image"}:
                    assert SHA256_IMAGE_RE.match(str(value)), f"{wf_path.name} {key} not digest-pinned: {value}"
                if key in {"vlm_revision", "cosmos3_revision"}:
                    assert SHA40_RE.match(str(value)), f"{wf_path.name} {key} not 40-char SHA: {value}"


def test_closed_loop_shell_script_behavior_and_precedence(tmp_path: Path) -> None:
    assert os.access(CL_SCRIPT, os.X_OK)

    # Config preview works without default env file present.
    preview = subprocess.run(
        ["bash", str(CL_SCRIPT), "--config-preview"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "zero-NGC" in preview.stdout

    # Dry-run executes all 4 stages cleanly.
    dry = subprocess.run(
        [
            "bash",
            str(CL_SCRIPT),
            "--dry-run",
            "-i",
            str(tmp_path / "clips"),
            "-o",
            str(tmp_path / "out"),
            "run-all",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "probe-clips" in dry.stdout
    assert "build-jobs" in dry.stdout
    assert "synthesize" in dry.stdout
    assert "compare-odd" in dry.stdout
    assert "--guidance-scale" in dry.stdout
    assert "--flow-shift" in dry.stdout
    assert "--seed" in dry.stdout
    assert "--timeout" in dry.stdout
    assert "--mode" in dry.stdout

    # Precedence: CLI > exported env > env file > default.
    env_file = tmp_path / "custom.env"
    env_file.write_text(
        "\n".join(
            [
                "AVOPS_MAX_CLIPS=10",
                "COSMOS3_MODE=i2v",
                "OSMO_POOL=file-pool",
                "ODD_THRESHOLD=0.91",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    base_env = {k: v for k, v in os.environ.items() if not k.startswith(("AVOPS_", "ODD_", "COSMOS3_", "OSMO_"))}
    base_env["COSMOS3_MODE"] = "t2v"  # Exported env beats env file (i2v)
    prec = subprocess.run(
        [
            "bash",
            str(CL_SCRIPT),
            "--env-file",
            str(env_file),
            "--max-clips",
            "3",  # CLI beats env file (10)
            "--config-preview",
        ],
        env=base_env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "3" in prec.stdout
    assert "mode=t2v" in prec.stdout
    assert "file-pool" in prec.stdout
    assert "0.91" in prec.stdout

    # Explicit missing --env-file fails fatally.
    missing_env = subprocess.run(
        ["bash", str(CL_SCRIPT), "--env-file", str(tmp_path / "nope.env"), "--config-preview"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing_env.returncode != 0
    assert "Environment file not found" in missing_env.stderr

    # Missing value after value option fails fatally.
    missing_val = subprocess.run(
        ["bash", str(CL_SCRIPT), "--max-clips"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing_val.returncode != 0
    assert "requires a value" in missing_val.stderr


def test_osmo_submit_set_string_keys_match_workflow_defaults() -> None:
    wf_raw = (WORKFLOWS_DIR / "avops-closed-loop.yaml").read_text(encoding="utf-8")
    wf_parsed = yaml.safe_load(_render_jinja_if_blocks(wf_raw, use_hf_cred=True))
    wf_defaults = set(wf_parsed["default-values"].keys())

    dry_submit = subprocess.run(
        ["bash", str(CL_SCRIPT), "--dry-run", "osmo-submit"],
        capture_output=True,
        text=True,
        check=True,
    )
    submitted_keys = set(re.findall(r"--set-string\s+([A-Za-z0-9_]+)=", dry_submit.stdout))
    expected_keys = {
        "odd_image",
        "vlm_model",
        "vlm_revision",
        "vlm_input",
        "odd_threshold",
        "cosmos3_revision",
        "max_clips",
        "video_size",
        "num_frames",
        "fps",
        "steps",
        "generation_mode",
    }
    assert submitted_keys == expected_keys
    assert submitted_keys.issubset(wf_defaults)
