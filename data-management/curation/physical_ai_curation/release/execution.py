"""Bounded private native effects; publication remains under the durable job coordinator."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pydantic import TypeAdapter

from physical_ai_curation.release.composition import NativeGenerationPlan
from physical_ai_curation.release.encoding import EncodeOptions, EncodingWindow, VideoOutput, encode_windows
from physical_ai_curation.release.jobs import CapacityError, ReleaseJobStore
from physical_ai_curation.release.media import ObjectPlan, ReleaseExecutionError, validate_processing_confirmation
from physical_ai_curation.release.store import CHUNK_BYTES
from physical_ai_curation.release.worker import run_supervised

MAX_SPEC_BYTES = 4 * 1024**2
MAX_RESULT_BYTES = 1024**2


class BoundedSink:
    """Seekable native sink that rejects growth before bytes exceed the admitted bound."""

    def __init__(self, stream: BinaryIO, limit: int) -> None:
        if type(limit) is not int or limit <= 0:
            raise CapacityError("native output bound is invalid")
        self._stream = stream
        self._limit = limit
        self.peak_bytes = 0

    def write(self, content: bytes) -> int:
        end = self._stream.tell() + len(content)
        if end > self._limit:
            raise CapacityError("native output exceeds its admitted bound")
        count = self._stream.write(content)
        self.peak_bytes = max(self.peak_bytes, self._stream.tell())
        return count

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        before = self._stream.tell()
        position = self._stream.seek(offset, whence)
        if not 0 <= position <= self._limit:
            self._stream.seek(before)
            raise CapacityError("native seek exceeds its admitted bound")
        return position

    def tell(self) -> int:
        return self._stream.tell()

    def flush(self) -> None:
        self._stream.flush()

    def writable(self) -> bool:
        return True

    @property
    def closed(self) -> bool:
        return self._stream.closed

    def seekable(self) -> bool:
        return True


@dataclass(frozen=True)
class EncodedObject:
    path: Path
    length: int
    sha256: str
    media: VideoOutput
    source_verification_bytes: int
    peak_scratch_bytes: int


@contextmanager
def encode_planned_object(
    store: ReleaseJobStore,
    job_id: str,
    fence: int,
    prepared: NativeGenerationPlan,
    obj: ObjectPlan,
    source_chunks: Iterable[bytes],
    *,
    confirmed_digest: str | None,
    scratch_bytes: int,
    output_bytes: int,
    checkpoint: Callable[[], None] | None = None,
) -> Iterator[EncodedObject]:
    """Encode a current confirmed object under supervision, exposing bytes only after proven stop.

    The composing service must first revalidate frozen inputs and regenerate ``prepared``.
    Only affected source bytes are privately spooled; unchanged objects use the publisher.
    """
    validate_processing_confirmation(prepared.plan, confirmed_digest=confirmed_digest)
    windows = prepared.encoding_windows(obj)
    if obj.source_length is None:
        raise ReleaseExecutionError("encoding source identity is missing")
    if type(output_bytes) is not int or output_bytes < max(1, obj.estimated_output_bytes):
        raise CapacityError("native output estimate exceeds its admitted bound")
    required = obj.source_length + output_bytes + MAX_SPEC_BYTES + MAX_RESULT_BYTES
    if type(scratch_bytes) is not int or scratch_bytes < required:
        raise CapacityError("native scratch estimate exceeds its admitted bound")
    directory = store.create_scratch_workspace(job_id, fence, scratch_bytes)
    try:
        source_path = directory / "source.bin"
        digest = hashlib.sha256()
        observed = 0
        with source_path.open("xb") as stream:
            for chunk in source_chunks:
                if checkpoint is not None:
                    checkpoint()
                if len(chunk) > CHUNK_BYTES or observed + len(chunk) > obj.source_length:
                    raise ReleaseExecutionError("encoding source exceeds its frozen length")
                observed += len(chunk)
                digest.update(chunk)
                stream.write(chunk)
        if observed != obj.source_length or (obj.source_sha256 is not None and digest.hexdigest() != obj.source_sha256):
            raise ReleaseExecutionError("encoding source does not match frozen content")
        spec = {
            "version": 1,
            "windows": TypeAdapter(tuple[EncodingWindow, ...]).dump_python(windows, mode="json"),
            "options": TypeAdapter(EncodeOptions).dump_python(prepared.encode_options, mode="json"),
            "fps": prepared.episodes[0].fps,
            "output_bytes": output_bytes,
        }
        request = json.dumps(spec, separators=(",", ":"), allow_nan=False).encode()
        if len(request) > MAX_SPEC_BYTES:
            raise CapacityError("native request exceeds its protocol bound")
        request_path = directory / "request.json"
        request_path.write_bytes(request)
        outcome = run_supervised(
            store,
            job_id,
            fence,
            [sys.executable, "-m", "physical_ai_curation.release.execution", "encode", str(request_path)],
            memory_bytes=store.settings.memory_bytes,
            file_bytes=max(output_bytes, MAX_RESULT_BYTES),
            heartbeat_seconds=min(5.0, store.settings.lease_seconds / 4),
            grace_seconds=store.settings.grace_seconds,
            liveness_seconds=store.settings.guardian_seconds,
            cwd=directory,
        )
        if outcome.reason != "exited" or outcome.returncode != 0:
            raise ReleaseExecutionError("supervised native encoding did not complete")
        result_path = directory / "result.json"
        if result_path.stat().st_size > MAX_RESULT_BYTES:
            raise CapacityError("native result exceeds its protocol bound")
        result = json.loads(result_path.read_bytes())
        media = TypeAdapter(VideoOutput).validate_python(result["media"])
        output = directory / "encoded.mp4"
        output_digest = hashlib.sha256()
        length = 0
        with output.open("rb") as stream:
            while chunk := stream.read(CHUNK_BYTES):
                if checkpoint is not None:
                    checkpoint()
                length += len(chunk)
                if length > output_bytes:
                    raise CapacityError("native output exceeds its admitted bound")
                output_digest.update(chunk)
        peak = observed + len(request) + int(result["peak_output_bytes"]) + result_path.stat().st_size
        if peak > scratch_bytes:
            raise CapacityError("native scratch exceeds its admitted bound")
        if obj.source_sha256 is not None:
            store.record_io(job_id, fence, directory.name, {"source_verification_bytes": observed})
        store.record_scratch_usage(job_id, fence, peak)
        yield EncodedObject(output, length, output_digest.hexdigest(), media, observed, peak)
    finally:
        store.cleanup_scratch_workspace(job_id, fence)
        store.release_scratch(job_id, fence)


def _encode(request_path: Path) -> None:
    if request_path.stat().st_size > MAX_SPEC_BYTES:
        raise CapacityError("native request exceeds its protocol bound")
    spec = json.loads(request_path.read_bytes())
    if spec["version"] != 1:
        raise ReleaseExecutionError("unsupported native protocol")
    windows = TypeAdapter(tuple[EncodingWindow, ...]).validate_python(spec["windows"])
    options = TypeAdapter(EncodeOptions).validate_python(spec["options"])
    directory = request_path.parent
    with (directory / "source.bin").open("rb") as source, (directory / "encoded.mp4").open("x+b") as output:
        sink = BoundedSink(output, spec["output_bytes"])
        media = encode_windows(source, sink, windows, fps=spec["fps"], options=options)
    result = {"media": TypeAdapter(VideoOutput).dump_python(media, mode="json"), "peak_output_bytes": sink.peak_bytes}
    content = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
    if len(content) > MAX_RESULT_BYTES:
        raise CapacityError("native result exceeds its protocol bound")
    (directory / "result.json").write_bytes(content)


def main(argv: list[str] | None = None) -> int:
    """Private trusted worker entry point; never exposed as client-provided argv."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2 or arguments[0] != "encode":
        return 2
    try:
        _encode(Path(arguments[1]))
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
