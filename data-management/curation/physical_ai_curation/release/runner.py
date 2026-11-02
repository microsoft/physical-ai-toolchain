"""Independent release execution from durable requests and trusted configured bindings."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import TypeAdapter

from physical_ai_curation.release.canonical import parse_strict_json
from physical_ai_curation.release.inputs import (
    BlobReleaseSource,
    FreezeContext,
    FrozenReleaseInputs,
    LocalReleaseSource,
    ReleaseInputError,
    ReleaseSelection,
    revalidate_release_inputs,
)
from physical_ai_curation.release.jobs import AuthorityError, ReleaseJobStore, ReleaseRuntimeSettings
from physical_ai_curation.release.publication import PublicationAttempt
from physical_ai_curation.release.record_storage import BlobLocation
from physical_ai_curation.saved_input import SavedInputReader

if TYPE_CHECKING:
    from physical_ai_curation.release.models import ReleaseResult


@dataclass(frozen=True)
class ConfiguredExecution:
    """Trusted service/operator composition, never an HTTP request with client paths or actor overrides."""

    source: LocalReleaseSource | BlobReleaseSource
    reader: SavedInputReader
    selection: ReleaseSelection
    context: FreezeContext
    destination: Path | BlobLocation


async def execute_job(
    store: ReleaseJobStore,
    job_id: str,
    configured: ConfiguredExecution,
    *,
    worker_id: str = "release-worker",
) -> ReleaseResult | None:
    """Claim and restore one persisted job; uncertain effects require explicit reconciliation.

    The caller must authorize the job before resolving its trusted configuration. This
    internal worker interface is not a resource access check or a remote submission API.
    """
    from physical_ai_curation.release.blob_worker import SupervisedBlobPublisher
    from physical_ai_curation.release.composition import prepare_saved_generation
    from physical_ai_curation.release.coordinator import (
        GenerationInputs,
        _publication_runtime,
        generation_request_bytes,
        run_release_generation,
    )
    from physical_ai_curation.release.encoding import EncodeOptions
    from physical_ai_curation.release.media import (
        ObjectPlanRequest,
        SubsetEpisodeInput,
        validate_processing_confirmation,
    )
    from physical_ai_curation.release.models import MANIFEST_PATH, ProducerIdentity
    from physical_ai_curation.release.native_worker import restore_inputs
    from physical_ai_curation.release.verification import verify_published

    record = store.storage.snapshot()[job_id]
    package_id, attempt_id = record["attempt_key"].split("/", 1)
    attempt = PublicationAttempt(package_id, attempt_id, record["attempt_prefix"])
    destination = configured.destination
    actual = destination.as_text() if isinstance(destination, BlobLocation) else str(destination.resolve())
    if record["destination"] != actual:
        raise AuthorityError("configured destination differs from accepted execution")
    if record["state"] == "cancelled":
        return None
    if record["state"] == "committed":
        with _publication_runtime(destination, attempt, store, job_id, record["fence"], None) as publisher:
            return verify_published(publisher.store, record["commit_digest"])

    fence = store.claim(job_id, worker_id)
    started = time.monotonic()
    operation = uuid4().hex
    try:
        store.recover_runtimes(job_id, fence)
        store.confirm_stopped(job_id, fence)
        if record["scratch_bytes"]:
            store.cleanup_scratch_workspace(job_id, fence, recover=True)
            store.release_scratch(job_id, fence)
        if record["cancel_requested"]:
            if isinstance(destination, BlobLocation):
                with _publication_runtime(destination, attempt, store, job_id, fence, None) as publisher:
                    assert isinstance(publisher, SupervisedBlobPublisher)
                    publisher.cleanup_cancelled()
            else:
                store.reconcile_local_outputs(job_id, fence)
                removed = store.cleanup_owned_outputs(job_id, fence, destination / attempt.prefix)
                store.settle_cancellation(job_id, fence, output_removed=removed)
                if not removed:
                    raise ReleaseInputError("owned output cleanup remains unresolved")
            return None
        if record["state"] in ("sealed", "committing"):
            digest = record["commit_digest"] or record["effects"][MANIFEST_PATH]["digest"]
            with _publication_runtime(destination, attempt, store, job_id, fence, None) as publisher:
                return publisher.publish(digest)
        raw = store.frozen_request(job_id)
        payload = parse_strict_json(raw)
        if payload.get("schema_version") != "curated-release-generation/v1":
            raise ReleaseInputError("unsupported durable generation request")
        frozen = TypeAdapter(FrozenReleaseInputs).validate_python(payload["frozen"])
        await revalidate_release_inputs(
            frozen,
            configured.source,
            configured.reader,
            configured.selection,
            configured.context,
        )
        import pyarrow as pa

        source_root = (
            configured.source.root if isinstance(configured.source, LocalReleaseSource) else configured.source.location
        )
        inputs = GenerationInputs(
            request=TypeAdapter(ObjectPlanRequest).validate_python(payload["request"]),
            sources=TypeAdapter(dict[str, SubsetEpisodeInput]).validate_python(payload["sources"]),
            source_root=source_root,
            source_info={},
            source_tables={},
            source_tasks=pa.table({}),
            camera_statistics=payload["camera_statistics"],
            preserved_dataset_statistics=payload["preserved_dataset_statistics"],
            encode_options=TypeAdapter(EncodeOptions | None).validate_python(payload["encode_options"]),
            producer=ProducerIdentity.model_validate(payload["producer"]),
            confirmed_digest=payload["confirmed_digest"],
            scratch_bytes=payload["scratch_bytes"],
            output_bytes=payload["output_bytes"],
        )
        if generation_request_bytes(frozen, inputs) != raw:
            raise ReleaseInputError("durable generation request is not canonical")
        prepared = prepare_saved_generation(
            frozen.content,
            inputs.request,
            inputs.sources,
            encode_options=inputs.encode_options,
            camera_statistics=inputs.camera_statistics,
        )
        validate_processing_confirmation(prepared.plan, confirmed_digest=inputs.confirmed_digest)
        store.validate_execution_binding(job_id, fence, destination, attempt, route=record["route"])
        inputs = restore_inputs(store, job_id, fence, frozen, prepared, inputs)
        result = await run_release_generation(
            store=store,
            job_id=job_id,
            fence=fence,
            destination=destination,
            attempt=attempt,
            expected=frozen,
            source=configured.source,
            reader=configured.reader,
            selection=configured.selection,
            context=configured.context,
            inputs=inputs,
        )
        store.record_diagnostic(
            job_id,
            fence,
            operation,
            "validation",
            "succeeded",
            duration_ms=min(86_400_000, int((time.monotonic() - started) * 1000)),
        )
        return result
    except Exception:
        with suppress(AuthorityError):
            store.record_diagnostic(
                job_id,
                fence,
                operation,
                "validation",
                "failed",
                duration_ms=min(86_400_000, int((time.monotonic() - started) * 1000)),
            )
        raise


async def run_queue(
    store: ReleaseJobStore,
    resolve: Callable[[str, bytes], ConfiguredExecution],
    *,
    worker_id: str = "release-worker",
    limit: int = 1,
) -> tuple[dict[str, str], ...]:
    """Perform one bounded queue pass with a trusted resolver; never busy-poll or redispatch uncertainty."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("worker queue limit must be between 1 and 100")
    outcomes = []
    for job_id, row in sorted(store.storage.snapshot().items()):
        if row["state"] in ("committed", "cancelled") or (row["lease_expires"] or 0) > time.time():
            continue
        try:
            await execute_job(store, job_id, resolve(job_id, store.frozen_request(job_id)), worker_id=worker_id)
            outcomes.append({"job_id": job_id, "status": "settled"})
        except Exception:
            outcomes.append({"job_id": job_id, "status": "recovery_required"})
        if len(outcomes) >= limit:
            break
    return tuple(outcomes)


