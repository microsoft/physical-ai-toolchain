"""Factory for the VLM-as-judge service used by the dataviewer router.

The dataviewer reuses the same :class:`evaluation.vlm_judge.JudgeService`
that the standalone CLI and policy-evaluation pipeline import — there is one
implementation, two consumption surfaces. The service is built lazily on the
first request so importing this module does not load model weights.
"""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock

from ..config import AppConfig

logger = logging.getLogger(__name__)

_service = None
_service_lock = Lock()
_PROCESS_METHODS = ("gvl", "chronological")


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

    with _service_lock:
        if _service is not None:
            return _service

        try:
            from evaluation.vlm_judge import (
                AgentConfig,
                BackendConfig,
                FrameConfig,
                JudgeService,
                ServiceConfig,
            )
        except ImportError as err:
            logger.warning(
                "VLM judge unavailable: evaluation.vlm_judge import failed (%s); install the backend vlm-judge extra",
                err,
            )
            return None

        cache_dir = Path(config.vlm_judge_cache_dir) if config.vlm_judge_cache_dir else None
        backend = BackendConfig(
            kind=config.vlm_judge_backend,
            model_id=config.vlm_judge_model_id,
            revision=config.vlm_judge_model_revision,
            base_url=config.vlm_judge_base_url,
            api_key=config.vlm_judge_api_key,
        )
        frames = FrameConfig(n_frames=config.vlm_judge_n_frames)
        method = config.vlm_judge_process_method
        if method not in _PROCESS_METHODS:
            logger.warning(
                "Invalid VLM_JUDGE_PROCESS_METHOD=%r; falling back to 'gvl'",
                method,
            )
        process_method = method if method in _PROCESS_METHODS else "gvl"
        agent = AgentConfig(process_method=process_method)
        _service = JudgeService(
            ServiceConfig(backend=backend, frames=frames, agent=agent, cache_dir=cache_dir),
        )
        logger.info(
            "VLM judge service ready: backend=%s model=%s process=%s cache_dir=%s",
            backend.kind,
            backend.model_id,
            process_method,
            cache_dir,
        )
        return _service


def reset_vlm_judge_service() -> None:
    """Drop the cached service singleton (used by tests)."""
    global _service
    with _service_lock:
        _service = None
