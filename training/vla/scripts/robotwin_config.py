"""RoboTwin 2.0 dataset configuration for TwinVLA training.

Defines task metadata, action space conventions, and dataset paths for the
RoboTwin 2.0 bimanual manipulation benchmark (50 dual-arm tasks, 5 robot
embodiments, 731 objects across 147 categories).

Dataset sources:
  - RLDS format: huggingface.co/jellyho/robotwin2_rlds
  - Raw HDF5: github.com/robotwin-Platform/RoboTwin

Action space: 20D end-effector pose per arm
  Left arm:  [x, y, z, r1, r2, r3, r4, r5, r6, gripper]  (10D)
  Right arm: [x, y, z, r1, r2, r3, r4, r5, r6, gripper]  (10D)
  Rotation: 6D continuous representation (Zhou et al., 2019)

References:
  - RoboTwin 2.0: https://robotwin-platform.github.io/
  - TwinVLA: https://jellyho.github.io/TwinVLA/ (ICLR 2026)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RoboTwinDifficulty(StrEnum):
    """Task difficulty classification from the RoboTwin benchmark."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class RoboTwinCategory(StrEnum):
    """Task category classification."""

    PICK_AND_PLACE = "pick_and_place"
    TOOL_USE = "tool_use"
    ARTICULATED = "articulated"
    COORDINATION = "coordination"
    ASSEMBLY = "assembly"


class DomainRandomizationType(StrEnum):
    """Domain randomization axes supported by RoboTwin 2.0."""

    CLUTTER = "clutter"
    LIGHTING = "lighting"
    BACKGROUND = "background"
    TABLE_HEIGHT = "table_height"
    LANGUAGE = "language"


@dataclass(frozen=True)
class RoboTwinTask:
    """Metadata for a single RoboTwin 2.0 benchmark task."""

    name: str
    description: str
    difficulty: RoboTwinDifficulty
    category: RoboTwinCategory
    num_episodes: int = 0
    requires_coordination: bool = True


# RoboTwin 2.0 task catalog (representative subset for initial integration)
ROBOTWIN_TASKS: dict[str, RoboTwinTask] = {
    "open_laptop": RoboTwinTask(
        name="open_laptop",
        description="Open the laptop lid with both arms",
        difficulty=RoboTwinDifficulty.EASY,
        category=RoboTwinCategory.ARTICULATED,
    ),
    "handover_box": RoboTwinTask(
        name="handover_box",
        description="Hand the box from one arm to the other",
        difficulty=RoboTwinDifficulty.EASY,
        category=RoboTwinCategory.COORDINATION,
    ),
    "pick_and_place_box": RoboTwinTask(
        name="pick_and_place_box",
        description="Pick up the box and place it at the target location",
        difficulty=RoboTwinDifficulty.EASY,
        category=RoboTwinCategory.PICK_AND_PLACE,
    ),
    "box_into_pot": RoboTwinTask(
        name="box_into_pot",
        description="Place the box into the pot using both arms",
        difficulty=RoboTwinDifficulty.MEDIUM,
        category=RoboTwinCategory.COORDINATION,
    ),
    "pour_water": RoboTwinTask(
        name="pour_water",
        description="Pour water from one container to another",
        difficulty=RoboTwinDifficulty.HARD,
        category=RoboTwinCategory.TOOL_USE,
    ),
    "stack_blocks": RoboTwinTask(
        name="stack_blocks",
        description="Stack blocks using coordinated bimanual manipulation",
        difficulty=RoboTwinDifficulty.MEDIUM,
        category=RoboTwinCategory.ASSEMBLY,
    ),
}


@dataclass
class RoboTwinDatasetConfig:
    """Configuration for loading RoboTwin 2.0 datasets for VLA training."""

    hf_repo_id: str = "jellyho/robotwin2_rlds"
    data_format: str = "rlds"
    action_dim: int = 20
    control_hz: int = 25
    image_keys: list[str] = field(
        default_factory=lambda: ["front_image", "wrist_image_left", "wrist_image_right"],
    )
    state_key: str = "state"
    action_key: str = "action"
    language_key: str = "language_instruction"
    image_resolution: tuple[int, int] = (224, 224)
    tasks: list[str] = field(default_factory=lambda: list(ROBOTWIN_TASKS.keys()))
    domain_randomization: list[DomainRandomizationType] = field(default_factory=list)

    @property
    def task_metadata(self) -> dict[str, RoboTwinTask]:
        """Return metadata for configured tasks."""
        return {name: ROBOTWIN_TASKS[name] for name in self.tasks if name in ROBOTWIN_TASKS}


# Tabletop-Sim dataset configuration (TwinVLA custom benchmark)
@dataclass
class TabletopSimDatasetConfig:
    """Configuration for Tabletop-Sim datasets from the TwinVLA paper."""

    hf_repo_id: str = "jellyho/tabletop-simulation-rlds"
    data_format: str = "rlds"
    action_dim: int = 20
    control_hz: int = 50
    tasks: list[str] = field(
        default_factory=lambda: [
            "aloha_handover_box",
            "aloha_dish_drainer",
            "aloha_fold_towel",
            "aloha_tape_measure",
            "aloha_open_jar",
        ],
    )
