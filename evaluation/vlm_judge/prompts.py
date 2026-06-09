"""Prompt templates and parsers for VLM-as-judge evaluation.

Two prompt patterns:

* ``OUTCOME_MCQ`` — two-pass multiple-choice for binary task success with
  N-sample self-consistency voting (Cosmos-Reason1 protocol, arXiv:2503.15558).
* ``PROCESS_GVL`` — shuffle-and-rank dense per-frame progress (GVL,
  arXiv:2411.04549). Frame 0 is anchored; the remaining frames are presented
  in random order; the resulting Value-Order Correlation (VOC) is the
  process-reward signal and a low-cost trajectory-quality metric.
"""

from __future__ import annotations

import json
import logging
import random
import re

_LOGGER = logging.getLogger("evaluation.vlm_judge")

PROMPT_VERSION = "outcome-mcq-v1+gvl-process-v1+milestones-v1+failuremode-v1"


# -------------------------------------------------------------------------
# Outcome MCQ
# -------------------------------------------------------------------------


OUTCOME_SYSTEM_PROMPT = (
    "You are a strict evaluator of robot manipulation task videos. "
    "You answer in the exact format requested and never add commentary."
)


OUTCOME_USER_TEMPLATE = """\
Watch the {n_frames} frames presented in chronological order.

TASK INSTRUCTION: {instruction}

Decide whether the robot SUCCESSFULLY completed the stated task by the final frame.

Reasoning rules:
- "SUCCESS" requires that the desired terminal state is clearly visible in the last few frames.
- "FAILURE" if the task is incomplete, the robot drops or misses the target, or the final state is wrong.
- A partial attempt without the final goal state is FAILURE.

Reason briefly inside <think>...</think> (max 200 tokens), then output exactly one of:

  <answer>A</answer>   - SUCCESS
  <answer>B</answer>   - FAILURE

Do not output anything after </answer>."""


_ANSWER_RE = re.compile(r"<answer>\s*([AB])\s*</answer>", re.IGNORECASE)


def render_outcome_prompt(*, instruction: str, n_frames: int) -> str:
    return OUTCOME_USER_TEMPLATE.format(instruction=instruction, n_frames=n_frames)


def parse_outcome_response(text: str) -> bool | None:
    """Return ``True`` (SUCCESS), ``False`` (FAILURE), or ``None`` on format violation."""
    match = _ANSWER_RE.search(text or "")
    if match is None:
        return None
    return match.group(1).upper() == "A"


# -------------------------------------------------------------------------
# Process — GVL shuffle-and-rank
# -------------------------------------------------------------------------


PROCESS_SYSTEM_PROMPT = (
    "You estimate task-completion progress at each frame of a robot manipulation video. "
    "You always output a strict JSON array and nothing else."
)


PROCESS_USER_TEMPLATE = """\
You will see {n_frames} frames sampled from a robot manipulation episode.

The first frame shown is the FIRST frame of the trajectory (anchored at progress 0). \
The remaining {n_shuffled} frames are presented in RANDOM ORDER (NOT chronological).

TASK INSTRUCTION: {instruction}

For each frame index i = 1..{n_frames} in the order shown, output an integer 0-100 \
estimating how much of the task is completed in that frame:
  - 0 = nothing of the task is yet started.
  - 100 = the task is fully complete and the goal state is achieved.
  - Intermediate values reflect the visible progress (approach, grasp, transport, place).

Output ONLY a single JSON array of {n_frames} integers and nothing else. Example:

  [0, 27, 13, 88, 41, 100]
"""


def render_process_prompt(*, instruction: str, n_frames: int) -> str:
    return PROCESS_USER_TEMPLATE.format(
        instruction=instruction,
        n_frames=n_frames,
        n_shuffled=max(0, n_frames - 1),
    )


_JSON_ARRAY_RE = re.compile(r"\[[^\[\]]*\]", re.DOTALL)


def parse_process_response(text: str, *, n_frames: int) -> list[int] | None:
    """Extract the first JSON integer array of length ``n_frames`` from ``text``."""
    if not text:
        return None
    match = _JSON_ARRAY_RE.search(text)
    if match is None:
        return None
    try:
        values = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(values, list) or len(values) != n_frames:
        return None
    out: list[int] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        out.append(round(float(v)))
    return out


def shuffle_with_anchor(
    n_frames: int,
    *,
    rng: random.Random | None = None,
) -> list[int]:
    """Return an ordering with index 0 fixed first and the rest randomly permuted."""
    if n_frames <= 0:
        raise ValueError(f"n_frames must be positive, got {n_frames}")
    rng = rng or random.Random(0)
    tail = list(range(1, n_frames))
    rng.shuffle(tail)
    return [0, *tail]


