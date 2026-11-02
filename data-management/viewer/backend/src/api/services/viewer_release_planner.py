"""Configured viewer composition for shared release planning and independent work."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, replace
from importlib.metadata import version
from pathlib import Path

from physical_ai_curation.release.canonical import canonical_json_bytes, parse_strict_json, sha256_hex
from physical_ai_curation.release.inputs import (
    BlobReleaseSource,
    FreezeContext,
    LocalReleaseSource,
    ReleaseSelection,
    freeze_release_inputs,
)
from physical_ai_curation.release.jobs import ReleaseJobStore, ReleaseRuntimeSettings
from physical_ai_curation.release.media import StorageEndpoint, load_media_limits
from physical_ai_curation.release.models import ProducerIdentity, VersionIdentity
from physical_ai_curation.release.publication import PublicationAttempt
from physical_ai_curation.release.record_storage import BlobLocation
from physical_ai_curation.release.runner import ConfiguredExecution
from physical_ai_curation.release.source_associations import (
    SourceAssociationIntent,
    source_association_binding,
    source_association_key,
)
from pydantic import TypeAdapter

from .annotation_service import get_annotation_service
from .dataset_service import get_dataset_service
from .release_service import PreparedRelease, ReleaseApiError, ReleaseApiSettings, ReleaseSpec
from .saved_input_service import ViewerSavedInputReader


class ViewerReleasePlanner:
    """Resolve only configured datasets and storage aliases, never caller paths."""

    def __init__(self, settings: ReleaseApiSettings) -> None:
        self.settings = settings
        self.datasets = get_dataset_service()
        self.reader = ViewerSavedInputReader(self.datasets, get_annotation_service())

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
            credential=DefaultAzureCredential(),
            connection_timeout=runtime.state_timeout_seconds,
            read_timeout=runtime.state_timeout_seconds,
            retry_total=0,
        )
        return BlobReleaseSource(
            client.get_container_client(provider.container_name), prefix=provider.get_blob_prefix(dataset_id)
        )

    async def prepare(self, scope: str, spec: ReleaseSpec, destination: Path | BlobLocation) -> PreparedRelease:
        from physical_ai_curation.release.composition import prepare_saved_generation
        from physical_ai_curation.release.coordinator import generation_request_bytes, plan_generation_inputs
        from physical_ai_curation.release.native_worker import MAX_RESULT_BYTES, MAX_SPEC_BYTES

        if await self.datasets.get_dataset(spec.dataset_id) is None:
            raise ReleaseApiError(404, "release source is unavailable")
        source = self._source(spec.dataset_id, finalized=spec.finalized)
        selection = ReleaseSelection(spec.dataset_id, spec.episode_indices, spec.resources)
        context = FreezeContext(
            scope,
            await self.datasets.get_source_revision(spec.dataset_id, spec.episode_indices[0]),
            ((scope, "localuser" if scope == "localuser" else "curator"),),
        )
        frozen = await freeze_release_inputs(source, self.reader, selection, context)
        runtime = ReleaseRuntimeSettings.from_environment(os.environ)
        limits = load_media_limits(os.environ)
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
        inputs = await asyncio.to_thread(
            plan_generation_inputs,
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
        )
        prepared = prepare_saved_generation(
            frozen.content, inputs.request, inputs.sources, camera_statistics=inputs.camera_statistics
        )
        capacity = prepared.plan.capacity
        table_bytes = sum(item.length for item in frozen.content.source_objects if item.path.startswith("data/"))
        native_scratch = MAX_SPEC_BYTES + MAX_RESULT_BYTES + max(16 * 1024**2, table_bytes * 3)
        scratch_bytes = max(capacity.scratch_peak_bytes, native_scratch)
        inputs = replace(
            inputs,
            scratch_bytes=scratch_bytes,
            output_bytes=max(16 * 1024**2, capacity.payload_bytes * 2),
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
            attempt=PublicationAttempt(spec.package_id, spec.attempt_id, spec.dataset_name),
            frozen_input_sha256=frozen.sha256,
            display_name=spec.dataset_name,
            description="",
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
                "route": prepared.plan.route,
                "capacity": asdict(capacity),
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
        store = ReleaseJobStore(self.settings.job_root, settings=ReleaseRuntimeSettings.from_environment(os.environ))
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
        source = (
            None
            if store.storage.snapshot()[job_id]["state"] == "committed"
            else self._source(selection.dataset_id, finalized=raw["finalized"], kind=raw["source_kind"])
        )
        return ConfiguredExecution(source, self.reader, selection, context, destination)
