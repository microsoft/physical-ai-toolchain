"""Factory for the VLM-as-judge service used by the dataviewer router.

The dataviewer reuses the same :class:`evaluation.vlm_judge.JudgeService`
that the standalone CLI and policy-evaluation pipeline import — there is one
implementation, two consumption surfaces. The service is built lazily on the
first request so importing this module does not load model weights.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from fastapi import Depends, HTTPException, Request

from ..config import AppConfig, get_app_config
from ..validation import validate_path_containment
from .annotation_service import AnnotationService, get_annotation_service
from .dataset_service import DatasetService, get_dataset_service
from .label_storage import BlobLabelStorage, LocalLabelStorage
from .saved_input_service import ViewerDatasetResolver

if TYPE_CHECKING:
    from evaluation.vlm_judge.dataset import EpisodeRecord
    from evaluation.vlm_judge.jobs import JudgeJobs
    from evaluation.vlm_judge.saved_input import SavedInputSnapshot

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


def prepare_job_configuration(options: dict[str, Any]) -> dict[str, Any]:
    from evaluation.vlm_judge.service import ServiceExecutor

    service = get_vlm_judge_service(get_app_config())
    if service is None:
        raise HTTPException(status_code=503, detail="VLM judge is unavailable")
    return ServiceExecutor(service).configuration(options)


async def create_judge_jobs(config: AppConfig, datasets: DatasetService, annotations: AnnotationService) -> JudgeJobs:
    from evaluation.vlm_judge.curation_storage import apply_judge_result
    from evaluation.vlm_judge.job_storage import BlobJobStore, LocalJobStore
    from evaluation.vlm_judge.jobs import JudgeJobs
    from evaluation.vlm_judge.service import ServiceExecutor

    service = get_vlm_judge_service(config)
    if service is None:
        raise HTTPException(status_code=503, detail="VLM judge is unavailable")

    async def prepare_media(record: EpisodeRecord, snapshot: SavedInputSnapshot) -> EpisodeRecord:
        return replace(
            record, video_paths=await datasets.materialize_episode_media(snapshot.dataset_id, record.video_paths)
        )

    def cache_directory(snapshot: SavedInputSnapshot) -> Path:
        root = Path(datasets.base_path)
        return validate_path_containment(
            root.joinpath(*snapshot.dataset_id.split("--"), "annotations", "vlm_judge", snapshot.snapshot_id), root
        )

    if config.storage_backend == "azure":
        provider = datasets._blob_provider
        if provider is None:
            raise HTTPException(status_code=503, detail="Azure judge storage unavailable")
        client = await provider._get_client()
        store = BlobJobStore(
            client.get_container_client(provider.container_name).get_blob_client("_curation/judge/state.json")
        )
        curation = BlobLabelStorage(provider)
    else:
        store = LocalJobStore(
            Path(config.vlm_judge_job_dir)
            if config.vlm_judge_job_dir
            else Path(datasets.base_path) / ".curation" / "judge"
        )
        curation = LocalLabelStorage(datasets.base_path)

    async def apply_result(job: dict[str, Any], target: dict[str, Any]) -> bool:
        return await apply_judge_result(curation, job, target)

    return JudgeJobs(
        store,
        ViewerDatasetResolver(datasets, annotations),
        ServiceExecutor(service, prepare_media=prepare_media, cache_directory=cache_directory),
        capacity_scope=config.vlm_judge_capacity_scope,
        capacity=config.vlm_judge_capacity,
        apply_result=apply_result,
    )


async def get_judge_jobs(
    request: Request,
    config: AppConfig = Depends(get_app_config),
    datasets: DatasetService = Depends(get_dataset_service),
    annotations: AnnotationService = Depends(get_annotation_service),
) -> JudgeJobs:
    jobs = getattr(request.app.state, "judge_jobs", None)
    if jobs is None:
        jobs = await create_judge_jobs(config, datasets, annotations)
        request.app.state.judge_jobs = jobs
    return jobs
