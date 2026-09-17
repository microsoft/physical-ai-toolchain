"""Separate policy-visible observations from task-private measurement data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np


@dataclass(frozen=True)
class Frame:
    """One measured simulator frame; only state and images may cross into inference."""

    state: np.ndarray
    images: dict[str, np.ndarray]
    evidence: dict[str, np.ndarray]


class Simulation(Protocol):
    """Finite joint-target simulation adapter with task-owned success measurement."""

    provenance: dict[str, Any]
    action_limits: np.ndarray
    initial_command: np.ndarray

    def reset(self, seed: int) -> Frame:
        """Reset the selected seed and return pre-command observations."""
        ...

    def step(self, action: np.ndarray) -> Frame:
        """Apply one absolute target for one control interval and return the post-command frame."""
        ...

    def score(self, data: dict[str, np.ndarray], initial: Frame) -> dict[str, Any]:
        """Return task metrics, not process-success or hardware-validation claims."""
        ...

    def hold(self) -> None:
        """Hold achieved joint positions after a failed policy request without advancing simulation."""
        ...

    def close(self) -> None:
        """Release the selected simulator resources."""
        ...
