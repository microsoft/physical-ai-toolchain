"""Narrow read/list protocol for release storage and a local filesystem adapter.

The adapter enforces the same rules a blob adapter must enforce: normalized
relative paths, regular files only, and no symbolic links.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from physical_ai_curation.release.canonical import canonical_json_bytes, normalize_release_path, sha256_hex
from physical_ai_curation.release.errors import ReleaseVerificationError

CHUNK_BYTES = 1024 * 1024


def blob_revision(etag: str, version_id: str | None) -> str:
    """Bind a storage revision, normalizing unquoted Azure listing ETags to property-response form."""
    if not etag.startswith(('"', 'W/"')):
        etag = f'"{etag}"'
    return sha256_hex(canonical_json_bytes({"etag": etag, "version_id": version_id}))


def blob_source_identity(container_url: str, path: str) -> str:
    """Bind the credential-free source container and object path without exposing either."""
    return sha256_hex(
        canonical_json_bytes({"container": container_url.rstrip("/"), "path": normalize_release_path(path)})
    )


@dataclass(frozen=True)
class ObjectStat:
    """Listed object with its declared byte length and optional storage revision evidence.

    ``copy_operation_sha256`` binds a revision to the ownership operation and server copy ID that
    produced it. ``source_revision`` is set when the copy was conditioned on a source storage revision
    rather than a content digest. ``uploaded_sha256`` is set only for payloads written by this release,
    whose digest was verified against the streamed bytes at write time. Both are revision-bound evidence,
    not independent content digests.
    """

    path: str
    length: int
    revision: str | None = None
    copy_operation_sha256: str | None = None
    source_revision: str | None = None
    uploaded_sha256: str | None = None
    source_identity: str | None = None


class ReleaseStore(Protocol):
    """Listing, streaming, and bounded control reads required by release verification."""

    def list_objects(self) -> Sequence[ObjectStat]:
        raise NotImplementedError

    def iter_bytes(self, path: str, *, chunk_size: int) -> Iterator[bytes]:
        raise NotImplementedError

    def read_control_bytes(self, path: str, *, max_bytes: int) -> bytes:
        raise NotImplementedError


def _safe_flags() -> tuple[int, int]:
    """Return (directory, nofollow) open flags or fail closed when unavailable."""
    directory = getattr(os, "O_DIRECTORY", None)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if directory is None or nofollow is None or os.stat not in os.supports_dir_fd or os.listdir not in os.supports_fd:
        raise ReleaseVerificationError("descriptor-bound traversal is unavailable; refusing to verify")
    return directory, nofollow


def _same_object(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


def _open_child_directory(parent: int, name: str) -> int:
    directory, nofollow = _safe_flags()
    try:
        descriptor = os.open(name, os.O_RDONLY | directory | nofollow, dir_fd=parent)
    except OSError as exc:
        raise ReleaseVerificationError(f"symbolic link or non-directory not allowed: {name}") from exc
    try:
        opened = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(opened.st_mode) or not _same_object(opened, current):
            raise ReleaseVerificationError(f"directory changed during traversal: {name}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_child_file(parent: int, name: str) -> int:
    _, nofollow = _safe_flags()
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if nonblocking is None:
        raise ReleaseVerificationError("nonblocking file access is unavailable; refusing to read")
    try:
        descriptor = os.open(name, os.O_RDONLY | nofollow | nonblocking, dir_fd=parent)
    except OSError as exc:
        raise ReleaseVerificationError(f"unreadable release object: {name}") from exc
    try:
        opened = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(opened.st_mode) or not _same_object(opened, current):
            raise ReleaseVerificationError(f"non-regular or replaced file not allowed: {name}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class LocalReleaseStore:
    """Filesystem adapter rooted at one release directory.

    Every component is opened relative to its parent descriptor with no-follow
    semantics, and identities are compared before and after use. Platforms
    without these primitives fail closed.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _open_root(self, *, create: bool = False) -> int:
        directory, nofollow = _safe_flags()
        absolute = self._root.absolute()
        try:
            descriptor = os.open(absolute.anchor, os.O_RDONLY | directory | nofollow)
            for segment in absolute.parts[1:]:
                try:
                    if create:
                        with suppress(FileExistsError):
                            os.mkdir(segment, dir_fd=descriptor)
                            os.fsync(descriptor)
                    child = _open_child_directory(descriptor, segment)
                finally:
                    os.close(descriptor)
                descriptor = child
        except (OSError, ReleaseVerificationError) as exc:
            raise ReleaseVerificationError("release root must be a real directory") from exc
        return descriptor

    def _check_chain(self, descriptors: list[int], segments: list[str]) -> None:
        current_root = self._open_root()
        try:
            if not _same_object(os.fstat(descriptors[0]), os.fstat(current_root)):
                raise ReleaseVerificationError("release root changed during read")
            for index, segment in enumerate(segments):
                current = os.stat(segment, dir_fd=descriptors[index], follow_symlinks=False)
                if not _same_object(os.fstat(descriptors[index + 1]), current):
                    raise ReleaseVerificationError("release path changed during read")
        finally:
            os.close(current_root)

    def list_objects(self) -> list[ObjectStat]:
        root = self._open_root()
        try:
            before = os.fstat(root)
            objects: list[ObjectStat] = []
            self._walk(root, "", objects)
            if not _same_object(before, os.stat(self._root, follow_symlinks=False)):
                raise ReleaseVerificationError("release root changed during listing")
            return objects
        finally:
            os.close(root)

    def _walk(self, directory_fd: int, prefix: str, objects: list[ObjectStat]) -> None:
        for name in sorted(os.listdir(directory_fd)):
            relative = f"{prefix}{name}"
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ReleaseVerificationError(f"symbolic link not allowed: {relative}")
            if stat.S_ISDIR(info.st_mode):
                child = _open_child_directory(directory_fd, name)
                try:
                    self._walk(child, f"{relative}/", objects)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ReleaseVerificationError(f"non-regular file not allowed: {relative}")
            objects.append(ObjectStat(path=normalize_release_path(relative), length=info.st_size))

    def iter_bytes(self, path: str, *, chunk_size: int) -> Iterator[bytes]:
        if not 0 < chunk_size <= CHUNK_BYTES:
            raise ReleaseVerificationError(f"chunk size must be between 1 and {CHUNK_BYTES}")
        segments = normalize_release_path(path).split("/")
        with ExitStack() as stack:
            root = self._open_root()
            stack.callback(os.close, root)
            descriptors = [root]
            for segment in segments[:-1]:
                child = _open_child_directory(descriptors[-1], segment)
                stack.callback(os.close, child)
                descriptors.append(child)
            file_fd = _open_child_file(descriptors[-1], segments[-1])
            stack.callback(os.close, file_fd)
            descriptors.append(file_fd)
            before = os.fstat(file_fd)
            while chunk := os.read(file_fd, chunk_size):
                yield chunk
            after = os.fstat(file_fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ReleaseVerificationError(f"object changed during read: {path}")
            self._check_chain(descriptors, segments)

    def write_verified_object(
        self,
        path: str,
        chunks: Iterable[bytes],
        *,
        length: int,
        sha256: str,
        on_created: Callable[[str, os.stat_result], None] | None = None,
    ) -> int:
        """Create an independent regular object only after its complete stream verifies.

        All traversal, temporary writes and create-only publication use held directory
        descriptors. This is a storage primitive, not a release commit or durable effect log.
        """
        segments = normalize_release_path(path).split("/")
        if type(length) is not int or length < 0:
            raise ReleaseVerificationError("expected object length must be nonnegative")
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ReleaseVerificationError("expected object digest must be lowercase SHA-256")
        root = self._open_root(create=True)
        descriptors = [root]
        partial = f".curation-copy-{uuid4().hex}.partial"
        owns_partial = False
        stack = ExitStack()
        try:
            stack.callback(os.close, root)
            for segment in segments[:-1]:
                with suppress(FileExistsError):
                    os.mkdir(segment, dir_fd=descriptors[-1])
                    os.fsync(descriptors[-1])
                child = _open_child_directory(descriptors[-1], segment)
                stack.callback(os.close, child)
                descriptors.append(child)
            _, nofollow = _safe_flags()
            descriptor = os.open(
                partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=descriptors[-1]
            )
            owns_partial = True
            stack.callback(os.close, descriptor)
            digest = hashlib.sha256()
            total = 0
            with os.fdopen(descriptor, "wb", closefd=False) as output:
                if on_created is not None:
                    on_created(partial, os.fstat(output.fileno()))
                for chunk in chunks:
                    if len(chunk) > CHUNK_BYTES:
                        raise ReleaseVerificationError("object stream chunk exceeds the bounded buffer")
                    total += len(chunk)
                    if total > length:
                        raise ReleaseVerificationError("source drift: object exceeds its expected length")
                    digest.update(chunk)
                    output.write(chunk)
                if total != length or digest.hexdigest() != sha256:
                    raise ReleaseVerificationError("source drift: object content differs from its expected identity")
                output.flush()
                os.fsync(output.fileno())
            self._check_chain(descriptors, segments[:-1])
            try:
                os.link(
                    partial,
                    segments[-1],
                    src_dir_fd=descriptors[-1],
                    dst_dir_fd=descriptors[-1],
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise ReleaseVerificationError("output already exists; create-only publication refused") from None
            os.fsync(descriptors[-1])
            return total
        finally:
            try:
                if owns_partial:
                    os.unlink(partial, dir_fd=descriptors[-1])
            finally:
                stack.close()

    def read_control_bytes(self, path: str, *, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in self.iter_bytes(path, chunk_size=min(CHUNK_BYTES, max_bytes + 1)):
            total += len(chunk)
            if total > max_bytes:
                raise ReleaseVerificationError(f"control object exceeds {max_bytes} bytes: {path}")
            chunks.append(chunk)
        return b"".join(chunks)
