"""
Plan LeRobot language annotation rows onto an edited episode's output frames.

LeRobot 0.6 datasets keep language annotations in two per-frame list columns. ``language_persistent``
repeats an episode's rows on every frame; each row stays active from its ``timestamp`` until a later
row of the same style takes over. ``language_events`` holds rows that fire on the frame they sit on.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from .episode_edits import PlannedFrame, SubtaskSegment, output_indices, remap_subtasks

LANGUAGE_PERSISTENT = "language_persistent"
LANGUAGE_EVENTS = "language_events"
LANGUAGE_COLUMNS = (LANGUAGE_PERSISTENT, LANGUAGE_EVENTS)
LANGUAGE_FEATURE = {"dtype": "language", "shape": [1], "names": None}
SUBTASK_STYLE = "subtask"
TASK_AUG_STYLE = "task_aug"
PLAN_STYLE = "plan"

_TIMESTAMP_TOLERANCE_S = 1e-4
"""Matches LeRobot's frame-timestamp tolerance, so float32 rounding can't push a row onto the next frame."""

LanguageRow = dict[str, Any]


@dataclass(frozen=True)
class LanguageInstruction:
    """An episode's saved language instruction and who saved it, for a LeRobot export."""

    instruction: str
    paraphrases: Sequence[str] = ()
    subtask_instructions: Sequence[str] = ()
    annotator_id: str | None = None
    saved_at: str | None = None
    """ISO 8601 time the annotation was saved."""


def _persistent_row(role: str, content: str, style: str, timestamp: float) -> LanguageRow:
    return {
        "role": role,
        "content": content,
        "style": style,
        "timestamp": timestamp,
        "camera": None,
        "tool_calls": None,
    }


def _sort_key(row: LanguageRow) -> tuple[float, str, str]:
    return float(row["timestamp"]), row.get("style") or "", row.get("role") or ""


def plan_persistent_rows(
    rows: list[LanguageRow],
    source_timestamps: list[float],
    plan: list[PlannedFrame],
    output_timestamps: list[float],
) -> list[LanguageRow]:
    """
    Move persistent rows from the source timeline onto the output frames.

    A row starts on the first source frame at or after its timestamp, or on the next kept frame when
    that frame was removed; a row with no later kept frame is dropped. When removals collapse rows of
    one style, role and camera onto the same output frame, only the rows from the latest source time
    stay, since those are the ones active at that frame.
    """
    positions = output_indices(plan)
    kept = sorted(positions)
    placed: dict[tuple[Any, ...], tuple[float, list[LanguageRow]]] = {}
    for row in rows:
        source_time = float(row["timestamp"])
        frame = bisect_left(source_timestamps, source_time - _TIMESTAMP_TOLERANCE_S)
        index = bisect_left(kept, frame)
        if index == len(kept):
            continue
        key = (row.get("style"), row.get("role"), row.get("camera"), positions[kept[index]])
        current = placed.get(key)
        if current is None or source_time > current[0]:
            placed[key] = (source_time, [row])
        elif source_time == current[0]:
            current[1].append(row)
    planned = [{**row, "timestamp": output_timestamps[key[-1]]} for key, (_, group) in placed.items() for row in group]
    return sorted(planned, key=_sort_key)


def plan_events(events_by_frame: list[list[LanguageRow] | None], plan: list[PlannedFrame]) -> list[list[LanguageRow]]:
    """Keep each kept frame's events and give inserted frames none, so no event fires twice."""
    return [(events_by_frame[frame.source] or []) if frame.following is None else [] for frame in plan]


def subtask_rows(
    subtasks: list[SubtaskSegment], plan: list[PlannedFrame], output_timestamps: list[float]
) -> list[LanguageRow]:
    """
    Return one LeRobot ``subtask`` row per exported subtask, starting on its first output frame.

    LeRobot can't choose between two rows of one style at one time, so when subtasks share an output
    start, the one that starts later in the source wins, then the later one in the request.
    """
    positions = output_indices(plan)
    labels = {}
    for subtask in sorted(subtasks, key=lambda segment: segment.frame_range[0]):
        remapped = remap_subtasks([subtask], positions)
        if remapped:
            labels[remapped[0]["frame_range"][0]] = subtask.label
    return [
        _persistent_row("assistant", label, SUBTASK_STYLE, output_timestamps[start])
        for start, label in sorted(labels.items())
    ]


def instruction_rows(language: LanguageInstruction, timestamp: float) -> list[LanguageRow]:
    """
    Return the ``task_aug`` and ``plan`` rows LeRobot's annotation pipeline writes for an instruction.

    The instruction comes first, then its paraphrases, without duplicates; LeRobot's renderer rotates
    ``${task}`` through these rows. Subtask instructions become one numbered ``plan`` row, the plan the
    pipeline writes at an episode's start.
    """
    phrasings = dict.fromkeys(text.strip() for text in (language.instruction, *language.paraphrases))
    rows = [_persistent_row("user", text, TASK_AUG_STYLE, timestamp) for text in phrasings if text]
    steps = [step.strip() for step in language.subtask_instructions if step.strip()]
    if steps:
        plan = "\n".join(f"{number}. {step}" for number, step in enumerate(steps, start=1))
        rows.append(_persistent_row("assistant", plan, PLAN_STYLE, timestamp))
    return rows


def episode_persistent_rows(
    recorded: list[LanguageRow],
    added: list[LanguageRow],
    replaced_styles: Collection[str],
    source_timestamps: list[float],
    plan: list[PlannedFrame],
    output_timestamps: list[float],
) -> list[LanguageRow]:
    """Return an episode's persistent rows on the output frames; recorded rows of the replaced styles are dropped."""
    kept = [row for row in recorded if row.get("style") not in replaced_styles]
    return sorted(plan_persistent_rows(kept, source_timestamps, plan, output_timestamps) + added, key=_sort_key)


def recorded_subtasks(rows: list[Any] | None, timestamps: Sequence[float]) -> list[SubtaskSegment]:
    """
    Return an episode's recorded ``subtask`` rows as subtasks, each running until the next row starts.

    A row starts on the first frame at or after its timestamp, and rows after the last frame are ignored.
    An empty row ends the previous subtask without starting another. When rows share a frame, the later
    one in the list wins; LeRobot itself refuses such rows as ambiguous.
    """
    starts: dict[int, str] = {}
    for row in rows or []:
        if not isinstance(row, dict) or row.get("style") != SUBTASK_STYLE or row.get("timestamp") is None:
            continue
        frame = bisect_left(timestamps, float(row["timestamp"]) - _TIMESTAMP_TOLERANCE_S)
        if frame < len(timestamps):
            starts[frame] = str(row.get("content") or "").strip()
    frames = sorted(starts)
    if not frames:
        return []
    ends = [following - 1 for following in frames[1:]] + [len(timestamps) - 1]
    spans = [(start, end, starts[start]) for start, end in zip(frames, ends, strict=True) if starts[start]]
    return [
        SubtaskSegment(id=f"recorded-{index}", label=label, frame_range=(start, end), color=None, source="recorded")
        for index, (start, end, label) in enumerate(spans)
    ]
