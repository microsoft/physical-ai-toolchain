"""Independent release execution from durable requests and trusted configured bindings."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
import time
from collections.abc import Awaitable, Callable
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
    SourceMetadataError,
    StaleReleaseInputError,
)
from physical_ai_curation.release.jobs import (
    AuthorityError,
    CancellationError,
    CapacityError,
    ReleaseJobError,
    ReleaseJobStore,
    ReleaseRefusal,
    ReleaseRuntimeSettings,
    has_recovery_obligation,
)
from physical_ai_curation.release.publication import PublicationAttempt
from physical_ai_curation.release.record_storage import BlobLocation
from physical_ai_curation.saved_input import SavedInputReader

if TYPE_CHECKING:
    from physical_ai_curation.release.models import ReleaseResult


@dataclass(frozen=True)
class ConfiguredExecution:
    """Trusted composition; source may be absent for committed verification, never generation."""

    source: LocalReleaseSource | BlobReleaseSource | None
    reader: SavedInputReader
    selection: ReleaseSelection
    context: FreezeContext | None
    destination: Path | BlobLocation
    capture: Callable[[Callable[[], None]], Awaitable[tuple[bytes, bytes, int, ConfiguredExecution]]] | None = None


class _LifecycleMonitor:
    """Renew a claimed lease from a thread while blocking controller phases hold the event loop.

    Cancellation and authority loss are recorded as the first stop reason; later signals are ignored.
    """

    def __init__(self, store: ReleaseJobStore, job_id: str, fence: int) -> None:
        self._store = store
        self._job_id = job_id
        self._fence = fence
        self._interval = min(5.0, store.settings.lease_seconds / 4)
        self._valid_until = store.now() + store.settings.lease_seconds / 2
        self._stop = threading.Event()
        self._reason: Exception | None = None
        self._thread = threading.Thread(target=self._run, name=f"release-lease-{job_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def request_stop(self, error: Exception) -> None:
        if self._reason is None:
            self._reason = error

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join()

    def check(self) -> None:
        reason = self._reason
        if reason is not None:
            raise reason

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                started = self._store.now()
                cancelled = self._store.heartbeat(self._job_id, self._fence)
            except AuthorityError as error:
                self.request_stop(error)
                return
            except Exception as error:
                if self._store.now() + self._interval >= self._valid_until:
                    self.request_stop(error)
                    return
                continue
            self._valid_until = started + self._store.settings.lease_seconds / 2
            if cancelled:
                self.request_stop(CancellationError("cancellation requested"))


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
    from physical_ai_curation.release.verification import verify_published

    record = store.storage.snapshot().get(job_id)
    if record is None:
        return None
    package_id, attempt_id = record["attempt_key"].split("/", 1)
    attempt = PublicationAttempt(package_id, attempt_id, record["attempt_prefix"], record["attempt_source_dataset_id"])
    destination = configured.destination
    actual = destination.as_text() if isinstance(destination, BlobLocation) else str(destination.resolve())
    if record["destination"] != actual:
        raise AuthorityError("configured destination differs from accepted execution")
    if record["state"] == "cancelled":
        return None
    if record["state"] == "committed":
        with _publication_runtime(destination, attempt, store, job_id, record["fence"], None) as publisher:
            from physical_ai_curation.release.blob_worker import SourceAssociatedBlobPublisher
            from physical_ai_curation.release.local_worker import SourceAssociatedLocalPublisher

            if isinstance(publisher, (SourceAssociatedLocalPublisher, SourceAssociatedBlobPublisher)):
                publisher.associations.require_checkpoint(
                    publisher.intent, record["commit_digest"], job_id=job_id, fence=record["fence"]
                )
            return verify_published(publisher.store, record["commit_digest"])

    fence = store.claim(job_id, worker_id)
    record = store.storage.snapshot()[job_id]
    monitor = _LifecycleMonitor(store, job_id, fence)
    monitor.start()
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
                monitor.stop()
                store.purge_cancelled(job_id, fence)
            else:
                store.reconcile_local_outputs(job_id, fence)
                removed = store.cleanup_owned_outputs(job_id, fence, destination / attempt.prefix)
                store.settle_cancellation(job_id, fence, output_removed=removed)
                if not removed:
                    raise ReleaseInputError("owned output cleanup remains unresolved")
                monitor.stop()
                store.purge_cancelled(job_id, fence)
            return None
        if record["state"] in ("sealed", "committing"):
            digest = record["commit_digest"] or record["effects"][MANIFEST_PATH]["digest"]
            with _publication_runtime(destination, attempt, store, job_id, fence, None) as publisher:
                return publisher.publish(digest)
        raw = store.frozen_request(job_id)
        payload = parse_strict_json(raw)
        if payload.get("schema_version") == "curated-release-intent/v1":
            if configured.capture is None:
                raise ReleaseInputError("release intent requires configured execution capture")
            captured, binding, reservation, execution = await configured.capture(monitor.check)
            monitor.check()
            try:
                store.capture_execution(
                    job_id,
                    fence,
                    intent_request=raw,
                    frozen_request=captured,
                    execution_binding=binding,
                    reserved_bytes=reservation,
                )
            except CapacityError as error:
                raise ReleaseRefusal("insufficient_capacity") from error
            configured = execution
            raw = store.frozen_request(job_id)
            payload = parse_strict_json(raw)
        if payload.get("schema_version") != "curated-release-generation/v1":
            raise ReleaseInputError("unsupported durable generation request")
        if configured.source is None or configured.context is None:
            raise ReleaseInputError("generation requires the accepted source")
        frozen = TypeAdapter(FrozenReleaseInputs).validate_python(payload["frozen"])
        monitor.check()
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
        if isinstance(destination, BlobLocation) and isinstance(configured.source, BlobReleaseSource):
            identities = {item.path: item for item in frozen.content.source_objects}
            copies = {}
            for item in prepared.plan.objects:
                if item.action == "copy":
                    identity = identities[item.source_path]
                    bound = configured.source.binding(identity)
                    copies[item.output_path] = (identity.model_copy(update={"path": item.output_path}), bound.etag)
            with _publication_runtime(destination, attempt, store, job_id, fence, None) as publisher:
                assert isinstance(publisher, SupervisedBlobPublisher)
                publisher.recover_outputs(copies)
        store.validate_execution_binding(job_id, fence, destination, attempt, route=record["route"])
        monitor.check()
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
            checkpoint=monitor.check,
        )
        with suppress(AuthorityError):
            store.record_diagnostic(
                job_id,
                fence,
                operation,
                "validation",
                "succeeded",
                duration_ms=min(86_400_000, int((time.monotonic() - started) * 1000)),
            )
        return result
    except (ReleaseRefusal, SourceMetadataError, StaleReleaseInputError) as error:
        monitor.stop()
        store.require_input(job_id, fence, error.code)
        return None
    except CancellationError:
        monitor.stop()
        with suppress(AuthorityError):
            store.yield_cancellation(job_id, fence)
        raise
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
    finally:
        monitor.stop()


async def _dispatch(
    store: ReleaseJobStore,
    resolve: Callable[[str, bytes], ConfiguredExecution],
    job_id: str,
    worker_id: str,
) -> str | None:
    """Run one job and return a fixed failure category, or None when the worker settled it."""
    try:
        configured = resolve(job_id, store.frozen_request(job_id))
    except Exception:
        return "resolver_failed"
    try:
        await execute_job(store, job_id, configured, worker_id=worker_id)
    except CancellationError:
        return None
    except Exception:
        return "worker_failed"
    return None


async def _register_committed(
    registration: Callable[[str], Awaitable[bool]] | None,
    job_id: str,
) -> str | None:
    if registration is None:
        return None
    try:
        return "registration_settled" if await registration(job_id) else None
    except Exception:
        return "registration_failed"


async def run_queue(
    store: ReleaseJobStore,
    resolve: Callable[[str, bytes], ConfiguredExecution],
    *,
    worker_id: str = "release-worker",
    limit: int = 1,
    registration: Callable[[str], Awaitable[bool]] | None = None,
) -> tuple[dict[str, str], ...]:
    """Perform one bounded queue pass with a trusted resolver; never busy-poll or redispatch uncertainty.

    Only settled dispatches count toward ``limit``. Failures are recorded with exponential backoff, so a
    permanently failing job neither starves healthy jobs nor is retried until its backoff elapses.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("worker queue limit must be between 1 and 100")
    outcomes: list[dict[str, str]] = []
    settled = 0
    failed = 0
    candidates = sorted(
        store.storage.snapshot().items(),
        key=lambda item: (item[1]["state"] == "committed", item[1].get("retry_not_before") or 0, item[0]),
    )
    for job_id, row in candidates:
        if row["state"] == "committed":
            registration_status = await _register_committed(registration, job_id)
            if registration_status is not None:
                outcomes.append({"job_id": job_id, "status": registration_status})
                settled += 1
                if settled >= limit:
                    break
            continue
        if row["state"] in ("cancelled", "needs_input"):
            continue
        if (row["lease_expires"] or 0) > store.now():
            continue
        if not has_recovery_obligation(row) and (row["retry_not_before"] or 0) > store.now():
            continue
        observed_fence = row["fence"]
        category = await _dispatch(store, resolve, job_id, worker_id)
        if category is None:
            outcome = {"job_id": job_id, "status": "settled"}
            if store.storage.snapshot().get(job_id, {}).get("state") == "committed":
                registration_status = await _register_committed(registration, job_id)
                if registration_status is not None:
                    outcome["registration"] = registration_status
            outcomes.append(outcome)
            settled += 1
            if settled >= limit:
                break
            continue
        with suppress(ReleaseJobError):
            store.record_dispatch_failure(job_id, category, expected_fence=observed_fence, worker_id=worker_id)
        outcomes.append({"job_id": job_id, "status": "recovery_required"})
        failed += 1
        if failed >= store.settings.max_failed_dispatches:
            break
    return tuple(outcomes)


