"""Guarded server-side Blob copies with independently authorized guardian children."""

from __future__ import annotations

import base64
import fcntl
import json
import os
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import TypeAdapter

from physical_ai_curation.release.azure_publication import (
    OWNER_LENGTH,
    OWNER_OPERATION,
    OWNER_SHA256,
    BlobPublisher,
    BlobReleaseStore,
    CopyProgress,
    _azure_errors,
    _content_matches,
    _copy_id,
    _copy_status,
    _is_owned,
    _match_conditions,
    _properties,
    _verified_stream,
)
from physical_ai_curation.release.canonical import normalize_release_path, sha256_hex
from physical_ai_curation.release.composition import NativeGenerationPlan
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.jobs import (
    BLOB_OWNER_CLEANUP_PATH,
    AuthorityError,
    ReleaseJobControl,
    ReleaseJobStore,
    ReleaseRuntimeSettings,
)
from physical_ai_curation.release.local_worker import (
    LINUX_F_ADD_SEALS,
    LINUX_F_SEAL_GROW,
    LINUX_F_SEAL_SEAL,
    LINUX_F_SEAL_SHRINK,
    LINUX_F_SEAL_WRITE,
    MAX_PROTOCOL_BYTES,
)
from physical_ai_curation.release.models import MARKER_PATH, ReleaseResult, SourceObject
from physical_ai_curation.release.publication import PublicationAttempt, ownership_bytes, ownership_path
from physical_ai_curation.release.record_storage import BlobLocation, storage_from_configuration
from physical_ai_curation.release.store import CHUNK_BYTES, LocalReleaseStore
from physical_ai_curation.release.verification import build_marker, verify_candidate, verify_published
from physical_ai_curation.release.worker import run_supervised


class _ObservedBlobStore(BlobReleaseStore):
    def __init__(self, container: Any, prefix: str) -> None:
        super().__init__(container, prefix)
        self.observed_bytes = 0

    def iter_bytes(self, path: str, *, chunk_size: int) -> Iterator[bytes]:
        for chunk in super().iter_bytes(path, chunk_size=chunk_size):
            self.observed_bytes += len(chunk)
            yield chunk


