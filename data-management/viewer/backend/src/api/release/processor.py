"""Execute durable release jobs through verification and publication."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from ..models.release_workflow import EligibilityCandidate, ReleaseEpisodeSelection, ReleaseJobRequest
from ..models.releases import PackageQualityReport, QualityEvidenceReference, ReleaseFormat, ReleaseManifest
from ..models.reviews import QualityOutcome, QualityReport, ReviewDecision
from ..services.hdf5_loader import HDF5Loader
from ..services.lerobot_loader import LeRobotLoader
from ..services.release_workflow_service import ReleaseWorkflowService
from ..storage.review_base import ReviewRepository
from ..storage.source_workspace import SourceWorkspace
from .blob_publisher import BlobReleasePublisher
from .integrity import finalize_release, verify_release
from .jobs import JobState, ReleaseJobStore
from .lerobot import LeRobotReleaseAdapter, ReleaseEpisode, ReleaseEpisodeReference
from .local_publisher import LocalReleasePublisher, PublicationConflictError


@dataclass(frozen=True)
class ReleaseAssembly:
    """Staged package metadata and immutable decision ledgers."""

    manifest: ReleaseManifest
    accepted: tuple[ReviewDecision, ...]
    rejected: tuple[ReviewDecision, ...]
    quality_reports: tuple[QualityReport, ...]
    package_quality: PackageQualityReport
    excluded: tuple[EligibilityCandidate, ...] = ()


ReleaseAssembler = Callable[
    [ReleaseJobRequest, tuple[ReleaseEpisodeSelection, ...], Path],
    Awaitable[ReleaseAssembly],
]
AzurePublisherFactory = Callable[[], Awaitable[BlobReleasePublisher]]


class ReleasePackageAssembler:
    """Build release packages from immutable decisions and source workspaces."""

    def __init__(
        self,
        repository: ReviewRepository,
        source_workspace: SourceWorkspace,
        *,
        worker: LeRobotReleaseAdapter | None = None,
    ) -> None:
        self._repository = repository
        self._source_workspace = source_workspace
        self._worker = worker or LeRobotReleaseAdapter()

    async def __call__(
        self,
        request: ReleaseJobRequest,
        selections: tuple[ReleaseEpisodeSelection, ...],
        staging_root: Path,
    ) -> ReleaseAssembly:
        decisions = []
        for selection in selections:
            decision = await self._repository.get_decision(selection.decision_id)
            if decision is None:
                raise ValueError(f"Release decision not found: {selection.decision_id}")
            decisions.append(decision)
        snapshot_by_index = {
            candidate.episode_index: candidate for candidate in request.eligibility_snapshot.candidates
        }
        for decision in decisions:
            candidate = snapshot_by_index.get(decision.source.episode_index)
            if (
                candidate is None
                or candidate.disposition != "accepted"
                or candidate.decision_id != decision.decision_id
                or candidate.source != decision.source
            ):
                raise ValueError("Accepted decision does not match the persisted eligibility snapshot")
        rejected = []
        for candidate in request.eligibility_snapshot.candidates:
            if candidate.disposition != "rejected":
                continue
            if candidate.decision_id is None:
                raise ValueError("Rejected candidate is missing its decision ID")
            decision = await self._repository.get_decision(candidate.decision_id)
            if decision is None or decision.source != candidate.source:
                raise ValueError("Rejected decision does not match the persisted eligibility snapshot")
            rejected.append(decision)
        excluded = tuple(
            candidate for candidate in request.eligibility_snapshot.candidates if candidate.disposition == "excluded"
        )
        quality_reports = []
        for decision in decisions:
            quality_report = await self._repository.get_quality_report(decision.quality_run_id)
            if quality_report is None:
                raise ValueError(f"Release quality report not found: {decision.quality_run_id}")
            quality_reports.append(quality_report)
        async with self._source_workspace.open(request.dataset_id) as source_root:
            return await asyncio.to_thread(
                self._assemble,
                request,
                tuple(decisions),
                tuple(quality_reports),
                tuple(rejected),
                excluded,
                source_root,
                staging_root,
            )

    def _assemble(
        self,
        request: ReleaseJobRequest,
        decisions: tuple[ReviewDecision, ...],
        quality_reports: tuple[QualityReport, ...],
        rejected: tuple[ReviewDecision, ...],
        excluded: tuple[EligibilityCandidate, ...],
        source_root: Path,
        staging_root: Path,
    ) -> ReleaseAssembly:
        source_formats = {decision.source.source_format for decision in decisions}
        if len(source_formats) != 1:
            raise ValueError("One release cannot mix source formats")
        source_format = next(iter(source_formats))
        if source_format == "lerobot":
            loader = LeRobotLoader(source_root)
            info = loader.get_dataset_info()
            features = {
                name: dict(specification)
                for name, specification in info.features.items()
                if name not in {"episode_index", "frame_index", "index", "task_index", "timestamp"}
            }
            fps = max(1, round(info.fps))
            if info.codebase_version.startswith("v3") and self._worker.supports_streaming_v3 is True:
                result = self._worker.copy_v3(
                    source_root=source_root,
                    episodes=tuple(
                        ReleaseEpisodeReference(
                            source=decision.source,
                            decision_id=decision.decision_id,
                            task=_task_name(source_root, decision.source.episode_index),
                        )
                        for decision in decisions
                    ),
                    target_root=staging_root,
                    repo_id=f"local/{request.release_id}",
                    fps=fps,
                    features=features,
                )
            else:
                episodes = tuple(
                    self._load_lerobot_episode(loader, source_root, decision, features) for decision in decisions
                )
                result = self._worker.write_v3(
                    episodes=episodes,
                    target_root=staging_root,
                    repo_id=f"local/{request.release_id}",
                    fps=fps,
                    features=features,
                )
        elif source_format == "hdf5":
            loader = HDF5Loader(source_root)
            loaded = tuple(
                loader.load_episode(decision.source.episode_index, load_images=True) for decision in decisions
            )
            features = _hdf5_features(loaded[0])
            episodes = tuple(
                _hdf5_release_episode(decision, episode, features)
                for decision, episode in zip(decisions, loaded, strict=True)
            )
            fps = max(1, round(float(loaded[0].metadata.get("fps", 30))))
            result = self._worker.write_v3(
                episodes=episodes,
                target_root=staging_root,
                repo_id=f"local/{request.release_id}",
                fps=fps,
                features=features,
            )
        else:
            raise ValueError(f"Unsupported release source format: {source_format}")
        source_formats = tuple(
            sorted(
                {
                    ReleaseFormat(name=decision.source.source_format, version=decision.source.format_version)
                    for decision in decisions
                },
                key=lambda value: (value.name, value.version),
            )
        )
        manifest = ReleaseManifest(
            release_id=request.release_id,
            created_at=datetime.now(UTC),
            actor_id=request.actor_id,
            source_provenance=tuple(decision.source for decision in decisions),
            accepted_decision_ids=result.accepted_decision_ids,
            rejected_decision_ids=tuple(sorted(decision.decision_id for decision in rejected)),
            excluded_episode_indices=tuple(candidate.episode_index for candidate in excluded),
            episode_index_mapping=result.episode_index_mapping,
            source_formats=source_formats,
            target_format=request.target_format,
            adapter_versions={"lerobot": "0.6.1"},
            tool_versions={"dataviewer": "0.1.0"},
            feature_schema=features,
            candidate_count=len(request.eligibility_snapshot.candidates),
            accepted_count=len(decisions),
            rejected_count=len(rejected),
            excluded_count=len(excluded),
            nonincluded_count=len(rejected) + len(excluded),
            episode_count=result.readback.episode_count,
            frame_count=result.readback.frame_count,
            quality_evidence=tuple(
                QualityEvidenceReference(
                    source_episode_index=decision.source.episode_index,
                    release_episode_index=result.episode_index_mapping[decision.source.episode_index],
                    decision_id=decision.decision_id,
                    quality_run_id=decision.quality_run_id,
                    quality_report_path=f"metadata/quality/{decision.quality_run_id}.json",
                    check_set_version=next(
                        report.check_set_version
                        for report in quality_reports
                        if report.run_id == decision.quality_run_id
                    ),
                    required_outcome=QualityOutcome.PASS,
                )
                for decision in sorted(decisions, key=lambda value: value.source.episode_index)
            ),
            files=(),
        )
        package_quality = PackageQualityReport(
            target_format=request.target_format,
            episode_count=result.readback.episode_count,
            frame_count=result.readback.frame_count,
            episode_frame_counts=result.readback.episode_frame_counts,
            features=tuple(sorted(features)),
            nonvisual_rows_read_back=result.readback.frame_count,
            visual_samples=result.readback.visual_samples,
            inventory_verified=True,
            checksums_verified=True,
        )
        return ReleaseAssembly(
            manifest=manifest,
            accepted=decisions,
            rejected=rejected,
            quality_reports=quality_reports,
            package_quality=package_quality,
            excluded=excluded,
        )

    @staticmethod
    def _load_lerobot_episode(
        loader: LeRobotLoader,
        source_root: Path,
        decision: ReviewDecision,
        features: dict[str, dict[str, Any]],
    ) -> ReleaseEpisode:
        episode = loader.load_episode(decision.source.episode_index)
        values: dict[str, Any] = {
            "observation.state": episode.joint_positions,
            "action": episode.actions,
            **episode.additional_features,
        }
        if episode.joint_velocities is not None:
            values["observation.velocity"] = episode.joint_velocities
        for feature_name, video_path in episode.video_paths.items():
            values[feature_name] = _decode_video(video_path, episode.length)
        missing = set(features).difference(values)
        if missing:
            raise ValueError(f"Source episode is missing release features: {sorted(missing)}")
        frames = tuple(
            {
                feature_name: _feature_value(values[feature_name][frame_index], specification)
                for feature_name, specification in features.items()
            }
            for frame_index in range(episode.length)
        )
        return ReleaseEpisode(
            source=decision.source,
            decision_id=decision.decision_id,
            task=_task_name(source_root, episode.task_index),
            frames=frames,
        )


def _decode_video(path: Path, expected_frames: int) -> tuple[np.ndarray[Any, Any], ...]:
    import av

    with av.open(str(path)) as container:
        frames = tuple(frame.to_ndarray(format="rgb24") for frame in container.decode(video=0))
    if len(frames) != expected_frames:
        raise ValueError(f"Video frame count mismatch for {path.name}")
    return frames


def _hdf5_features(episode: Any) -> dict[str, dict[str, Any]]:
    features: dict[str, dict[str, Any]] = {
        "observation.state": _array_feature(episode.joint_positions),
    }
    optional = {
        "action": episode.actions,
        "observation.velocity": episode.joint_velocities,
        "observation.end_effector_pose": episode.end_effector_pose,
        "observation.gripper": episode.gripper_states,
    }
    for name, values in optional.items():
        if values is not None:
            features[name] = _array_feature(values)
    for camera, images in episode.images.items():
        features[f"observation.images.{camera}"] = {
            "dtype": "video",
            "shape": list(images.shape[1:]),
        }
    return features


def _array_feature(values: np.ndarray[Any, Any]) -> dict[str, Any]:
    shape = list(values.shape[1:]) or [1]
    return {"dtype": str(values.dtype), "shape": shape}


def _hdf5_release_episode(
    decision: ReviewDecision,
    episode: Any,
    features: dict[str, dict[str, Any]],
) -> ReleaseEpisode:
    values: dict[str, Any] = {"observation.state": episode.joint_positions}
    optional = {
        "action": episode.actions,
        "observation.velocity": episode.joint_velocities,
        "observation.end_effector_pose": episode.end_effector_pose,
        "observation.gripper": episode.gripper_states,
    }
    values.update({name: value for name, value in optional.items() if value is not None})
    values.update({f"observation.images.{camera}": images for camera, images in episode.images.items()})
    frames = tuple(
        {name: _feature_value(values[name][frame_index], specification) for name, specification in features.items()}
        for frame_index in range(episode.length)
    )
    return ReleaseEpisode(
        source=decision.source,
        decision_id=decision.decision_id,
        task=str(episode.metadata.get("task", f"task-{episode.task_index}")),
        frames=frames,
    )


def _feature_value(value: Any, specification: dict[str, Any]) -> np.ndarray[Any, Any]:
    dtype = specification.get("dtype")
    if dtype in {"image", "video"}:
        return np.asarray(value, dtype=np.uint8)
    array = np.asarray(value, dtype=np.dtype(str(dtype)))
    return array.reshape(1) if array.ndim == 0 else array


def _task_name(source_root: Path, task_index: int) -> str:
    path = source_root / "meta" / "tasks.parquet"
    if path.is_file():
        table = pq.read_table(path)
        if "task_index" in table.column_names and "task" in table.column_names:
            for index, task in zip(
                table.column("task_index").to_pylist(),
                table.column("task").to_pylist(),
                strict=True,
            ):
                if int(index) == task_index:
                    return str(task)
    info_path = source_root / "meta" / "info.json"
    if info_path.is_file():
        info = json.loads(info_path.read_text(encoding="utf-8"))
        if task := info.get("task"):
            return str(task)
    return f"task-{task_index}"


class ReleaseProcessor:
    """Advance one queued job through package publication."""

    def __init__(
        self,
        workflow: ReleaseWorkflowService,
        jobs: ReleaseJobStore,
        *,
        staging_root: Path,
        assembler: ReleaseAssembler,
        local_publisher: LocalReleasePublisher,
        azure_publisher_factory: AzurePublisherFactory | None = None,
    ) -> None:
        self._workflow = workflow
        self._jobs = jobs
        self._staging_root = staging_root
        self._assembler = assembler
        self._local_publisher = local_publisher
        self._azure_publisher_factory = azure_publisher_factory
        self._active: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        """Reconcile interrupted work and resume queued jobs."""
        published = set(self._local_publisher.list_published_releases())
        if self._azure_publisher_factory is not None:
            azure_publisher = await self._azure_publisher_factory()
            published.update(await azure_publisher.list_published_releases())
        self._jobs.reconcile(
            staging_root=self._staging_root,
            is_published=lambda dataset_id, release_id: (dataset_id, release_id) in published,
        )
        for job in self._jobs.list_jobs():
            if job.state is JobState.QUEUED:
                self.notify(job.job_id)

    async def stop(self) -> None:
        """Wait for active release tasks before application shutdown."""
        if self._active:
            await asyncio.gather(*tuple(self._active.values()), return_exceptions=True)

    def notify(self, job_id: str) -> None:
        """Schedule a queued job without extending the HTTP request lifetime."""
        if job_id in self._active:
            return
        task = asyncio.create_task(self.process(job_id), name=f"release-{job_id}")
        self._active[job_id] = task
        task.add_done_callback(lambda _task: self._active.pop(job_id, None))

    async def process(self, job_id: str) -> None:
        """Process one queued job and persist its terminal result."""
        job = self._jobs.get(job_id)
        if job.state is not JobState.QUEUED:
            return
        request = ReleaseJobRequest.model_validate(job.request)
        staging_root = self._staging_root / request.dataset_id / job.job_id
        try:
            self._staging_root.mkdir(parents=True, exist_ok=True)
            self._workflow.transition(job_id, JobState.RUNNING)
            if self._jobs.cancellation_checkpoint(job_id):
                return
            assembly = await self._assembler(request, request.episodes, staging_root)
            await asyncio.to_thread(
                finalize_release,
                staging_root,
                assembly.manifest,
                assembly.accepted,
                assembly.rejected,
                assembly.quality_reports,
                assembly.package_quality,
                assembly.excluded,
            )
            self._workflow.transition(job_id, JobState.VERIFYING)
            await asyncio.to_thread(verify_release, staging_root)
            if self._jobs.cancellation_checkpoint(job_id):
                return
            self._workflow.transition(job_id, JobState.PUBLISHING)
            if request.destination_kind == "local":
                destination = await asyncio.to_thread(
                    self._local_publisher.publish,
                    request.dataset_id,
                    request.release_id,
                    staging_root,
                    owner=job.job_id,
                )
                manifest_path = (destination / "metadata" / "release-manifest.json").as_posix()
                checksums_path = (destination / "checksums.sha256").as_posix()
            elif self._azure_publisher_factory is not None:
                azure_publisher = await self._azure_publisher_factory()
                await azure_publisher.publish(
                    request.dataset_id,
                    request.release_id,
                    staging_root,
                    owner=job.job_id,
                )
                release_path = f"releases/{request.dataset_id}/{request.release_id}"
                manifest_path = f"{release_path}/metadata/release-manifest.json"
                checksums_path = f"{release_path}/checksums.sha256"
                await asyncio.to_thread(_remove_staging, staging_root)
            else:
                raise ValueError("Azure release destination is not configured")
            self._workflow.complete_success(
                job_id,
                manifest_path=manifest_path,
                checksums_path=checksums_path,
            )
        except PublicationConflictError as exc:
            self._workflow.complete_failure(job_id, str(exc), conflict=True)
        except Exception as exc:
            self._workflow.complete_failure(job_id, str(exc))


def _remove_staging(staging_root: Path) -> None:
    import shutil

    shutil.rmtree(staging_root, ignore_errors=True)


_release_processor: ReleaseProcessor | None = None


def get_release_processor() -> ReleaseProcessor:
    """Return the configured in-process release processor singleton."""
    global _release_processor
    if _release_processor is None:
        from ..config import create_blob_dataset_provider, create_source_workspace, get_app_config
        from ..services.release_workflow_service import get_release_workflow_service

        config = get_app_config()
        workflow = get_release_workflow_service()
        root = Path(config.dataviewer_release_root)
        azure_factory: AzurePublisherFactory | None = None
        if config.storage_backend == "azure":
            provider = create_blob_dataset_provider(config)
            if provider is None:
                raise ValueError("Azure release publication requires a configured dataset container")

            async def create_azure_publisher() -> BlobReleasePublisher:
                client = await provider._get_client()
                container = client.get_container_client(provider.container_name)
                return BlobReleasePublisher(container, export_prefix=config.azure_dataset_export_prefix)

            azure_factory = create_azure_publisher
        _release_processor = ReleaseProcessor(
            workflow,
            workflow.job_store,
            staging_root=root / ".staging",
            assembler=ReleasePackageAssembler(
                workflow.repository,
                create_source_workspace(config),
            ),
            local_publisher=LocalReleasePublisher(root / "releases"),
            azure_publisher_factory=azure_factory,
        )
        workflow.set_job_notifier(_release_processor.notify)
    return _release_processor
