"""Supervised local payload effects with a sealed, bounded Linux memory protocol."""

from __future__ import annotations

import base64
import fcntl
import json
import os
import sys
from collections.abc import Iterable, Iterator
from dataclasses import asdict
from pathlib import Path

from pydantic import TypeAdapter

from physical_ai_curation.release.canonical import normalize_release_path, sha256_hex
from physical_ai_curation.release.composition import NativeGenerationPlan
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.jobs import ReleaseJobControl, ReleaseJobStore
from physical_ai_curation.release.models import MARKER_PATH, ReleaseResult, SourceObject
from physical_ai_curation.release.publication import LocalPublisher, PublicationAttempt
from physical_ai_curation.release.store import CHUNK_BYTES, LocalReleaseStore
from physical_ai_curation.release.verification import verify_published
from physical_ai_curation.release.worker import run_supervised

MAX_PROTOCOL_BYTES = 4 * 1024**2
LINUX_F_ADD_SEALS = 1033
LINUX_F_SEAL_SEAL = 0x0001
LINUX_F_SEAL_SHRINK = 0x0002
LINUX_F_SEAL_GROW = 0x0004
LINUX_F_SEAL_WRITE = 0x0008


class _ObservedDestinationStore(LocalReleaseStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.observed_bytes = 0

    def iter_bytes(self, path: str, *, chunk_size: int) -> Iterator[bytes]:
        for chunk in super().iter_bytes(path, chunk_size=chunk_size):
            self.observed_bytes += len(chunk)
            yield chunk


class SupervisedLocalPublisher(LocalPublisher):
    """Keep payload writers in durably registered process trees, never controller threads."""

    def __init__(
        self, destination: Path, attempt: PublicationAttempt, jobs: ReleaseJobStore, job_id: str, fence: int
    ) -> None:
        super().__init__(destination, attempt, ReleaseJobControl(jobs, job_id, fence))
        self.jobs, self.job_id, self.fence = jobs, job_id, fence
        self.store = _ObservedDestinationStore(self.root)

    @property
    def destination_read_bytes(self) -> int:
        return self.store.observed_bytes

    def _protocol(self, path: str, length: int, digest: str, spec: dict[str, object]) -> bytes:
        content = json.dumps(
            {
                "version": 1,
                "root": str(self.root.resolve()),
                "path": path,
                "length": length,
                "sha256": digest,
                "ledger_root": str(self.jobs.root.resolve()),
                "storage": self.jobs.storage.configuration(),
                "job_id": self.job_id,
                "fence": self.fence,
                "settings": asdict(self.jobs.settings),
                **spec,
            },
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        if len(content) > MAX_PROTOCOL_BYTES:
            raise ReleaseVerificationError("local effect protocol exceeds its bound")
        return content

    def _execute(self, path: str, length: int, content: bytes) -> None:
        """Run one sealed protocol in a registered child; the child owns every byte it creates."""
        descriptor = os.memfd_create("release-local-effect", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
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
            outcome = run_supervised(
                self.jobs,
                self.job_id,
                self.fence,
                [sys.executable, "-B", "-m", "physical_ai_curation.release.local_worker", str(descriptor)],
                memory_bytes=self.jobs.settings.memory_bytes,
                file_bytes=max(length, self.jobs.storage.configuration()["max_record_bytes"]),
                heartbeat_seconds=min(5.0, self.jobs.settings.lease_seconds / 4),
                grace_seconds=self.jobs.settings.grace_seconds,
                liveness_seconds=self.jobs.settings.guardian_seconds,
                inherit_fds=(descriptor,),
                effect_path=path,
            )
            if outcome.reason != "exited" or outcome.returncode != 0:
                raise ReleaseVerificationError("supervised local effect did not complete")
        finally:
            os.close(descriptor)

    def _run(self, path: str, length: int, digest: str, spec: dict[str, object]) -> None:
        path = normalize_release_path(path)
        if path == MARKER_PATH:
            raise ReleaseVerificationError("marker requires publication arbitration")
        content = self._protocol(path, length, digest, spec)
        with self.control.effect(self.attempt, path, length, digest) as replay:
            self._claim_root()
            if replay:
                if not any(item.path == path for item in self.store.list_objects()):
                    raise ReleaseVerificationError("acknowledged output is missing")
                super().write(path, (), length=length, sha256=digest)
                return
            self._execute(path, length, content)
            counters = (
                {"storage_copy_bytes": length, "source_verification_bytes": length}
                if spec.get("copy") is True
                else {"generated_output_bytes": length}
            )
            operation = sha256_hex(f"local:{self.attempt.prefix}:{path}:{length}:{digest}".encode())
            self.jobs.record_io(self.job_id, self.fence, operation, counters)

    def _write_marker(self, marker: bytes, expected_manifest_sha256: str) -> None:
        """Create the marker only through a registered child under a journaled commit-bound intent."""
        length, digest = len(marker), sha256_hex(marker)
        if self.jobs.begin_marker_effect(
            self.job_id, self.fence, self.attempt, expected_manifest_sha256, length, digest
        ):
            return
        content = self._protocol(
            MARKER_PATH,
            length,
            digest,
            {
                "mode": "inline",
                "content": base64.b64encode(marker).decode("ascii"),
                "marker": True,
                "manifest_digest": expected_manifest_sha256,
                "attempt": [self.attempt.package_id, self.attempt.attempt_id, self.attempt.prefix],
            },
        )
        try:
            self._execute(MARKER_PATH, length, content)
            if self.store.read_control_bytes(MARKER_PATH, max_bytes=length) != marker:
                raise ReleaseVerificationError("marker content does not match the commit decision")
            verify_published(self.store, expected_manifest_sha256)
        except BaseException:
            self.jobs.finish_effect(self.job_id, self.fence, MARKER_PATH, ok=False)
            raise
        self.jobs.finish_effect(self.job_id, self.fence, MARKER_PATH, ok=True)

    def publish(self, expected_manifest_sha256: str) -> ReleaseResult:
        """Resolve any predecessor marker intent before the common publication path runs."""
        self._recover_marker(expected_manifest_sha256)
        return super().publish(expected_manifest_sha256)

    def _recover_marker(self, expected_manifest_sha256: str) -> None:
        intent = self.jobs.marker_intent(self.job_id, self.fence)
        if intent is None or intent["status"] == "acknowledged":
            return
        self.jobs.recover_runtimes(self.job_id, self.fence)
        self.jobs.confirm_stopped(self.job_id, self.fence)
        self.jobs.remove_marker_temporaries(self.job_id, self.fence, self.root)
        observed = self._observe_marker(intent["length"], intent["digest"], expected_manifest_sha256)
        self.jobs.resolve_marker_effect(
            self.job_id, self.fence, observed=observed, length=intent["length"], digest=intent["digest"]
        )

    def _observe_marker(self, length: int, digest: str, expected_manifest_sha256: str) -> str:
        if not os.path.lexists(self.root / MARKER_PATH):
            return "absent"
        try:
            content = self.store.read_control_bytes(MARKER_PATH, max_bytes=length)
            if (len(content), sha256_hex(content)) != (length, digest):
                return "conflict"
            verify_published(self.store, expected_manifest_sha256)
        except (OSError, ReleaseVerificationError):
            return "conflict"
        return "complete"

    def write(self, path: str, chunks: Iterable[bytes], *, length: int, sha256: str) -> None:
        if path == MARKER_PATH:
            raise ReleaseVerificationError("marker requires publication arbitration")
        content = bytearray()
        for chunk in chunks:
            if len(chunk) > CHUNK_BYTES or len(content) + len(chunk) > MAX_PROTOCOL_BYTES // 2:
                raise ReleaseVerificationError("inline local payload exceeds its protocol bound")
            content.extend(chunk)
        self._run(path, length, sha256, {"mode": "inline", "content": base64.b64encode(content).decode("ascii")})

    def write_from_path(self, path: str, source: Path, *, length: int, sha256: str) -> None:
        """Stream one stopped native output directly from its private owned workspace."""
        self._run(
            path,
            length,
            sha256,
            {"mode": "file", "source_root": str(source.parent.resolve()), "source_path": source.name},
        )

    def write_lineage(self, path: str, prepared: NativeGenerationPlan, *, length: int, sha256: str) -> None:
        self._run(
            path,
            length,
            sha256,
            {"mode": "lineage", "prepared": TypeAdapter(NativeGenerationPlan).dump_python(prepared, mode="json")},
        )

    def copy(self, source_root: Path, source: SourceObject) -> None:
        original, destination = source_root.resolve(), self.root.resolve()
        if original == destination or original in destination.parents or destination in original.parents:
            raise ReleaseVerificationError("source and destination roots overlap")
        self._run(
            source.path,
            source.length,
            source.sha256,
            {"mode": "file", "source_root": str(original), "source_path": source.path, "copy": True},
        )


def _process(descriptor: int) -> None:
    with os.fdopen(descriptor, "rb") as stream:
        content = stream.read(MAX_PROTOCOL_BYTES + 1)
    if len(content) > MAX_PROTOCOL_BYTES:
        raise ReleaseVerificationError("local effect protocol exceeds its bound")
    spec = json.loads(content)
    if spec["version"] != 1:
        raise ReleaseVerificationError("unsupported local effect protocol")
    if spec["mode"] == "file":
        chunks = LocalReleaseStore(Path(spec["source_root"])).iter_bytes(spec["source_path"], chunk_size=CHUNK_BYTES)
    elif spec["mode"] == "inline":
        content = base64.b64decode(spec["content"], validate=True)
        chunks = (content[offset : offset + CHUNK_BYTES] for offset in range(0, len(content), CHUNK_BYTES))
    elif spec["mode"] == "lineage":
        from physical_ai_curation.release.coordinator import _lineage

        chunks = _lineage(TypeAdapter(NativeGenerationPlan).validate_python(spec["prepared"]))
    else:
        raise ReleaseVerificationError("unsupported local effect mode")
    root = Path(spec["root"])
    from physical_ai_curation.release.jobs import ReleaseRuntimeSettings
    from physical_ai_curation.release.record_storage import storage_from_configuration

    jobs = ReleaseJobStore(
        Path(spec["ledger_root"]),
        settings=ReleaseRuntimeSettings(**spec["settings"]),
        storage=storage_from_configuration(spec["storage"]),
    )
    if spec.get("marker") is True:
        jobs.authorize_marker_writer(
            spec["job_id"],
            spec["fence"],
            PublicationAttempt(*spec["attempt"]),
            spec["manifest_digest"],
            spec["length"],
            spec["sha256"],
        )
    if os.path.lexists(root / MARKER_PATH):
        raise ReleaseVerificationError("published package is immutable")

    def created(name: str, identity: os.stat_result) -> None:
        jobs.register_local_temporary(spec["job_id"], spec["fence"], spec["path"], name, identity)

    LocalReleaseStore(root).write_verified_object(
        spec["path"], chunks, length=spec["length"], sha256=spec["sha256"], on_created=created
    )


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
