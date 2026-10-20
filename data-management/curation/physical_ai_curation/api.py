"""HTTP API for the VLM-as-judge service.

Exposes a local-only FastAPI router. The viewer uses a separate authenticated
dataset router backed by the same JudgeService. Standalone judging requires
explicit local opt-in and a confined media root; forwarding is unsupported.

Run as a standalone server:

    uvicorn physical_ai_curation.api:app --host 127.0.0.1 --port 8080 --no-proxy-headers

Mount inside an existing FastAPI app:

    from physical_ai_curation.api import build_router
    from evaluation.vlm_judge.service import JudgeService, ServiceConfig

    judge_service = JudgeService(ServiceConfig(...))
    app.include_router(build_router(judge_service), prefix="/api/vlm-judge")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    from starlette.requests import Request
    from starlette.responses import Response
except ModuleNotFoundError as error:
    if error.name != "starlette":
        raise

from physical_ai_curation.adapters.job_storage import LocalJobStore
from physical_ai_curation.adapters.local_dataset import LocalDatasetResolver
from physical_ai_curation.adapters.local_storage import LocalCurationStorage
from physical_ai_curation.application import apply_judge_result
from physical_ai_curation.jobs import DatasetResolver, JudgeJobs, fingerprint
from physical_ai_curation.saved_input import SavedInputError
from physical_ai_curation.storage import CurationStorage, JobStore

if TYPE_CHECKING:
    from evaluation.vlm_judge.service import JudgeService

_LOGGER = logging.getLogger("evaluation.vlm_judge")


class JudgeRequest(BaseModel):
    """Body for ``POST /judge``."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1)
    episode_index: int = Field(ge=0, strict=True)
    views: list[str] = Field(default_factory=list)
    annotation_author_id: str | None = None
    snapshot_id: str | None = None
    force: bool = Field(default=False, description="Bypass the cache")


class JobOptions(BaseModel):
    """Caller-controlled options, separate from operator-owned runtime identity."""

    model_config = ConfigDict(extra="forbid")
    views: list[str] = Field(default_factory=list, max_length=16)
    annotation_author_id: str | None = Field(default=None, min_length=1, max_length=256)
    process_method: Literal["gvl", "chronological"] | None = None
    force: bool = Field(default=False, strict=True)

    @field_validator("views")
    @classmethod
    def valid_views(cls, values: list[str]) -> list[str]:
        import re

        cleaned = [value.replace("\r", "").replace("\n", "") for value in values]
        if any(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", value) is None for value in cleaned):
            raise ValueError("Invalid camera selection")
        return sorted(set(cleaned))

    @field_validator("annotation_author_id")
    @classmethod
    def clean_author(cls, value: str | None) -> str | None:
        return value.replace("\r", "").replace("\n", "") if value is not None else None


class SampleReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    annotation_author_id: str | None = Field(default=None, min_length=1, max_length=256)
    annotation_revision: str | None = Field(default=None, min_length=1, max_length=200)
    labels_revision: str | None = Field(default=None, min_length=1, max_length=128)
    snapshot_id: str = Field(min_length=1, max_length=128)

    @field_validator("*")
    @classmethod
    def clean_reference(cls, value: str | None) -> str | None:
        return value.replace("\r", "").replace("\n", "") if value is not None else None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledge_exceptions: bool = Field(default=False, strict=True)


class JobRequest(BaseModel):
    """Submit actual episode identifiers, never a guessed contiguous range."""

    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._ -]{0,254}$")
    episode_indices: list[Annotated[int, Field(strict=True, ge=0)]] = Field(min_length=1, max_length=10000)
    options: JobOptions = Field(default_factory=JobOptions)
    snapshot_ids: dict[int, str] = Field(default_factory=dict, max_length=10000)
    mode: Literal["judge", "sample", "judge-and-label"] = "judge"
    approval_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    samples: dict[int, SampleReference] = Field(default_factory=dict, max_length=10000)


class ResetPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._ -]{0,254}$")


class ResetConfirmRequest(ResetPreviewRequest):
    preview_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class ResetPlanRequest(ResetPreviewRequest):
    include_unlisted_runs: bool = False


class ApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_indices: list[Annotated[int, Field(strict=True, ge=0)]] | None = Field(
        default=None, min_length=1, max_length=10000
    )


