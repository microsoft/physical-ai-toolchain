# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Tests for the ODD generation-prompt writer and its use by sdg_odd.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

AVOPS = Path(__file__).resolve().parents[1]
ODD_DIR = AVOPS / "ODD"
sys.path.insert(0, str(ODD_DIR))

from odd_core.engine import ODDCoverageConfig  # noqa: E402
from odd_core.prompt_writer import (  # noqa: E402
    PROMPTS_FILENAME,
    PromptWriter,
    normalize_paragraph,
    scene_facts,
    write_generation_prompts,
)

spec = importlib.util.spec_from_file_location("sdg_odd", AVOPS / "scripts" / "sdg_odd.py")
sdg_odd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdg_odd)

ANNOTATION = {
    "sceneDetails": {
        "weatherType": {"question": "What is the weather?", "answer": "Snowy"},
        "timeOfDay": {"question": "What is the time of day?", "answer": "Night."},
    },
    "otherAgentDetails": {
        "pedestrianDetails": {"question": "Are there pedestrians?", "answer": "No"},
    },
}
LONG = "A dark winter night on a wide asphalt road with falling snow and no pedestrians visible ahead."


def fake_llm(text: str):
    calls: list = []

    def complete(messages):
        calls.append(messages)
        return text

    complete.calls = calls
    return complete


def test_scene_facts_flatten_and_clean_answers():
    facts = scene_facts(ANNOTATION)
    assert ("timeOfDay", "What is the time of day?", "night") in facts
    assert len(facts) == 3


def test_normalize_paragraph_collapses_and_appends_preservation():
    text = normalize_paragraph("<think>hmm</think>**Prompt:** " + LONG.replace(" on ", "\n\non "))
    assert "\n" not in text and "*" not in text
    assert text.endswith("Preserve the camera viewpoint, road layout, and vehicle motion.")


@pytest.mark.parametrize("bad", ["", "too short", "<think>never closed " + LONG, "x" * 2000])
def test_normalize_paragraph_rejects_unusable_output(bad):
    assert normalize_paragraph(bad) == ""


def test_writer_uses_llm_with_scene_facts_and_target():
    complete = fake_llm(LONG)
    text, source = PromptWriter(complete).write(ANNOTATION, "timeOfDay", "night")
    assert source == "llm" and LONG in text
    user = complete.calls[0][1]["content"]
    assert "What is the weather? -> snowy" in user and "time of day is night" in user


@pytest.mark.parametrize("writer", [PromptWriter(), PromptWriter(fake_llm("nope"))])
def test_writer_falls_back_to_template(writer):
    text, source = writer.write(ANNOTATION, "timeOfDay", "night")
    assert source == "template"
    assert "time of day is night" in text and "weather type: snowy" in text


def test_writer_falls_back_when_llm_raises():
    def boom(_messages):
        raise RuntimeError("endpoint down")

    _, source = PromptWriter(boom).write(ANNOTATION, "timeOfDay", "night")
    assert source == "template"


@pytest.fixture
def odd_output(tmp_path: Path) -> Path:
    out = tmp_path / "odd"
    (out / "Text_Annotations").mkdir(parents=True)
    for scene in ("a", "b"):
        (out / "Text_Annotations" / f"{scene}.json").write_text(json.dumps(ANNOTATION))
    # Only scene "a" has a solver-written variation file.
    (out / "a").mkdir()
    variation = json.loads(json.dumps(ANNOTATION))
    variation["sceneDetails"]["timeOfDay"]["answer"] = "Night."
    (out / "a" / "a_var_sceneDetails_timeOfDay_night_run1.json").write_text(json.dumps(variation))
    wfm = {
        "generation_gaps": {
            "sceneDetails": {
                "timeOfDay": {
                    "values": {
                        "night": {
                            "required_count": 2,
                            "suitable_videos": [
                                {"scene_name": "a", "video_path": "/clips/a.mp4"},
                                {"scene_name": "b", "video_path": "/clips/b.mp4"},
                            ],
                        }
                    }
                }
            }
        }
    }
    (out / "wfm_generation_input.json").write_text(json.dumps(wfm))
    return out


@pytest.fixture
def config() -> ODDCoverageConfig:
    return ODDCoverageConfig.load(ODD_DIR / "configs" / "odd_coverage_spec.json")


def test_write_generation_prompts_writes_files_and_index(odd_output, config):
    index = write_generation_prompts(odd_output, config, PromptWriter(fake_llm(LONG)), "m")
    assert [e["scene_name"] for e in index["prompts"]] == ["a", "b"]
    assert index["prompts"][0]["variation_file"] == "a/a_var_sceneDetails_timeOfDay_night_run1.json"
    assert index["prompts"][1]["variation_file"] is None  # derived from the base annotation
    assert (odd_output / "b" / "b_var_sceneDetails_timeOfDay_night_prompt.txt").read_text().startswith(LONG)
    assert json.loads((odd_output / PROMPTS_FILENAME).read_text())["prompt_model"] == "m"


def test_sdg_odd_prefers_odd_paragraphs_over_templates(odd_output, config):
    write_generation_prompts(odd_output, config, PromptWriter(fake_llm(LONG)))
    odd_prompts = sdg_odd.load_odd_prompts(odd_output)
    wfm = json.loads((odd_output / "wfm_generation_input.json").read_text())
    base = {"output_directory": "/out", "top_k": 3}

    runs = sdg_odd.generation_payloads(base, wfm, {"timeOfDay": "Template {value}."}, odd_prompts=odd_prompts)
    assert [r["cosmos"]["prompt"].startswith(LONG) for _, r in runs] == [True, True]

    runs = sdg_odd.generation_payloads(base, wfm, {"timeOfDay": "Template {value}."})
    assert runs[0][1]["cosmos"]["prompt"] == "Template night."
    assert sdg_odd.generation_payloads(base, wfm, {}) == []
