"""Task-findings inference from caller-resolved media and instruction."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from threading import Lock
from typing import TYPE_CHECKING, Any

from .backend import GenerationConfig, Qwen3VLBackend
from .frames import FrameWindow, extract_frames, tile_horizontally

if TYPE_CHECKING:
    from physical_ai_dataset_io.lerobot import EpisodeRecord

ANALYSIS_FIELDS = (
    "pick_from",
    "object",
    "grasp_success",
    "place_success",
    "movement_quality",
    "notes",
)

SYSTEM_PROMPT = (
    "You are a meticulous robotics data annotator reviewing remotely operated "
    "robot-arm manipulation episodes. You analyze multi-view camera frames and "
    "report precise, objective labels. You never guess wildly: when the "
    "evidence is ambiguous you say so. You always answer with a single strict "
    "JSON object and nothing else."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def build_user_prompt(
    *,
    n_frames: int,
    views: Sequence[str],
    instruction: str | None,
    scene_context: str | None = None,
) -> str:
    """Compose the per-episode user prompt, describing the tiled views in order."""
    if len(views) == 1:
        view_desc = f"Each image is a single camera view: {views[0]}."
    else:
        ordered = ", ".join(f"{index + 1}) {view}" for index, view in enumerate(views))
        view_desc = f"Each image tiles {len(views)} camera views side by side, left to right: {ordered}."
    task_line = (
        f'The task for this episode is: "{instruction}".'
        if instruction
        else "The task is a manipulation (pick-and-place) episode."
    )
    scene_line = f"\n{scene_context.strip()}\n" if scene_context else ""
    return f"""These {n_frames} images are frames sampled in temporal order (first = start of \
the episode, last = end) from ONE robot-arm episode.

{view_desc}

{task_line}
{scene_line}
Watch the whole sequence, then label this episode. Report:
1. pick_from: a short phrase for where the object is picked FROM (e.g. "front", \
"right", "left", "table", "bin", or "uncertain").
2. object: a short noun phrase naming the object being picked up (e.g. "red cube", \
"wooden block", "small toy"). Use "unclear" only if truly indeterminable.
3. grasp_success: true if the gripper closed on the object and lifted it clear of \
the source location; false otherwise.
4. place_success: true if the object was released and came to rest at the intended \
destination; false otherwise.
5. movement_quality: ONE concise sentence assessing the arm's motion (smoothness, \
hesitation, retries, collisions, or overall efficiency).
6. notes: at most one short sentence of supporting evidence (optional, may be "").

Respond with ONLY this JSON object, no markdown, no prose:
{{"pick_from": "...", "object": "...", "grasp_success": true, "place_success": true, \
"movement_quality": "...", "notes": "..."}}"""


def parse_label(text: str) -> dict[str, Any]:
    """Extract the JSON object from a model response, tolerating code fences."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped).strip()
    match = _JSON_RE.search(stripped)
    if not match:
        raise ValueError("No JSON object in model output")
    return json.loads(match.group(0))


def normalize_label(label: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the distinct task-findings result family."""
    if not isinstance(label, dict):
        raise ValueError("Invalid task label result")
    for name in ("pick_from", "object", "movement_quality"):
        if not isinstance(label.get(name), str) or not label[name].strip():
            raise ValueError(f"Invalid task label result field: {name}")
    for name in ("grasp_success", "place_success"):
        if type(label.get(name)) is not bool:
            raise ValueError(f"Invalid task label result field: {name}")
    if not isinstance(label.get("notes", ""), str):
        raise ValueError("Invalid task label result field: notes")
    return {
        "pick_from": label["pick_from"].lower(),
        "object": label["object"],
        "grasp_success": label["grasp_success"],
        "place_success": label["place_success"],
        "movement_quality": label["movement_quality"],
        "notes": label.get("notes", ""),
        "error": None,
    }


def build_filmstrip(
    record: EpisodeRecord,
    *,
    views: Sequence[str],
    n_frames: int,
    frame_size: int,
) -> list[Any]:
    """Tile selected views using their individual episode windows."""
    target = (frame_size, frame_size)
    per_view = []
    for view in views:
        start, end = record.video_windows.get(view, (record.from_timestamp, record.to_timestamp))
        window = FrameWindow(path=record.video_paths[view], from_s=start, to_s=end)
        per_view.append(extract_frames(window, n_frames=n_frames, target_size=target))
    return tile_horizontally(per_view) if len(per_view) > 1 else per_view[0]


class TaskFindingsService:
    """Score caller-resolved media without consulting curation state."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.backend = None
        self.lock = Lock()

    def evaluate(self, record: EpisodeRecord, *, instruction: str) -> dict[str, Any]:
        with self.lock:
            if self.backend is None:
                self.backend = Qwen3VLBackend(
                    model_id=self.config["model_id"],
                    revision=self.config["model_revision"],
                    device_map=self.config["device_map"],
                    dtype=self.config["dtype"],
                )
        frames = build_filmstrip(
            record, views=self.config["views"], n_frames=self.config["n_frames"], frame_size=self.config["frame_size"]
        )
        raw = self.backend.generate(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(
                n_frames=self.config["n_frames"],
                views=self.config["views"],
                instruction=instruction,
                scene_context=self.config["scene_context"],
            ),
            images=frames,
            config=GenerationConfig(**self.config["generation"]),
        )
        row = normalize_label(parse_label(raw))
        return {
            "episode_id": record.episode_id,
            "instruction": instruction,
            "judge_model": self.config["model_id"],
            "prompt_version": self.config["prompt_version"],
            "findings": {field: row[field] for field in ANALYSIS_FIELDS},
        }