def job_summary(job: dict[str, Any]) -> dict[str, Any]:
    """Keep submission and listing responses independent of target count."""
    return {key: value for key, value in job.items() if key not in {"targets", "actor", "request_key", "payload_key"}}


def build_job_router(
    get_jobs: Callable[..., Any],
    *,
    actor_dependency: Callable[..., str],
    prepare_config: Callable[[dict[str, Any]], dict[str, Any]],
    mutation_dependencies: list[Any] | None = None,
    route_class: type[Any] | None = None,
) -> Any:
    """Expose the shared lifecycle with caller-supplied authentication and storage."""
    from fastapi import APIRouter, Depends, Header, HTTPException, Query

    router = APIRouter(tags=["judge-jobs"], **({"route_class": route_class} if route_class is not None else {}))
    jobs_dependency = Depends(get_jobs)

    async def invoke(operation: Awaitable[Any]) -> Any:
        try:
            return await operation
        except (KeyError, PermissionError):
            raise HTTPException(status_code=404, detail="Judge job not found") from None
        except SavedInputError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from None
        except ValueError:
            raise HTTPException(
                status_code=409, detail="Judge request conflicts with saved state or configuration"
            ) from None

    @router.post("/resets/preview", status_code=201, dependencies=mutation_dependencies or [])
    async def preview_reset(
        payload: ResetPlanRequest,
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        await invoke(jobs.resolver.episode_indices(payload.dataset_id, principal_scope_id=actor))
        result = await invoke(
            jobs.preview_reset(payload.dataset_id, actor, include_unlisted_runs=payload.include_unlisted_runs)
        )
        return {key: value for key, value in result.items() if key != "actor"}

    @router.post("/resets", status_code=202, dependencies=mutation_dependencies or [])
    async def confirm_reset(
        payload: ResetConfirmRequest,
        request: Request,
        response: Response,
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        await invoke(jobs.resolver.episode_indices(payload.dataset_id, principal_scope_id=actor))
        result = await invoke(jobs.confirm_reset(payload.dataset_id, actor, payload.preview_id))
        response.headers["Location"] = str(
            request.url_for("judge_dataset_reset_status").include_query_params(dataset_id=payload.dataset_id)
        )
        response.headers["Retry-After"] = "1"
        return {key: value for key, value in result.items() if key != "actor"}

    @router.get("/resets", name="judge_dataset_reset_status")
    async def reset_status(
        dataset_id: str = Query(min_length=1, max_length=255),
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any] | None:
        dataset_id = dataset_id.replace("\r", "").replace("\n", "")
        await invoke(jobs.resolver.episode_indices(dataset_id, principal_scope_id=actor))
        try:
            result = await invoke(jobs.reset_status(dataset_id, actor))
        except HTTPException as error:
            if error.status_code != 404:
                raise
            return None
        return {key: value for key, value in result.items() if key != "actor"}

    @router.post("/resets/retry", status_code=202, dependencies=mutation_dependencies or [])
    async def retry_reset(
        payload: ResetPreviewRequest,
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        await invoke(jobs.resolver.episode_indices(payload.dataset_id, principal_scope_id=actor))
        result = await invoke(jobs.retry_reset(payload.dataset_id, actor))
        return {key: value for key, value in result.items() if key != "actor"}

    @router.post("/jobs", status_code=202, dependencies=mutation_dependencies or [])
    async def submit_job(
        payload: JobRequest,
        request: Request,
        response: Response,
        idempotency_key: str = Header(min_length=1, max_length=200),
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        config = prepare_config(payload.options.model_dump(exclude_none=True))
        job = await invoke(
            jobs.submit(
                payload.dataset_id.replace("\r", "").replace("\n", ""),
                actor,
                [int(index) for index in payload.episode_indices],
                config,
                idempotency_key=idempotency_key.replace("\r", "").replace("\n", ""),
                expected_snapshots=payload.snapshot_ids,
                mode=payload.mode,
                approval_id=payload.approval_id,
                samples={
                    int(index): reference.model_dump(exclude_none=True) for index, reference in payload.samples.items()
                },
                require_approval=True,
            )
        )
        response.headers["Location"] = str(request.url_for("judge_job_status", job_id=job["id"]))
        response.headers["Retry-After"] = "1"
        return job_summary(job)

    @router.post("/jobs/{job_id}/approve", status_code=201, dependencies=mutation_dependencies or [])
    async def approve_samples(
        job_id: str,
        payload: ApprovalRequest,
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        approval = await invoke(
            jobs.approve(
                job_id.replace("\r", "").replace("\n", ""),
                actor,
                acknowledge_exceptions=bool(payload.acknowledge_exceptions),
            )
        )
        return {key: value for key, value in approval.items() if key != "actor"}

    @router.get("/approvals")
    async def list_approvals(
        dataset_id: str = Query(min_length=1, max_length=255),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        return await invoke(
            jobs.approvals(
                dataset_id.replace("\r", "").replace("\n", ""),
                actor,
                offset=int(offset),
                limit=int(limit),
            )
        )

    @router.get("/jobs")
    async def list_jobs(
        dataset_id: str = Query(min_length=1, max_length=255),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        page = await invoke(
            jobs.list(dataset_id.replace("\r", "").replace("\n", ""), actor, offset=int(offset), limit=int(limit))
        )
        return {"items": [job_summary(job) for job in page["items"]], "total": page["total"]}

    @router.get("/jobs/{job_id}", name="judge_job_status")
    async def get_job(
        job_id: str,
        response: Response,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        job = await invoke(jobs.get(job_id.replace("\r", "").replace("\n", ""), actor))
        response.headers["Cache-Control"] = "no-store"
        response.headers["Retry-After"] = "1"
        targets = [
            {key: value for key, value in target.items() if key not in {"owner", "fence", "expires_at"}}
            for target in job["targets"][int(offset) : int(offset) + int(limit)]
        ]
        return {**job_summary(job), "targets": targets, "target_offset": int(offset), "target_limit": int(limit)}

    @router.post("/jobs/{job_id}/cancel", dependencies=mutation_dependencies or [])
    async def cancel_job(
        job_id: str,
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        return job_summary(await invoke(jobs.cancel(job_id.replace("\r", "").replace("\n", ""), actor)))

    @router.post("/jobs/{job_id}/retry", status_code=202, dependencies=mutation_dependencies or [])
    async def retry_job(
        job_id: str,
        response: Response,
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        response.headers["Retry-After"] = "1"
        return job_summary(await invoke(jobs.retry(job_id.replace("\r", "").replace("\n", ""), actor)))

    @router.post("/jobs/{job_id}/apply", status_code=202, dependencies=mutation_dependencies or [])
    async def apply_job(
        job_id: str,
        payload: ApplicationRequest,
        request: Request,
        response: Response,
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        job = await invoke(
            jobs.request_application(
                job_id.replace("\r", "").replace("\n", ""),
                actor,
                payload.episode_indices,
            )
        )
        response.headers["Location"] = str(request.url_for("judge_job_status", job_id=job["id"]))
        response.headers["Retry-After"] = "1"
        return job_summary(job)

    @router.get("/episodes")
    async def episode_inventory(
        dataset_id: str = Query(min_length=1, max_length=255),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=250),
        snapshot_id: str | None = Query(default=None, max_length=64),
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        dataset_id = dataset_id.replace("\r", "").replace("\n", "")
        indices = await invoke(jobs.resolver.episode_indices(dataset_id, principal_scope_id=actor))
        revision = fingerprint([dataset_id, indices])
        if snapshot_id is not None and snapshot_id != revision:
            raise HTTPException(status_code=409, detail="Episode inventory changed; refresh selection")
        return {
            "items": indices[int(offset) : int(offset) + int(limit)],
            "total": len(indices),
            "snapshot_id": revision,
        }

    @router.get("/results")
    async def episode_results(
        dataset_id: str = Query(min_length=1, max_length=255),
        episode_index: int = Query(ge=0),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
        actor: str = Depends(actor_dependency),
        jobs: JudgeJobs = jobs_dependency,
    ) -> dict[str, Any]:
        dataset_id = dataset_id.replace("\r", "").replace("\n", "")
        evidence = await invoke(jobs.results(dataset_id, actor, int(episode_index)))
        items = evidence[int(offset) : int(offset) + int(limit)]
        for item in items:
            if item["applicability"] == "withdrawn":
                continue
            try:
                await jobs.resolver.resolve(
                    dataset_id,
                    int(episode_index),
                    principal_scope_id=actor,
                    views=tuple(item["config"].get("views") or ()),
                    annotation_author_id=item["input"]["annotation_author_id"],
                    expected_snapshot_id=item["input"]["snapshot_id"],
                )
            except SavedInputError as error:
                if error.status_code in {401, 403}:
                    raise HTTPException(status_code=error.status_code, detail="Judge evidence access denied") from None
                item["applicability"] = "stale"
            else:
                item["applicability"] = "current"
        return {"items": items, "total": len(evidence)}

    return router


class JudgeResponse(BaseModel):
    """Response for ``POST /judge``."""

    episode_id: str
    instruction: str
    judge_model: str
    prompt_version: str
    n_frames: int
    outcome_success: bool | None
    outcome_confidence: float
    outcome_n_valid_votes: int
    progress_per_frame: list[int]
    voc: float
    milestones: list[dict[str, Any]] = []
    failure_mode: str | None = None


def _local_authority(authority: str, scheme: str) -> tuple[str, str, int]:
    """Parse a literal loopback authority without DNS or forwarded headers."""
    if (
        scheme not in {"http", "https"}
        or not authority
        or any(character.isspace() or character in "/\\?#@%," for character in authority)
    ):
        raise ValueError("Invalid authority")
    parsed = urlsplit(f"//{authority}")
    host = parsed.hostname
    if not host or authority.endswith(":"):
        raise ValueError("Invalid authority")
    if host != "localhost":
        address = ip_address(host)
        if not address.is_loopback:
            raise ValueError("Non-loopback authority")
        host = str(address)
    port = parsed.port
    if port == 0:
        raise ValueError("Invalid port")
    return scheme, host, port if port is not None else (443 if scheme == "https" else 80)


def _admit_local_request(request: Request, *, require_json: bool = True) -> bool:
    """Admit unproxied local requests, requiring JSON for body-bearing routes."""
    try:
        if request.client is None or not ip_address(request.client.host).is_loopback:
            return False
        if any(name == "forwarded" or name.startswith("x-forwarded-") for name in request.headers):
            return False
        hosts = request.headers.getlist("host")
        if len(hosts) != 1:
            return False
        authority = _local_authority(hosts[0], request.scope.get("scheme", "http"))
        origins = request.headers.getlist("origin")
        if len(origins) > 1:
            return False
        if origins:
            origin = urlsplit(origins[0])
            if origin.path or origin.query or origin.fragment:
                return False
            if _local_authority(origin.netloc, origin.scheme) != authority:
                return False
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return False
        if request.method in {"GET", "HEAD"}:
            return True
        content_types = request.headers.getlist("content-type")
        if not require_json and not content_types:
            return True
        return len(content_types) == 1 and content_types[0].split(";", 1)[0].strip().lower() == "application/json"
    except ValueError:
        return False


def build_router(
    service: JudgeService | None,
    *,
    resolver: DatasetResolver | None = None,
    principal_dependency: Callable[..., str] | None = None,
    local_actor: str | None = None,
    job_store: JobStore | None = None,
    curation_store: CurationStorage | None = None,
    capacity: int = 1,
    capacity_scope: str = "configured-inference-device",
    allow_unauthenticated: bool | None = None,
) -> Any:
    """Return a FastAPI router bound to ``service``."""
    from fastapi import APIRouter, Depends, Header, HTTPException
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from fastapi.routing import APIRoute

    enabled = (
        allow_unauthenticated
        if allow_unauthenticated is not None
        else os.environ.get("VLM_JUDGE_ALLOW_UNAUTHENTICATED", "").lower() == "true"
    )
    configured = (
        resolver is not None
        and job_store is not None
        and bool(principal_dependency or local_actor)
        and (
            not isinstance(resolver, LocalDatasetResolver)
            or (bool(resolver.datasets) and all(root != Path(root.anchor) for root in resolver.datasets.values()))
        )
    )

    class LocalJudgeRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            handler = super().get_route_handler()
            if self.name == "health":
                return handler

            async def admitted_handler(request: Request) -> Response:
                if not enabled or not configured:
                    raise HTTPException(status_code=503, detail="Judge unavailable")
                if not _admit_local_request(request, require_json=self.body_field is not None):
                    raise HTTPException(status_code=403, detail="Request forbidden")
                try:
                    return await handler(request)
                except RequestValidationError:
                    return JSONResponse(status_code=422, content={"detail": "Invalid request"})

            return admitted_handler

    executor = None
    if service is not None:
        from physical_ai_curation.adapters.evaluator import ServiceExecutor

        executor = ServiceExecutor(service)

    def prepare_config(options: dict[str, Any]) -> dict[str, Any]:
        if executor is None:
            raise HTTPException(status_code=503, detail="Judge unavailable")
        return executor.configuration(options)

    if curation_store is None and isinstance(resolver, LocalDatasetResolver):
        curation_store = LocalCurationStorage(resolver.datasets)

    async def apply_result(job: dict[str, Any], target: dict[str, Any]) -> bool:
        if curation_store is None:
            raise ValueError("Curation storage is not configured")
        return await apply_judge_result(curation_store, job, target)

    jobs = (
        JudgeJobs(
            job_store,
            resolver,
            executor,
            capacity=capacity,
            capacity_scope=capacity_scope,
            apply_result=apply_result if curation_store is not None else None,
            curation_storage=curation_store,
        )
        if job_store is not None and resolver is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        workers = []
        if enabled and configured and jobs is not None:
            app.state.judge_jobs = jobs
            workers = [asyncio.create_task(jobs.run_forever()) for _ in range(capacity)]
        try:
            yield
        finally:
            for worker in workers:
                worker.cancel()
            for worker in workers:
                with suppress(asyncio.CancelledError):
                    _ = await worker

    router = APIRouter(tags=["vlm-judge"], route_class=LocalJudgeRoute, lifespan=lifespan)

    def require_local_actor() -> str:
        if not local_actor:
            raise HTTPException(status_code=503, detail="Configure a principal dependency or explicit local actor")
        return local_actor

    def require_jobs(request: Request) -> JudgeJobs:
        if jobs is None:
            raise HTTPException(status_code=503, detail="Configure saved dataset resolution and durable job storage")
        request.app.state.judge_jobs = jobs
        return jobs

    router.include_router(
        build_job_router(
            require_jobs,
            actor_dependency=principal_dependency or require_local_actor,
            prepare_config=prepare_config,
            route_class=LocalJudgeRoute,
        )
    )

    @router.get("/health")
    def health() -> dict[str, Any]:
        if service is None:
            return {"status": "unavailable", "model_id": None, "backend_kind": None, "cache_enabled": False}
        return {
            "status": "ok",
            "model_id": service.model_id,
            "backend_kind": service.config.backend.kind,
            "cache_enabled": service.config.cache_dir is not None,
        }

    @router.post("/judge", status_code=202)
    async def judge(
        request: JudgeRequest,
        http_request: Request,
        response: Response,
        idempotency_key: str | None = Header(default=None, min_length=1, max_length=200),
        actor: str = Depends(principal_dependency or require_local_actor),
    ) -> dict[str, Any]:
        if resolver is None:
            raise HTTPException(status_code=503, detail="Saved dataset resolver is not configured")
        manager = require_jobs(http_request)
        try:
            options = JobOptions(
                views=request.views, annotation_author_id=request.annotation_author_id, force=request.force
            )
            job = await manager.submit(
                request.dataset_id.replace("\r", "").replace("\n", ""),
                actor,
                [int(request.episode_index)],
                prepare_config(options.model_dump()),
                idempotency_key=(idempotency_key or uuid4().hex).replace("\r", "").replace("\n", ""),
                expected_snapshots={int(request.episode_index): request.snapshot_id} if request.snapshot_id else None,
            )
        except SavedInputError as err:
            raise HTTPException(status_code=err.status_code, detail=str(err)) from err
        except ValueError:
            raise HTTPException(
                status_code=409, detail="Judge request conflicts with saved state or configuration"
            ) from None
        response.headers["Location"] = str(http_request.url_for("judge_job_status", job_id=job["id"]))
        response.headers["Retry-After"] = "1"
        return job_summary(job)

    return router


def build_app():
    """Construct a standalone FastAPI app from environment variables.

    Environment variables:

    - ``VLM_JUDGE_BACKEND``  (default ``echo`` for safe boot without GPU)
    - ``VLM_JUDGE_MODEL_ID`` (default ``Qwen/Qwen3-VL-4B-Instruct``)
    - ``VLM_JUDGE_MODEL_REVISION`` (immutable HF commit SHA to pin the qwen3-vl download)
    - ``VLM_JUDGE_BASE_URL`` (required for ``openai-compat``)
    - ``VLM_JUDGE_API_KEY``  (optional API key for ``openai-compat``)
    - ``VLM_JUDGE_N_FRAMES`` (default ``12``)
    - ``VLM_JUDGE_CACHE_DIR`` (default ``outputs/vlm-judge/cache``)
    - ``VLM_JUDGE_ALLOW_UNAUTHENTICATED`` (must be ``true`` for local judging)
    - ``VLM_JUDGE_DATASETS`` (JSON mapping of dataset IDs to confined local roots)
    - ``VLM_JUDGE_LOCAL_ACTOR`` (explicit local principal)
    - ``VLM_JUDGE_JOB_DIR`` (durable local job storage)
    """
    from fastapi import FastAPI
    from fastapi.openapi.docs import get_swagger_ui_html
    from fastapi.responses import HTMLResponse

    try:
        from evaluation.vlm_judge.service import BackendConfig, FrameConfig, JudgeService, ServiceConfig
    except ImportError:
        service = None
    else:
        backend = BackendConfig(
            kind=os.environ.get("VLM_JUDGE_BACKEND", "echo"),
            model_id=os.environ.get("VLM_JUDGE_MODEL_ID", "Qwen/Qwen3-VL-4B-Instruct"),
            revision=os.environ.get("VLM_JUDGE_MODEL_REVISION") or None,
            base_url=os.environ.get("VLM_JUDGE_BASE_URL") or None,
            api_key=os.environ.get("VLM_JUDGE_API_KEY") or None,
        )
        frames = FrameConfig(n_frames=int(os.environ.get("VLM_JUDGE_N_FRAMES", "12")))
        cache_dir_env = os.environ.get("VLM_JUDGE_CACHE_DIR", "outputs/vlm-judge/cache")
        cache_dir = Path(cache_dir_env) if cache_dir_env else None
        service = JudgeService(ServiceConfig(backend=backend, frames=frames, cache_dir=cache_dir))
    app = FastAPI(title="VLM-as-Judge", version="0.1.0", docs_url=None)

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        response = get_swagger_ui_html(openapi_url=app.openapi_url, title=f"{app.title} - Swagger UI")
        styles = """<style>
@media (max-width: 600px) {
  .swagger-ui .wrapper { padding-inline: 10px; }
  .swagger-ui table { table-layout: fixed; width: 100%; }
  .swagger-ui table .response-col_status { width: 48px; }
  .swagger-ui table .response-col_links { display: none; }
  .swagger-ui table .response-col_description { width: auto; }
  .swagger-ui .model-example,
  .swagger-ui .response-col_description__inner { min-width: 0; max-width: 100%; }
  .swagger-ui .highlight-code,
  .swagger-ui .microlight,
  .swagger-ui pre { max-width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; }
}
</style>"""
        return HTMLResponse(response.body.decode("utf-8").replace("</head>", f"{styles}</head>"))

    configured = json.loads(os.environ.get("VLM_JUDGE_DATASETS", "{}"))
    resolver = LocalDatasetResolver({identifier: Path(root) for identifier, root in configured.items()})
    local_actor = (
        os.environ.get("VLM_JUDGE_LOCAL_ACTOR") if os.environ.get("VLM_JUDGE_ALLOW_UNAUTHENTICATED") == "true" else None
    )
    job_dir = os.environ.get("VLM_JUDGE_JOB_DIR")
    app.include_router(
        build_router(
            service,
            resolver=resolver,
            local_actor=local_actor,
            job_store=LocalJobStore(Path(job_dir)) if job_dir else None,
            capacity=int(os.environ.get("VLM_JUDGE_CAPACITY", "1")),
            capacity_scope=os.environ.get("VLM_JUDGE_CAPACITY_SCOPE", "configured-inference-device"),
        )
    )
    _LOGGER.info("Curation API ready (inference_available=%s)", service is not None)
    return app


try:
    app = build_app()
except ModuleNotFoundError as error:
    if error.name not in {"fastapi", "starlette"}:
        raise
    app = None
