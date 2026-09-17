"""Fresh-output publication and bounded-memory content fingerprints."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from .config import canonical, digest, require


def file_identity(path: Path) -> dict[str, Any]:
    """Hash a regular file and detect mutation while reading it."""
    path = path.expanduser().absolute()
    resolved = path.resolve(strict=True)
    require(resolved.is_file(), f"Not a regular file: {path}")
    before = resolved.stat()
    hasher = hashlib.sha256()
    with resolved.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    after = resolved.stat()
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    signature = [getattr(before, name) for name in fields]
    require(
        signature == [getattr(after, name) for name in fields] and path.resolve(strict=True) == resolved,
        f"File changed while hashing: {path}",
    )
    return {"path": str(path), "resolved_path": str(resolved), "signature": signature, "sha256": hasher.hexdigest()}


def fingerprint(path: Path) -> dict[str, Any]:
    """Hash checkpoint weights, configuration, and normalization assets without loading tensors."""
    root = path.expanduser().resolve(strict=True)
    require(root.is_dir(), "A checkpoint must be a local directory")
    files = {}
    for item in sorted(root.rglob("*")):
        require(not item.is_symlink(), f"Materialize checkpoint symlinks before fingerprinting: {item}")
        if item.is_dir():
            continue
        identity = file_identity(item)
        files[item.relative_to(root).as_posix()] = {
            "sha256": identity["sha256"],
            "size_bytes": identity["signature"][2],
        }
        require(len(files) <= 100000, "Checkpoint file inventory exceeds limit")
    require(bool(files), "Checkpoint directory is empty")
    return {"path": str(root), "sha256": digest(files), "files": files}


def unchanged(records: list[dict[str, Any]]) -> None:
    """Recheck source identities before publishing a completed result."""
    for record in records:
        require(file_identity(Path(record["path"])) == record, f"Input changed: {record['path']}")


def fresh_directory(path: Path, protected: list[Path]) -> Path:
    """Allocate a new output tree that cannot overwrite or contain an input tree."""
    raw = path.expanduser().absolute()
    require(not raw.exists() and not raw.is_symlink(), f"Output must be fresh: {raw}")
    output = raw.resolve()
    for source in protected:
        source = source.resolve()
        require(
            output != source and not output.is_relative_to(source) and not source.is_relative_to(output),
            f"Output overlaps protected input: {source}",
        )
    output.mkdir(parents=True, exist_ok=False)
    return output


def write_json(path: Path, value: Any, *, replace: bool = False) -> None:
    """Publish finite JSON atomically; only explicit progress files may be replaced."""
    require(path.parent.resolve(strict=True) == path.parent and not path.is_symlink(), "Output parent changed")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            Path(temporary).replace(path)
        else:
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def inventory(root: Path) -> dict[str, dict[str, Any]]:
    """Return artifact hashes; the final manifest is written after this inventory."""
    result = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "Evidence must not contain symlinks")
        if path.is_file():
            identity = file_identity(path)
            result[path.relative_to(root).as_posix()] = {
                "sha256": identity["sha256"],
                "size_bytes": identity["signature"][2],
            }
    return result
