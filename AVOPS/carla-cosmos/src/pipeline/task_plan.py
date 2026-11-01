# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Plan CARLA tasks from an ODD ``wfm_generation_input.json``.

Pure Python without container dependencies, so the host-side ``sdg.sh`` helpers can import it.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COMPONENT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_MAP = COMPONENT_DIR / "scenario_map.json"


@dataclass(frozen=True)
class Task:
    name: str
    attribute: str
    value: str
    scenario: str
    prompts: str
    params: dict[str, str]


def load_scenario_map(path: Path = DEFAULT_SCENARIO_MAP) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", name)


def plan_tasks(wfm: dict[str, Any], scenario_map: dict[str, dict[str, Any]]) -> tuple[list[Task], dict[str, int]]:
    """Return (tasks, skipped variation count per ``attribute=value``) for the ODD generation gaps."""
    tasks: list[Task] = []
    skipped: dict[str, int] = {}
    gaps = wfm.get("generation_gaps") or {}
    for attributes in gaps.values():
        for attribute, info in attributes.items():
            for value, spec in (info.get("values") or {}).items():
                count = int(spec.get("required_count", 1))
                entry = scenario_map.get(attribute, {}).get(value.lower())
                if entry is None:
                    skipped[f"{attribute}={value}"] = count
                    continue
                videos = spec.get("suitable_videos") or []
                for index in range(count):
                    source = Path(videos[index]["video_path"]).stem if index < len(videos) else "scene"
                    tasks.append(
                        Task(
                            name=sanitize(f"{attribute}_{value.lower()}_{index + 1}_{source}"),
                            attribute=attribute,
                            value=value.lower(),
                            scenario=entry["scenario"],
                            prompts=entry["prompts"],
                            params=dict(entry.get("params") or {}),
                        )
                    )
    return tasks, skipped


def pick_prompt(prompt_file: Path, rng: random.Random) -> str:
    """Pick one caption, one per line, from a prompt file."""
    captions = [line.strip() for line in prompt_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not captions:
        raise ValueError(f"No prompts in {prompt_file}")
    return rng.choice(captions)


def scenario_params(scenario: str, scenarios_dir: Path) -> dict[str, str]:
    """CARLA capture parameters selecting a scenario file: an XML scenario name or an OpenSCENARIO path."""
    if scenario.lower().endswith(".xml"):
        return {"carla_scenario": scenario[: -len(".xml")]}
    return {"carla_openscenario": str(scenarios_dir / scenario)}
