"""Safe checkpoint deserialization helpers for the evaluation/inference load path."""

from __future__ import annotations

import pickle

import torch


def safe_load_checkpoint(path: str, *, map_location: str = "cpu") -> dict:
    """Load a checkpoint under ``weights_only=True``, failing with actionable guidance.

    ``weights_only=True`` runs the restricted unpickler over the whole checkpoint, so a
    trusted framework checkpoint that stores non-tensor objects (e.g. a numpy scalar in
    ``infos``) alongside ``model_state_dict`` is rejected even though only tensors are read.
    Surface that as a clear error steering the operator to allowlist the offending type
    rather than disabling the safeguard, which would reopen the pickle-RCE vector.

    Args:
        path: Path to the checkpoint file.
        map_location: Map location for torch.load.

    Returns:
        The loaded checkpoint dict.

    Raises:
        ValueError: If the safe unpickler rejects the checkpoint.
    """
    try:
        return torch.load(path, map_location=map_location, weights_only=True)
    except pickle.UnpicklingError as error:
        raise ValueError(
            f"Checkpoint {path} could not be loaded under weights_only=True (safe unpickler). "
            "If it is a trusted framework checkpoint storing non-tensor objects outside model_state_dict, "
            "allowlist those types with torch.serialization.add_safe_globals([...]); do not set "
            f"weights_only=False. Underlying error: {error}"
        ) from error