def main(argv: list[str] | None = None) -> int:
    """Execute one local job from an operator-owned configuration file; emit safe versioned JSON."""
    parser = argparse.ArgumentParser(description="Execute an accepted curated release independently")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    try:
        from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader

        if args.config.stat().st_size > 1024**2:
            raise ValueError("worker configuration exceeds its bound")
        config: Any = parse_strict_json(args.config.read_bytes())
        if set(config) != {
            "schema_version",
            "job_root",
            "source_root",
            "destination_root",
            "selection",
            "context",
            "finalized",
        } or (config["schema_version"] != "curated-release-worker/v1" or config["finalized"] is not True):
            raise ValueError("invalid worker configuration")
        context = TypeAdapter(FreezeContext).validate_python(config["context"])
        if context.principal_scope_id != "localuser":
            raise ValueError("standalone local execution requires deliberate localuser composition")
        root = Path(config["source_root"])
        store = ReleaseJobStore(Path(config["job_root"]), settings=ReleaseRuntimeSettings.from_environ())
        binding = ConfiguredExecution(
            LocalReleaseSource(root, finalized=True),
            LocalSavedInputReader(root),
            TypeAdapter(ReleaseSelection).validate_python(config["selection"]),
            context,
            Path(config["destination_root"]),
        )
        result = asyncio.run(execute_job(store, args.job, binding))
        output = {"schema_version": "curated-release-worker-result/v1", "job_id": args.job, "status": "settled"}
        if result is not None:
            output["manifest_sha256"] = result.identity.manifest_sha256
        print(json.dumps(output))
        return 0
    except Exception:
        print(json.dumps({"schema_version": "curated-release-worker-result/v1", "status": "recovery_required"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
