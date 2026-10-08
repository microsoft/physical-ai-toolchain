"""Interprocess conditional publication for local curation resources."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .base import RevisionConflictError


def content_etag(content: bytes) -> str:
    return f'"{hashlib.sha256(content).hexdigest()}"'


@contextmanager
def _resource_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "r+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _check_revision(path: Path, if_match: str | None, if_none_match: bool) -> bool:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        content = None
    current_etag = content_etag(content) if content is not None else None
    if (if_none_match and content is not None) or (if_match is not None and if_match != current_etag):
        raise RevisionConflictError(current_etag)
    return content is not None


def write_conditional(path: Path, content: str, *, if_match: str | None = None, if_none_match: bool = False) -> str:
    with _resource_lock(path):
        _check_revision(path, if_match, if_none_match)
        encoded = content.encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return content_etag(encoded)


def delete_conditional(path: Path, *, if_match: str | None = None) -> bool:
    with _resource_lock(path):
        if not _check_revision(path, if_match, False):
            return False
        path.unlink()
        return True
