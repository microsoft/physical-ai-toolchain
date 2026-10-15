"""Resolved inference inputs with no curation authority or persistence callbacks."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

if TYPE_CHECKING:
    from physical_ai_curation.evidence import EvidenceRecord


class ResolvedView(BaseModel):
    """One authorized selected view in caller-defined order."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str = Field(min_length=1)
    path: Path
    window: tuple[float, float] | None = None
    media_identity: str | None = None


class ExecutionOptions(BaseModel):
    """Inference-only options; paths are trusted adapter inputs, never HTTP parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    process_method: Literal["gvl", "chronological"] = "gvl"
    force: bool = False
    from_s: float | None = None
    to_s: float | None = None
    cache_dir: Path | None = None


class JudgeInvocation(BaseModel):
    """Caller-owned correlation plus resolved media and instruction, not a saved snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    invocation_id: str = Field(min_length=1)
    input_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    views: tuple[ResolvedView, ...]
    options: ExecutionOptions
    declared_identity: dict[str, JsonValue]


class JudgePort(Protocol):
    async def evaluate(self, invocation: JudgeInvocation) -> EvidenceRecord:
        """Return bound evidence without applying annotations or deciding author scope."""
        ...
