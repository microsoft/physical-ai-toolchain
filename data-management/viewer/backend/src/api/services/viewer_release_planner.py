"""Configured viewer composition for shared release planning and independent work."""

from __future__ import annotations

import asyncio
import functools
import os
import shutil
from collections.abc import Callable
from dataclasses import asdict, replace
from importlib.metadata import version
from pathlib import Path

from physical_ai_curation.adapters.job_storage import LocalJobStore
from physical_ai_curation.release.canonical import canonical_json_bytes, parse_strict_json, sha256_hex
from physical_ai_curation.release.inputs import (
    BlobReleaseSource,
    FreezeContext,
    FrozenReleaseInputs,
    LocalReleaseSource,
    ReleaseSelection,
    _join_owned,
    capture_saved_selection,
    freeze_release_inputs,
)
from physical_ai_curation.release.jobs import ReleaseRuntimeSettings
from physical_ai_curation.release.media import StorageEndpoint, load_media_limits
from physical_ai_curation.release.models import ProducerIdentity, VersionIdentity
from physical_ai_curation.release.projections import materialize_saved_projections
from physical_ai_curation.release.publication import PublicationAttempt
from physical_ai_curation.release.record_storage import BlobLocation
from physical_ai_curation.release.runner import ConfiguredExecution
from physical_ai_curation.release.source_associations import (
    SourceAssociationIntent,
    source_association_binding,
    source_association_key,
)
from pydantic import TypeAdapter

from ..config import get_app_config
from .annotation_service import get_annotation_service
from .dataset_service import get_dataset_service
from .label_storage import BlobLabelStorage, LabelStorage, LocalLabelStorage
from .release_service import (
    PreparedRelease,
    ReleaseApiError,
    ReleaseApiSettings,
    ReleaseSpec,
    check_cancelled,
    open_release_job_store,
)
from .saved_input_service import (
    AzureJudgeStateReader,
    JudgeStateReader,
    ScopedJudgeEvidenceAuthority,
    ViewerSavedInputReader,
)