def main(argv: list[str] | None = None) -> int:
    """Execute one local job from an operator-owned configuration file; emit safe versioned JSON."""
    parser = argparse.ArgumentParser(description="Execute an accepted curated release independently")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    try:
        from physical_ai_curation.adapters.job_storage import LocalJobStore
        from physical_ai_curation.adapters.local_saved_input import LocalSavedInputReader, ScopedJudgeEvidenceAuthority

        if args.config.stat().st_size > 1024**2:
            raise ValueError("worker configuration exceeds its bound")
        config: Any = parse_strict_json(args.config.read_bytes())
        required = {
            "schema_version",
            "job_root",
            "source_root",
            "destination_root",
            "selection",
            "context",
            "finalized",
        }
        if (
            not isinstance(config, dict)
            or not required.issubset(config)
            or set(config) - required - {"judge_state_root"}
            or config["schema_version"] != "curated-release-worker/v1"
            or config["finalized"] is not True
        ):
            raise ValueError("invalid worker configuration")
        context = TypeAdapter(FreezeContext).validate_python(config["context"])
        if context.principal_scope_id != "localuser":
            raise ValueError("standalone local execution requires deliberate localuser composition")
        root = Path(config["source_root"])
        reader = LocalSavedInputReader(root)
        if "judge_state_root" in config:
            judge_root = config["judge_state_root"]
            if not isinstance(judge_root, str) or not judge_root.strip():
                raise ValueError("invalid judge state root")
            reader.judge_evidence = ScopedJudgeEvidenceAuthority(
                LocalJobStore(Path(judge_root)), reader.source_revision, principal_scope_id=context.principal_scope_id
            )
        store = ReleaseJobStore(Path(config["job_root"]), settings=ReleaseRuntimeSettings.from_environment(os.environ))
        binding = ConfiguredExecution(
            LocalReleaseSource(root, finalized=True),
            reader,
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
