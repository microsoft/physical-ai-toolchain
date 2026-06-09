"""Factory for the VLM-as-judge service used by the dataviewer router.

The dataviewer reuses the same :class:`evaluation.vlm_judge.JudgeService`
that the standalone CLI and policy-evaluation pipeline import — there is one
implementation, two consumption surfaces. The service is built lazily on the
first request so importing this module does not load model weights.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import AppConfig

logger = logging.getLogger(__name__)

_service = None


def get_vlm_judge_service(config: AppConfig):
    """Return the singleton ``JudgeService``, building it on first call.

    Returns ``None`` when ``vlm_judge_enabled`` is false so callers can skip
    mounting the router without conditional imports of optional deps.
    """
    global _service
    if not config.vlm_judge_enabled:
        return None
    if _service is not None:
        return _service

    try:
        from evaluation.vlm_judge import (
            BackendConfig,
            FrameConfig,
            JudgeService,
            ServiceConfig,
        )
    except ImportError as err:
        logger.warning(
            "VLM judge unavailable: evaluation.vlm_judge import failed (%s); "
            "ensure the evaluation package is on PYTHONPATH",
            err,
        )
        return None

    cache_dir = Path(config.vlm_judge_cache_dir) if config.vlm_judge_cache_dir else None
    backend = BackendConfig(
        kind=config.vlm_judge_backend,
        model_id=config.vlm_judge_model_id,
        base_url=config.vlm_judge_base_url,
        api_key=config.vlm_judge_api_key,
    )
    frames = FrameConfig(n_frames=config.vlm_judge_n_frames)
    _service = JudgeService(
        ServiceConfig(backend=backend, frames=frames, cache_dir=cache_dir),
    )
    logger.info(
        "VLM judge service ready: backend=%s model=%s cache_dir=%s",
        backend.kind,
        backend.model_id,
        cache_dir,
    )
    return _service


def reset_vlm_judge_service() -> None:
    """Drop the cached service singleton (used by tests)."""
    global _service
    _service = None
