"""Typed requests for server-executed quality runs."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..quality.profiles import QualityProfile
from ..validation import SanitizedModel
from .reviews import ContractId


class QualityRunRequest(SanitizedModel):
    """Inputs for strict source inspection and persisted quality evidence."""

    run_id: ContractId
    actor_id: ContractId
    source_format: Literal["lerobot", "hdf5"]
    profile: QualityProfile
    reason: str | None = Field(default=None, max_length=2000)