class ViewerReleasePlanner:
    """Resolve only configured datasets and storage aliases, never caller paths."""

    def __init__(self, settings: ReleaseApiSettings) -> None:
        self.settings = settings
        self.datasets = get_dataset_service()
        self._annotations = get_annotation_service()
        self.reader = ViewerSavedInputReader(self.datasets, self._annotations, label_storage=self._label_storage())

    def reader_for_scope(self, scope: str, *, inventory: bool = False) -> ViewerSavedInputReader:
        """Bind evidence to the principal; inventory readers share labels only within one page."""
        authority = ScopedJudgeEvidenceAuthority(
            self._judge_state(), self.datasets.get_source_revision, principal_scope_id=scope
        )
        return ViewerSavedInputReader(
            self.datasets,
            self._annotations,
            label_storage=self._label_storage(),
            judge_evidence=authority,
            cache_release_labels=inventory,
            cache_source_revisions=inventory,
        )

    def _judge_state(self) -> JudgeStateReader:
        config = get_app_config()
        if config.storage_backend == "azure":
            return AzureJudgeStateReader(self.datasets._blob_provider)
        return LocalJobStore(
            Path(config.vlm_judge_job_dir)
            if config.vlm_judge_job_dir
            else Path(self.datasets.base_path) / ".curation" / "judge"
        )

    def _label_storage(self) -> LabelStorage:
        provider = self.datasets._blob_provider
        if self.datasets.has_blob_provider() and provider is not None:
            return BlobLabelStorage(provider)
        return LocalLabelStorage(self.datasets.base_path)

    def _source(self, dataset_id: str, *, finalized: bool, kind: str | None = None):
        if kind == "local" or (kind is None and not self.datasets.has_blob_provider()):
            root = self.datasets._get_dataset_path(dataset_id)
            return LocalReleaseSource(root, finalized=finalized)
        provider = self.datasets._blob_provider
        if provider is None or kind == "local":
            raise ReleaseApiError(404, "release source is unavailable")
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        runtime = ReleaseRuntimeSettings.from_environment(os.environ)
        client = BlobServiceClient(
            account_url=f"https://{provider.account_name}.blob.core.windows.net",
            credential=DefaultAzureCredential(
                process_timeout=runtime.state_timeout_seconds,
                connection_timeout=runtime.state_timeout_seconds,
                read_timeout=runtime.state_timeout_seconds,
                retry_total=0,
            ),
            connection_timeout=runtime.state_timeout_seconds,
            read_timeout=runtime.state_timeout_seconds,
            retry_total=0,
        )
        return BlobReleaseSource(
            client.get_container_client(provider.container_name), prefix=provider.get_blob_prefix(dataset_id)
        )

    async def prepare(self, scope: str, spec: ReleaseSpec, destination: Path | BlobLocation) -> PreparedRelease:
        """Describe selected intent without inspecting or freezing the source."""
        if self.settings.job_root is None:
            raise ReleaseApiError(503, "release worker is not configured")
        source_kind = "blob" if self.datasets.has_blob_provider() else "local"
        if source_kind == "blob" and isinstance(destination, Path):
            raise ReleaseApiError(422, "azure_to_local_unsupported")
        location = (
            {"kind": "local", "path": str(destination.resolve())}
            if isinstance(destination, Path)
            else {"kind": "blob", **asdict(destination)}
        )
        payload = {
            "schema_version": "curated-release-intent/v1",
            "scope": scope,
            "spec": asdict(spec),
            "effective_destination": location,
            "source_kind": source_kind,
            "processing_confirmed": False,
        }
        content = canonical_json_bytes(payload)
        return PreparedRelease(
            content,
            0,
            {
                "advisory": True,
                "processing_digest": sha256_hex(content),
                "selected_count": len(spec.episode_indices),
                "destination_kind": location["kind"],
                "available_bytes": None,
                "route": "blob-same-account" if source_kind == "blob" else "local-to-local",
                "capacity": None,
                "blocking": [],
                "warnings": [
                    "Statistics and analysis completeness have not been checked; omissions do not prevent release.",
                    "Source identity, integrity evidence and capacity are established during release execution.",
                    "Video processing requires explicit consent; source changes do not invalidate this preview.",
                ],
                "actions": [],
            },
            canonical_json_bytes(
                {
                    "schema": "curated-release-intent/v1",
                    "selection": asdict(ReleaseSelection(spec.dataset_id, spec.episode_indices, spec.resources)),
                }
            ),
        )

    async def _capture(
        self,
        scope: str,
        spec: ReleaseSpec,
        destination: Path | BlobLocation,
        checkpoint: Callable[[], None],
        source_kind: str,
    ) -> tuple[PreparedRelease, ConfiguredExecution]:
        async def run[Result](operation: Callable[..., Result], *args: object, **kwargs: object) -> Result:
            checkpoint()
            future = asyncio.get_running_loop().run_in_executor(None, functools.partial(operation, *args, **kwargs))
            try:
                result = await asyncio.shield(future)
            except asyncio.CancelledError:
                await _join_owned(future)
                raise
            checkpoint()
            return result

        checkpoint()
        if await self.datasets.get_dataset(spec.dataset_id) is None:
            raise ReleaseApiError(404, "release source is unavailable")
        source = await run(self._source, spec.dataset_id, finalized=spec.finalized, kind=source_kind)
        if isinstance(source, BlobReleaseSource) and isinstance(destination, Path):
            raise ReleaseApiError(422, "azure_to_local_unsupported")
        if isinstance(source, BlobReleaseSource):
            from physical_ai_curation.release.coordinator import select_blob_dependencies

            runtime = ReleaseRuntimeSettings.from_environment(os.environ)
            source = await run(
                select_blob_dependencies,
                source,
                spec.episode_indices,
                metadata_budget=min(runtime.memory_bytes, 16 * 1024**2),
                checkpoint=checkpoint,
            )
        selection = ReleaseSelection(spec.dataset_id, spec.episode_indices, spec.resources)
        context = FreezeContext(
            scope,
            await self.datasets.get_source_revision(spec.dataset_id, spec.episode_indices[0]),
            ((scope, "localuser" if scope == "localuser" else "curator"),),
        )
        reader = self.reader_for_scope(scope)
        selection = await capture_saved_selection(reader, selection, context, checkpoint)
        frozen = await freeze_release_inputs(source, reader, selection, context, checkpoint=checkpoint)
        prepared = await run(
            self._build_prepared,
            scope,
            spec,
            destination,
            source,
            selection,
            context,
            frozen,
            checkpoint=checkpoint,
        )
        return prepared, ConfiguredExecution(source, reader, selection, context, destination)

    def _build_prepared(
        self,
        scope: str,
        spec: ReleaseSpec,
        destination: Path | BlobLocation,
        source: LocalReleaseSource | BlobReleaseSource,
        selection: ReleaseSelection,
        context: FreezeContext,
        frozen: FrozenReleaseInputs,
        *,
        checkpoint: Callable[[], None] = check_cancelled,
    ) -> PreparedRelease:
        from physical_ai_curation.release.composition import prepare_saved_generation
        from physical_ai_curation.release.coordinator import generation_request_bytes, plan_generation_inputs
        from physical_ai_curation.release.execution import MAX_RESULT_BYTES as ENCODE_RESULT_BYTES
        from physical_ai_curation.release.execution import MAX_SPEC_BYTES as ENCODE_SPEC_BYTES
        from physical_ai_curation.release.native_worker import MAX_RESULT_BYTES, MAX_SPEC_BYTES

        runtime = ReleaseRuntimeSettings.from_environment(os.environ)
        limits = load_media_limits(os.environ)
        checkpoint()
        if isinstance(source, LocalReleaseSource):
            source_endpoint = StorageEndpoint("source", "local", volume=str(source.root.stat().st_dev))
        else:
            source_endpoint = StorageEndpoint("source", "blob", account=source.location.account_url)
        if isinstance(destination, Path):
            ancestor = destination
            while not ancestor.exists() and ancestor != ancestor.parent:
                ancestor = ancestor.parent
            destination_endpoint = StorageEndpoint(spec.destination, "local", volume=str(ancestor.stat().st_dev))
        else:
            destination_endpoint = StorageEndpoint(spec.destination, "blob", account=destination.account_url)
        scratch = self.settings.job_root
        if scratch is None:
            raise ReleaseApiError(503, "release worker is not configured")
        while not scratch.exists() and scratch != scratch.parent:
            scratch = scratch.parent
        scratch_endpoint = StorageEndpoint("scratch", "local", volume=str(scratch.stat().st_dev))
        identity = VersionIdentity(name="physical-ai-curation", version=version("physical-ai-curation"))
        target = VersionIdentity(name="lerobot", version="v3.0")
        producer = ProducerIdentity(
            application=identity,
            adapter=identity,
            source_format=target,
            target_format=target,
            tools=(identity,),
            settings_json="{}",
        )
        inputs = plan_generation_inputs(
            source,
            frozen,
            source_endpoint=source_endpoint,
            destination_endpoint=destination_endpoint,
            scratch_endpoint=scratch_endpoint,
            producer=producer,
            tool=identity,
            scratch_bytes=1,
            output_bytes=1,
            limits=limits,
            metadata_budget=min(runtime.memory_bytes, 16 * 1024**2),
            checkpoint=checkpoint,
        )
        checkpoint()
        prepared = prepare_saved_generation(
            frozen.content, inputs.request, inputs.sources, camera_statistics=inputs.camera_statistics
        )
        capacity = prepared.plan.capacity
        checkpoint()
        attempt = PublicationAttempt(spec.package_id, spec.attempt_id, spec.dataset_name)
        projections = materialize_saved_projections(
            frozen.content, prepared.episodes, release_dataset_id=attempt.prefix
        )
        projection_bytes = sum(len(content) for content in projections.values())
        capacity = replace(
            capacity,
            generated_bytes=capacity.generated_bytes + projection_bytes,
            destination_required_bytes=(
                capacity.destination_required_bytes + projection_bytes
                if capacity.destination_required_bytes is not None
                else None
            ),
            combined_required_bytes=(
                capacity.combined_required_bytes + projection_bytes
                if capacity.combined_required_bytes is not None
                else None
            ),
        )
        table_bytes = sum(item.length for item in frozen.content.source_objects if item.path.startswith("data/"))
        native_scratch = MAX_SPEC_BYTES + MAX_RESULT_BYTES + max(16 * 1024**2, table_bytes * 3)
        output_budget = max(16 * 1024**2, capacity.payload_bytes * 2)
        encoding_scratch = max(
            (
                (item.source_length or 0) + output_budget + ENCODE_SPEC_BYTES + ENCODE_RESULT_BYTES
                for item in prepared.plan.objects
                if item.action == "transcode"
            ),
            default=0,
        )
        native_scratch = max(native_scratch, encoding_scratch)
        scratch_bytes = max(capacity.scratch_peak_bytes, native_scratch)
        inputs = replace(
            inputs,
            scratch_bytes=scratch_bytes,
            output_bytes=output_budget,
        )
        intent = SourceAssociationIntent(
            source_key=source_association_key(context.source[0]),
            source_identity=context.source[0],
            source_revision=context.source[1],
            scope_sha256=sha256_hex(scope.encode()),
            destination_alias=spec.destination,
            source_relative_path=spec.dataset_id.replace("--", "/"),
            effective_destination=(
                str(destination.resolve()) if isinstance(destination, Path) else destination.as_text()
            ),
            attempt=attempt,
            frozen_input_sha256=frozen.sha256,
            display_name=spec.dataset_name,
            description=spec.description,
            accepted_at="1970-01-01T00:00:00+00:00",
            source_display_label=spec.dataset_id,
        )
        source_association_binding(intent)
        association = intent.model_dump(mode="json", exclude={"accepted_at"})
        binding = canonical_json_bytes(
            {
                "schema": "curated-release-execution/v3",
                "selection": asdict(selection),
                "context": asdict(context),
                "destination": spec.destination,
                "effective_destination": (
                    {"kind": "local", "path": str(destination.resolve())}
                    if isinstance(destination, Path)
                    else {"kind": "blob", **asdict(destination)}
                ),
                "source_kind": source_endpoint.kind,
                "finalized": spec.finalized,
                "association": association,
            }
        )
        return PreparedRelease(
            generation_request_bytes(frozen, inputs),
            max(1, max(capacity.payload_bytes, inputs.output_bytes) + scratch_bytes),
            {
                "processing_digest": inputs.confirmed_digest,
                "selected_count": len(selection.episode_indices),
                "destination_kind": "local" if isinstance(destination, Path) else "blob",
                "available_bytes": shutil.disk_usage(ancestor).free if isinstance(destination, Path) else None,
                "route": prepared.plan.route,
                "capacity": asdict(capacity),
                "projection_bytes": projection_bytes,
                "projection_objects": len(projections),
                "native_scratch_bytes": native_scratch,
                "destination_budget_bytes": inputs.output_bytes,
                "blocking": list(prepared.plan.blocking),
                "warnings": list(prepared.plan.warnings),
                "actions": [asdict(item) for item in prepared.plan.objects],
            },
            binding,
        )

    def execution(self, job_id: str, frozen_request: bytes) -> ConfiguredExecution:
        if self.settings.job_root is None:
            raise ReleaseApiError(503, "release worker is not configured")
        payload = parse_strict_json(frozen_request)
        if payload.get("schema_version") == "curated-release-intent/v1":
            spec = TypeAdapter(ReleaseSpec).validate_python(payload["spec"])
            if spec.destination not in self.settings.destinations:
                raise ReleaseApiError(409, "configured destination is unavailable")
            location = payload["effective_destination"]
            if location["kind"] == "local" and set(location) == {"kind", "path"}:
                destination = Path(location["path"])
                if not destination.is_absolute() or destination.resolve() != destination:
                    raise ReleaseApiError(409, "accepted destination is unavailable")
            elif location["kind"] == "blob" and set(location) == {"kind", "account_url", "container", "prefix"}:
                destination = BlobLocation(location["account_url"], location["container"], location["prefix"])
            else:
                raise ReleaseApiError(409, "release destination binding is unsupported")
            scope = payload["scope"]
            selection = ReleaseSelection(spec.dataset_id, spec.episode_indices, spec.resources)
            store = open_release_job_store(self.settings)
            record = store.storage.snapshot()[job_id]
            expected_destination = str(destination) if isinstance(destination, Path) else destination.as_text()
            if (
                store.execution_binding(job_id)
                != canonical_json_bytes(
                    {
                        "schema": "curated-release-intent/v1",
                        "selection": asdict(selection),
                    }
                )
                or record["destination"] != expected_destination
                or record["principal_digest"] != sha256_hex(scope.encode())
                or payload["source_kind"] not in {"local", "blob"}
                or not isinstance(payload["processing_confirmed"], bool)
            ):
                raise ReleaseApiError(409, "release intent binding is unsupported")

            async def capture(checkpoint: Callable[[], None]) -> tuple[bytes, bytes, int, ConfiguredExecution]:
                prepared, execution = await self._capture(scope, spec, destination, checkpoint, payload["source_kind"])
                if not payload["processing_confirmed"] and any(
                    action["confirmation_required"] for action in prepared.summary["actions"]
                ):
                    raise ReleaseApiError(409, "processing_confirmation_required")
                return prepared.frozen_request, prepared.execution_binding, prepared.reserved_bytes, execution

            return ConfiguredExecution(
                None,
                self.reader_for_scope(scope),
                selection,
                None,
                destination,
                capture,
            )
        store = open_release_job_store(self.settings)
        raw = parse_strict_json(store.execution_binding(job_id))
        if raw["schema"] != "curated-release-execution/v3":
            raise ReleaseApiError(409, "release worker binding is unsupported")
        selection = TypeAdapter(ReleaseSelection).validate_python(raw["selection"])
        context = TypeAdapter(FreezeContext).validate_python(raw["context"])
        if raw["destination"] not in self.settings.destinations:
            raise ReleaseApiError(409, "configured destination is unavailable")
        location = raw["effective_destination"]
        if location["kind"] == "local" and set(location) == {"kind", "path"}:
            destination = Path(location["path"])
            if not destination.is_absolute() or destination.resolve() != destination:
                raise ReleaseApiError(409, "accepted destination is unavailable")
        elif location["kind"] == "blob" and set(location) == {"kind", "account_url", "container", "prefix"}:
            destination = BlobLocation(location["account_url"], location["container"], location["prefix"])
        else:
            raise ReleaseApiError(409, "release destination binding is unsupported")
        association = store.source_association(job_id)
        expected_destination = str(destination) if isinstance(destination, Path) else destination.as_text()
        if (
            association is None
            or association.effective_destination != expected_destination
            or association.source_identity != context.source[0]
            or association.source_revision != context.source[1]
            or association.scope_sha256 != sha256_hex(context.principal_scope_id.encode())
        ):
            raise ReleaseApiError(409, "release association binding is unsupported")
        record = store.storage.snapshot()[job_id]
        source = (
            None
            if record["state"] == "committed" or record["cancel_requested"]
            else self._source(selection.dataset_id, finalized=raw["finalized"], kind=raw["source_kind"])
        )
        return ConfiguredExecution(
            source, self.reader_for_scope(context.principal_scope_id), selection, context, destination
        )