# -------------------------------------------------------------------------
# Milestone decomposition
# -------------------------------------------------------------------------


MILESTONE_SYSTEM_PROMPT = (
    "You decompose robot manipulation videos into 3-5 atomic milestones "
    "and judge each one as completed or not. You always emit strict JSON."
)


MILESTONE_USER_TEMPLATE = """\
A robot manipulation episode was just judged: {outcome_label}.

TASK INSTRUCTION: {instruction}

The {n_frames} frames are presented in chronological order, indexed 0..{last_idx}.

Decompose the task into 3-5 atomic milestones (e.g., "approach_object", "grasp_object",
"lift_clear", "transport", "place_in_target"). For each milestone, output:
  - "name": short snake_case verb phrase
  - "completed": true if the milestone is visibly achieved in the video, else false
  - "frame_range": "<start>-<end>" using frame indices that bracket the observation
  - "evidence": one short sentence citing what is visible in those frames

To minimise hallucination, only mark "completed": true when you can cite a specific
frame range showing the achievement. If you are unsure, mark it false.

Output ONLY a JSON object of the form:

  {{
    "milestones": [
      {{"name": "approach_object", "completed": true, "frame_range": "0-3", "evidence": "..."}},
      ...
    ]
  }}

Do not output anything else."""


_MILESTONE_OBJECT_RE = re.compile(r"\{[^{}]*?\"milestones\"\s*:\s*\[.*?\]\s*\}", re.DOTALL)


def render_milestone_prompt(
    *,
    instruction: str,
    n_frames: int,
    outcome_success: bool | None,
) -> str:
    if outcome_success is True:
        outcome_label = "SUCCESS"
    elif outcome_success is False:
        outcome_label = "FAILURE"
    else:
        outcome_label = "UNKNOWN (verify each step)"
    return MILESTONE_USER_TEMPLATE.format(
        instruction=instruction,
        n_frames=n_frames,
        last_idx=max(0, n_frames - 1),
        outcome_label=outcome_label,
    )


def parse_milestone_response(text: str) -> list[dict[str, object]] | None:
    """Extract the milestones list from a JSON object response, or ``None``."""
    if not text:
        return None
    match = _MILESTONE_OBJECT_RE.search(text)
    candidate = match.group(0) if match else text.strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    milestones = payload.get("milestones")
    if not isinstance(milestones, list) or not milestones:
        return None
    cleaned: list[dict[str, object]] = []
    for entry in milestones:
        if not isinstance(entry, dict):
            return None
        name = entry.get("name")
        completed = entry.get("completed")
        frame_range = entry.get("frame_range")
        if not isinstance(name, str) or not isinstance(completed, bool):
            return None
        cleaned.append(
            {
                "name": name,
                "completed": completed,
                "frame_range": str(frame_range) if frame_range is not None else "",
                "evidence": str(entry.get("evidence", "")),
            },
        )
    return cleaned


# -------------------------------------------------------------------------
# Failure-mode classification
# -------------------------------------------------------------------------


FAILURE_MODES: tuple[tuple[str, str], ...] = (
    ("A", "missed_grasp"),
    ("B", "wrong_object"),
    ("C", "dropped"),
    ("D", "target_not_reached"),
    ("E", "collision_or_unsafe"),
    ("F", "early_termination"),
    ("G", "other"),
)


FAILURE_SYSTEM_PROMPT = (
    "You attribute robot manipulation failures to a single failure mode "
    "from a fixed list. You always answer in the requested format."
)


_FAILURE_LIST_TEXT = "\n".join(f"  {letter}) {label}" for letter, label in FAILURE_MODES)


FAILURE_USER_TEMPLATE = f"""\
The robot did NOT complete the task. Choose ONE failure mode that best explains why.

TASK INSTRUCTION: {{instruction}}

Failure modes (pick exactly one letter):
{_FAILURE_LIST_TEXT}

Reason briefly inside <think>...</think> (max 120 tokens), then output exactly:

  <answer>X</answer>

where X is one of A B C D E F G. Do not output anything after </answer>."""


_FAILURE_LETTER_RE = re.compile(r"<answer>\s*([A-Ga-g])\s*</answer>")
_FAILURE_LABEL_BY_LETTER = {letter: label for letter, label in FAILURE_MODES}


def render_failure_prompt(*, instruction: str) -> str:
    return FAILURE_USER_TEMPLATE.format(instruction=instruction)


def parse_failure_response(text: str) -> str | None:
    """Return the snake_case failure-mode label, or ``None`` on format violation."""
    match = _FAILURE_LETTER_RE.search(text or "")
    if match is None:
        return None
    return _FAILURE_LABEL_BY_LETTER.get(match.group(1).upper())