class SupervisedBlobPublisher(BlobPublisher):
    """Issue each server-side copy from a registered guardian child; refuse unsupervised mutations."""

    def __init__(
        self,
        container: Any,
        location: BlobLocation,
        attempt: PublicationAttempt,
        jobs: ReleaseJobStore,
        job_id: str,
        fence: int,
    ) -> None:
        super().__init__(container, attempt, ReleaseJobControl(jobs, job_id, fence), root_prefix=location.prefix)
        self.location = location
        self.jobs, self.job_id, self.fence = jobs, job_id, fence
        self.store = _ObservedBlobStore(container, self.root)
        self._active: SourceObject | None = None
        self._active_write: dict[str, Any] | None = None
        self._cleanup_deadline: float | None = None

    @property
    def destination_read_bytes(self) -> int:
        return self.store.observed_bytes

    def copy(
        self, source_container: Any, source: SourceObject, source_etag: str, *, source_path: str | None = None
    ) -> CopyProgress:
        self.jobs.validate_execution_binding(self.job_id, self.fence, self.location, self.attempt, route="blob")
        self._active = source
        try:
            progress = super().copy(source_container, source, source_etag, source_path=source_path)
            if progress.state == "complete":
                self._record_copy(source)
            return progress
        finally:
            self._active = None

    def _claim_root(self) -> None:
        """Inspect ownership without mutation; only the registered child may create it."""
        _inspect_root_ownership(self)

    def write(self, path: str, chunks: Iterable[bytes], *, length: int, sha256: str) -> None:
        if path == MARKER_PATH:
            raise ReleaseVerificationError("marker requires publication arbitration")
        content = bytearray()
        for chunk in chunks:
            if len(chunk) > CHUNK_BYTES or len(content) + len(chunk) > MAX_PROTOCOL_BYTES // 2:
                raise ReleaseVerificationError("inline blob payload exceeds its protocol bound")
            content.extend(chunk)
        self._write(path, length, sha256, {"mode": "inline", "content": base64.b64encode(content).decode("ascii")})

    def write_from_path(self, path: str, source: Path, *, length: int, sha256: str) -> None:
        self._write(
            path,
            length,
            sha256,
            {"mode": "file", "source_root": str(source.parent.resolve()), "source_path": source.name},
        )

    def write_lineage(self, path: str, prepared: NativeGenerationPlan, *, length: int, sha256: str) -> None:
        self._write(
            path,
            length,
            sha256,
            {"mode": "lineage", "prepared": TypeAdapter(NativeGenerationPlan).dump_python(prepared, mode="json")},
        )

    def _write(self, path: str, length: int, digest: str, spec: dict[str, Any]) -> None:
        self.jobs.validate_execution_binding(self.job_id, self.fence, self.location, self.attempt, route="blob")
        self._active_write = spec
        try:
            super().write(path, (), length=length, sha256=digest)
        finally:
            self._active_write = None

    def _create(self, blob: Any, path: str, stream: Iterator[bytes], length: int, sha256: str, operation: str) -> None:
        if self._active_write is None:
            raise ReleaseVerificationError("blob upload lacks a supervised request")
        self._execute(path, length, self._protocol(path, length, sha256, self._active_write))
        if not _content_matches(self.store, path, length, sha256):
            raise ReleaseVerificationError("uploaded content conflict")
        self.jobs.record_io(self.job_id, self.fence, operation, {"generated_upload_bytes": length})

    def abort(self, path: str, copy_id: str) -> None:
        raise ReleaseVerificationError("blob copy abort is not yet supervised")

    def reconcile_copy(self, source: SourceObject, source_etag: str, *, cancel: bool = False) -> CopyProgress | None:
        self.jobs.validate_execution_binding(
            self.job_id,
            self.fence,
            self.location,
            self.attempt,
            route="blob",
            recovery=True,
            cancellation=cancel,
        )
        self._claim_root()
        if cancel:
            properties = _properties(self.container.get_blob_client(self.root + source.path))
            copy_id = _copy_id(properties) if properties is not None else None
            with self.control.reconciliation(
                self.attempt,
                source.path,
                source.length,
                source.sha256,
                copy_id=copy_id,
                cancel=True,
            ) as observed:
                self._execute(
                    source.path,
                    source.length,
                    self._protocol(
                        source.path,
                        source.length,
                        source.sha256,
                        {
                            "mode": "blob_cleanup",
                            "source": source.model_dump(mode="json"),
                            "source_etag": source_etag,
                            "copy_id": copy_id,
                        },
                    ),
                )
                if _properties(self.container.get_blob_client(self.root + source.path)) is not None:
                    raise ReleaseVerificationError("cancelled copy residue remains unresolved")
                observed("absent")
            return None
        progress = super().reconcile_copy(source, source_etag, cancel=False)
        if progress is not None and progress.state == "complete":
            self._record_copy(source)
        return progress

    def cleanup_cancelled(self) -> None:
        """Remove a journaled unpublished root; retain capacity on any uncertainty."""
        self.jobs.blob_cleanup_status(self.job_id, self.fence, failed=False)
        self._cleanup_deadline = time.monotonic() + self.jobs.settings.cleanup_seconds
        try:
            self._cleanup_cancelled()
        except Exception:
            with suppress(AuthorityError):
                self.jobs.blob_cleanup_status(self.job_id, self.fence, failed=True)
            raise
        finally:
            self._cleanup_deadline = None

    def _cleanup_cancelled(self) -> None:
        self.jobs.recover_runtimes(self.job_id, self.fence)
        self.jobs.confirm_stopped(self.job_id, self.fence)
        self.jobs.validate_execution_binding(
            self.job_id,
            self.fence,
            self.location,
            self.attempt,
            route="blob",
            recovery=True,
            cancellation=True,
        )
        effects = {effect["path"]: effect for effect in self.jobs.cancellation_effects(self.job_id, self.fence)}

        def bounded() -> None:
            if self._cleanup_deadline is None or time.monotonic() >= self._cleanup_deadline:
                raise ReleaseVerificationError("blob cleanup budget exhausted")

        if self._marker_exists():
            raise ReleaseVerificationError("marked package cleanup is refused")
        self._claim_root()
        for item in self.container.list_blobs(name_starts_with=self.root):
            bounded()
            path = normalize_release_path(item.name[len(self.root) :])
            if path not in effects or path == BLOB_OWNER_CLEANUP_PATH:
                raise ReleaseVerificationError("unjournaled output prevents cleanup")
            effect = effects[path]
            properties = _properties(self.container.get_blob_client(self.root + path))
            if properties is None or not _cleanup_identity(properties, self.attempt, effect):
                raise ReleaseVerificationError("output ownership prevents cleanup")
            if _copy_status(properties) not in ("pending", "aborted", "failed"):
                for _chunk in _verified_stream(
                    self.store.iter_bytes(path, chunk_size=CHUNK_BYTES),
                    effect["length"],
                    effect["digest"],
                ):
                    bounded()
        for path, effect in effects.items():
            if path == BLOB_OWNER_CLEANUP_PATH:
                continue
            bounded()
            properties = _properties(self.container.get_blob_client(self.root + path))
            copy_id = _copy_id(properties) if properties is not None else None
            with self.control.reconciliation(
                self.attempt,
                path,
                effect["length"],
                effect["digest"],
                copy_id=copy_id,
                cancel=True,
            ) as observed:
                self._execute(
                    path,
                    effect["length"],
                    self._protocol(
                        path,
                        effect["length"],
                        effect["digest"],
                        {
                            "mode": "blob_remove",
                            "copy_id": copy_id,
                            "operation": properties.metadata.get(OWNER_OPERATION) if properties is not None else None,
                        },
                    ),
                )
                if _properties(self.container.get_blob_client(self.root + path)) is not None:
                    raise ReleaseVerificationError("blob cleanup outcome remains unresolved")
                observed("absent")
        bounded()
        length, digest = self.jobs.begin_blob_owner_cleanup(self.job_id, self.fence, self.attempt)
        self._execute(
            BLOB_OWNER_CLEANUP_PATH,
            length,
            self._protocol(
                BLOB_OWNER_CLEANUP_PATH,
                length,
                digest,
                {
                    "mode": "blob_owner_cleanup",
                    "copy_id": None,
                },
            ),
        )
        companion = self.parent_prefix + ownership_path(self.attempt.prefix)
        if (
            _properties(self.container.get_blob_client(companion)) is not None
            or next(iter(self.container.list_blobs(name_starts_with=self.root)), None) is not None
        ):
            raise ReleaseVerificationError("blob cleanup closure remains unresolved")
        self.jobs.reconcile_effect(
            self.job_id,
            self.fence,
            BLOB_OWNER_CLEANUP_PATH,
            observed="absent",
            length=length,
            digest=digest,
        )
        self.jobs.settle_cancellation(self.job_id, self.fence, output_removed=True)

    def _record_copy(self, source: SourceObject) -> None:
        operation = sha256_hex(f"blob-copy:{self.attempt.key}:{source.path}:{source.revision}".encode())
        self.jobs.record_io(self.job_id, self.fence, operation, {"storage_copy_bytes": source.length})

    def _publish_marker(self, marker: bytes, *, expected_manifest_sha256: str) -> None:
        length, digest = len(marker), sha256_hex(marker)
        with self.control.publication(self.attempt, expected_manifest_sha256):
            if self.jobs.begin_marker_effect(
                self.job_id, self.fence, self.attempt, expected_manifest_sha256, length, digest
            ):
                return
            spec = {
                "mode": "inline",
                "content": base64.b64encode(marker).decode("ascii"),
                "marker": True,
                "manifest_digest": expected_manifest_sha256,
            }
            try:
                self._execute(MARKER_PATH, length, self._protocol(MARKER_PATH, length, digest, spec))
                if self.store.read_control_bytes(MARKER_PATH, max_bytes=length) != marker:
                    raise ReleaseVerificationError("marker differs from the commit decision")
                verify_published(self.store, expected_manifest_sha256)
            except BaseException:
                self.jobs.finish_effect(self.job_id, self.fence, MARKER_PATH, ok=False)
                raise
            self.jobs.finish_effect(self.job_id, self.fence, MARKER_PATH, ok=True)

    def publish(self, expected_manifest_sha256: str) -> ReleaseResult:
        intent = self.jobs.marker_intent(self.job_id, self.fence)
        if intent is not None and intent["status"] != "acknowledged":
            self.jobs.recover_runtimes(self.job_id, self.fence)
            self.jobs.confirm_stopped(self.job_id, self.fence)
            observed = "absent"
            if self._marker_exists():
                observed = "conflict"
                try:
                    marker = self.store.read_control_bytes(MARKER_PATH, max_bytes=intent["length"])
                    if (len(marker), sha256_hex(marker)) == (intent["length"], intent["digest"]):
                        verify_published(self.store, expected_manifest_sha256)
                        observed = "complete"
                except ReleaseVerificationError:
                    pass
            self.jobs.resolve_marker_effect(
                self.job_id, self.fence, observed=observed, length=intent["length"], digest=intent["digest"]
            )
        return super().publish(expected_manifest_sha256)

    def _delete_owned_residue(self, blob: Any, copy_id: str, operation: str) -> None:
        raise ReleaseVerificationError("blob residue deletion is not yet supervised")

    def _dispatch_copy(self, _blob: Any, source_url: str, metadata: dict[str, str], source_etag: str) -> None:
        source = self._active
        if source is None:
            raise ReleaseVerificationError("blob copy dispatched without an active source")
        content = self._copy_protocol(source, source_url, metadata, source_etag)
        self._execute(source.path, source.length, content)

    def _copy_protocol(
        self, source: SourceObject, source_url: str, metadata: dict[str, str], source_etag: str
    ) -> bytes:
        return self._protocol(
            source.path,
            source.length,
            source.sha256,
            {
                "mode": "blob_copy",
                "source_url": source_url,
                "source_etag": source_etag,
                "source": source.model_dump(mode="json"),
            },
        )

    def _protocol(self, path: str, length: int, digest: str, operation: dict[str, Any]) -> bytes:
        path = normalize_release_path(path)
        spec = {
            "version": 1,
            "job_id": self.job_id,
            "fence": self.fence,
            "attempt": [self.attempt.package_id, self.attempt.attempt_id, self.attempt.prefix],
            "destination": self.location.as_text(),
            "account_url": self.location.account_url,
            "container": self.location.container,
            "prefix": self.location.prefix,
            "root": self.root,
            "path": path,
            "blob_name": self.root + path,
            "length": length,
            "sha256": digest,
            "ledger_root": str(self.jobs.root.resolve()),
            "storage": self.jobs.storage.configuration(),
            "settings": asdict(self.jobs.settings),
            **operation,
        }
        content = json.dumps(spec, separators=(",", ":"), allow_nan=False).encode()
        if len(content) > MAX_PROTOCOL_BYTES:
            raise ReleaseVerificationError("blob effect protocol exceeds its bound")
        return content

    def _command(self, descriptor: int) -> list[str]:
        return [sys.executable, "-B", "-m", "physical_ai_curation.release.blob_worker", str(descriptor)]

    def _execute(self, path: str, length: int, content: bytes) -> None:
        """Run one sealed copy protocol in a registered child; the child alone issues the copy."""
        descriptor = os.memfd_create("release-blob-effect", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        try:
            with os.fdopen(os.dup(descriptor), "wb") as stream:
                stream.write(content)
                stream.flush()
            os.lseek(descriptor, 0, os.SEEK_SET)
            fcntl.fcntl(
                descriptor,
                LINUX_F_ADD_SEALS,
                LINUX_F_SEAL_WRITE | LINUX_F_SEAL_GROW | LINUX_F_SEAL_SHRINK | LINUX_F_SEAL_SEAL,
            )
            reconciliation = json.loads(content)["mode"] in ("blob_cleanup", "blob_remove", "blob_owner_cleanup")
            timeout = self.jobs.settings.cleanup_seconds if reconciliation else None
            if timeout is not None and self._cleanup_deadline is not None:
                timeout = min(timeout, self._cleanup_deadline - time.monotonic())
                if timeout < 0.001:
                    raise ReleaseVerificationError("blob cleanup budget exhausted")
            outcome = run_supervised(
                self.jobs,
                self.job_id,
                self.fence,
                self._command(descriptor),
                memory_bytes=self.jobs.settings.memory_bytes,
                file_bytes=max(length, self.jobs.storage.configuration()["max_record_bytes"]),
                heartbeat_seconds=min(5.0, self.jobs.settings.lease_seconds / 4),
                grace_seconds=self.jobs.settings.grace_seconds,
                liveness_seconds=self.jobs.settings.guardian_seconds,
                inherit_fds=(descriptor,),
                effect_path=path,
                reconciliation=reconciliation,
                timeout_seconds=timeout,
            )
            if outcome.reason != "exited" or outcome.returncode != 0:
                raise ReleaseVerificationError("supervised blob copy did not complete")
        finally:
            os.close(descriptor)


class _RegisteredCopyControl:
    def __init__(self, jobs: ReleaseJobStore, spec: dict[str, Any]) -> None:
        self.jobs, self.spec = jobs, spec

    @contextmanager
    def effect(self, attempt: PublicationAttempt, path: str, length: int, digest: str) -> Iterator[bool]:
        self.jobs.authorize_blob_writer(
            self.spec["job_id"],
            self.spec["fence"],
            attempt,
            path,
            length,
            digest,
            destination=self.spec["destination"],
        )
        yield False

    @contextmanager
    def reconciliation(
        self,
        attempt: PublicationAttempt,
        path: str,
        length: int,
        digest: str,
        *,
        copy_id: str | None,
        cancel: bool,
    ) -> Iterator[Callable[[str], None]]:
        if (
            self.spec["mode"] != "blob_cleanup"
            or not cancel
            or (copy_id is not None and copy_id != self.spec["copy_id"])
        ):
            raise ReleaseVerificationError("copy cleanup identity differs from its journal")
        self.jobs.authorize_blob_writer(
            self.spec["job_id"],
            self.spec["fence"],
            attempt,
            path,
            length,
            digest,
            destination=self.spec["destination"],
            reconciliation=True,
            copy_id=copy_id,
        )
        yield lambda observation: None

    @contextmanager
    def publication(self, attempt: PublicationAttempt, digest: str) -> Iterator[None]:
        if self.spec.get("marker") is not True:
            raise ReleaseVerificationError("marker requires a commit-bound request")
        self.jobs.authorize_marker_writer(
            self.spec["job_id"],
            self.spec["fence"],
            attempt,
            digest,
            self.spec["length"],
            self.spec["sha256"],
        )
        yield


def _inspect_root_ownership(publisher: BlobPublisher) -> None:
    blob = publisher.container.get_blob_client(publisher.parent_prefix + ownership_path(publisher.attempt.prefix))
    existing = _properties(blob)
    if existing is None:
        if next(iter(publisher.container.list_blobs(name_starts_with=publisher.root)), None) is not None:
            raise ReleaseVerificationError("output collision: release root exists without ownership")
        return
    content = ownership_bytes(publisher.attempt)
    if existing.size != len(content):
        raise ReleaseVerificationError("release root ownership conflict")
    download = blob.download_blob(etag=existing.etag, match_condition=_match_conditions().IfNotModified)
    try:
        observed = b"".join(_verified_stream(download.chunks(), len(content), sha256_hex(content)))
    except ReleaseVerificationError:
        raise ReleaseVerificationError("release root ownership conflict") from None
    if observed != content:
        raise ReleaseVerificationError("release root ownership conflict")


def _cleanup_identity(properties: Any, attempt: PublicationAttempt, effect: dict[str, Any]) -> bool:
    operation = properties.metadata.get(OWNER_OPERATION)
    return (
        bool(operation)
        and _is_owned(properties.metadata, attempt, operation)
        and (properties.metadata.get(OWNER_LENGTH), properties.metadata.get(OWNER_SHA256))
        == (str(effect["length"]), effect["digest"])
    )


def _remove_blob(publisher: BlobPublisher, jobs: ReleaseJobStore, spec: dict[str, Any]) -> None:
    if publisher._marker_exists():
        raise ReleaseVerificationError("marked package cleanup is refused")
    _inspect_root_ownership(publisher)
    if spec["mode"] == "blob_owner_cleanup":
        if (
            spec["path"] != BLOB_OWNER_CLEANUP_PATH
            or next(iter(publisher.container.list_blobs(name_starts_with=publisher.root)), None) is not None
        ):
            raise ReleaseVerificationError("companion cleanup requires an empty payload root")
        blob = publisher.container.get_blob_client(publisher.parent_prefix + ownership_path(publisher.attempt.prefix))
        properties = _properties(blob)
        if properties is None:
            return
        content = ownership_bytes(publisher.attempt)
        if properties.size != len(content):
            raise ReleaseVerificationError("companion ownership conflict")
        observed = b"".join(
            _verified_stream(
                blob.download_blob(etag=properties.etag, match_condition=_match_conditions().IfNotModified).chunks(),
                len(content),
                sha256_hex(content),
            )
        )
        if observed != content:
            raise ReleaseVerificationError("companion ownership conflict")
        blob.delete_blob(etag=properties.etag, match_condition=_match_conditions().IfNotModified)
        return
    blob = publisher.container.get_blob_client(spec["blob_name"])
    properties = _properties(blob)
    if properties is None:
        return
    if not _cleanup_identity(properties, publisher.attempt, {"length": spec["length"], "digest": spec["sha256"]}) or (
        properties.metadata.get(OWNER_OPERATION),
        _copy_id(properties),
    ) != (spec["operation"], spec["copy_id"]):
        raise ReleaseVerificationError("output ownership prevents cleanup")
    if _copy_status(properties) == "pending":
        try:
            blob.abort_copy(spec["copy_id"])
        except _azure_errors().HttpResponseError as error:
            if error.status_code != 409:
                raise
    jobs.authorize_blob_writer(
        spec["job_id"],
        spec["fence"],
        publisher.attempt,
        spec["path"],
        spec["length"],
        spec["sha256"],
        destination=spec["destination"],
        reconciliation=True,
        copy_id=spec["copy_id"],
    )
    if publisher._marker_exists():
        raise ReleaseVerificationError("marked package cleanup is refused")
    publisher._delete_owned_residue(blob, spec["copy_id"], spec["operation"])


def _process(
    descriptor: int,
    *,
    container_factory: Callable[[str, str], Any] | None = None,
    source_authorization: str | None = None,
) -> None:
    """Authorize the sealed copy against the ledger before any Azure import or remote call."""
    with os.fdopen(descriptor, "rb") as stream:
        content = stream.read(MAX_PROTOCOL_BYTES + 1)
    if len(content) > MAX_PROTOCOL_BYTES:
        raise ReleaseVerificationError("blob effect protocol exceeds its bound")
    spec = json.loads(content)
    if spec["version"] != 1 or spec["mode"] not in (
        "blob_copy",
        "blob_cleanup",
        "blob_remove",
        "blob_owner_cleanup",
        "inline",
        "file",
        "lineage",
    ):
        raise ReleaseVerificationError("unsupported blob effect protocol")
    path = normalize_release_path(spec["path"])
    location = BlobLocation(spec["account_url"], spec["container"], spec["prefix"])
    attempt = PublicationAttempt(*spec["attempt"])
    root = (location.prefix + "/" if location.prefix else "") + attempt.prefix + "/"
    if location.as_text() != spec["destination"] or root != spec["root"] or root + path != spec["blob_name"]:
        raise ReleaseVerificationError("blob destination is outside the authorized root")
    jobs = ReleaseJobStore(
        Path(spec["ledger_root"]),
        settings=ReleaseRuntimeSettings(**spec["settings"]),
        storage=storage_from_configuration(spec["storage"]),
    )
    jobs.authorize_blob_writer(
        spec["job_id"],
        spec["fence"],
        attempt,
        path,
        spec["length"],
        spec["sha256"],
        destination=spec["destination"],
        reconciliation=spec["mode"] in ("blob_cleanup", "blob_remove", "blob_owner_cleanup"),
        copy_id=spec.get("copy_id"),
    )

    with ExitStack() as stack:
        if container_factory is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import ContainerClient

            credential = stack.enter_context(DefaultAzureCredential())
            if spec["mode"] == "blob_copy":
                source_authorization = "Bearer " + credential.get_token("https://storage.azure.com/.default").token

            def container_factory(account_url: str, name: str) -> Any:
                return stack.enter_context(
                    ContainerClient(
                        account_url,
                        name,
                        credential=credential,
                        connection_timeout=jobs.settings.recovery_seconds,
                        read_timeout=jobs.settings.recovery_seconds,
                        retry_total=0,
                    )
                )

        publisher = BlobPublisher(
            container_factory(location.account_url, location.container),
            attempt,
            _RegisteredCopyControl(jobs, spec),
            root_prefix=location.prefix,
            source_authorization=source_authorization,
        )
        if spec["mode"] in ("blob_remove", "blob_owner_cleanup"):
            _remove_blob(publisher, jobs, spec)
            return
        if spec["mode"] == "blob_cleanup":
            source = SourceObject.model_validate(spec["source"])
            if (source.path, source.length, source.sha256) != (path, spec["length"], spec["sha256"]):
                raise ReleaseVerificationError("cleanup identity does not match the authorized effect")
            publisher.reconcile_copy(source, spec["source_etag"], cancel=True)
            return
        if spec["mode"] == "blob_copy":
            source = SourceObject.model_validate(spec["source"])
            if (source.path, source.length, source.sha256) != (path, spec["length"], spec["sha256"]):
                raise ReleaseVerificationError("source identity does not match the authorized effect")
            address, origin = urlsplit(spec["source_url"]), urlsplit(location.account_url)
            if (
                (address.scheme, address.netloc) != (origin.scheme, origin.netloc)
                or address.query
                or address.fragment
                or address.username is not None
            ):
                raise ReleaseVerificationError("source URL must be a query-free same-account address")
            source_container, separator, source_name = unquote(address.path).lstrip("/").partition("/")
            if not separator:
                raise ReleaseVerificationError("source URL does not identify a blob")
            source_location = BlobLocation(location.account_url, source_container)
            publisher.copy(
                container_factory(source_location.account_url, source_location.container),
                source,
                spec["source_etag"],
                source_path=normalize_release_path(source_name),
            )
            return
        if spec["mode"] == "inline":
            content = base64.b64decode(spec["content"], validate=True)
            chunks = (content[offset : offset + CHUNK_BYTES] for offset in range(0, len(content), CHUNK_BYTES))
        elif spec["mode"] == "file":
            chunks = LocalReleaseStore(Path(spec["source_root"])).iter_bytes(
                spec["source_path"], chunk_size=CHUNK_BYTES
            )
        else:
            from physical_ai_curation.release.coordinator import _lineage

            chunks = _lineage(TypeAdapter(NativeGenerationPlan).validate_python(spec["prepared"]))
        if spec.get("marker") is True:
            marker = b"".join(_verified_stream(chunks, spec["length"], spec["sha256"]))
            candidate = verify_candidate(publisher.store, spec["manifest_digest"])
            if build_marker(candidate) != marker:
                raise ReleaseVerificationError("marker differs from the verified candidate")
            publisher._claim_root()
            publisher._publish_marker(marker, expected_manifest_sha256=spec["manifest_digest"])
        else:
            publisher.write(path, chunks, length=spec["length"], sha256=spec["sha256"])


def main() -> int:
    """Run only the private inherited-descriptor protocol; never print request contents."""
    try:
        if len(sys.argv) != 2:
            return 2
        _process(int(sys.argv[1]))
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
