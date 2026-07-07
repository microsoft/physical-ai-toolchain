"""Shared hashing utilities for validating files."""

from __future__ import annotations

import hashlib
from pathlib import Path

HASH_CHUNK_BYTES = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Return the hex SHA256 of a file, streamed in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sha256_sidecar(filepath: str) -> str:
    """Write a ``sha256sum``-format ``<filepath>.sha256`` next to ``filepath``.

    Consumed by ``verify_sha256_sidecar`` to detect a corrupted model
    (TorchScript or ONNX) before it is deserialized/loaded. The sidecar is co-located
    with the model, so it guards against accidental corruption and partial writes,
    not against an attacker who can rewrite both; see that function's docstring for
    the out-of-band pinning needed to cover substitution.

    Returns:
        Path to the written sidecar.
    """
    path = Path(filepath)
    hex_digest = sha256_file(path)
    sidecar = path.with_name(f"{path.name}.sha256")
    with sidecar.open("w", encoding="utf-8") as f:
        f.write(f"{hex_digest}  {path.name}\n")
    return str(sidecar)


def verify_sha256_sidecar(model_path: str) -> None:
    """Verify an exported model against its co-located sidecar SHA256 before loading.

    Both ``torch.jit.load`` (TorchScript) and ``ort.InferenceSession`` (ONNX)
    consume attacker-influenceable model files — a corrupted TorchScript model is a
    code-execution vector, and a corrupted ONNX model yields attacker-controlled
    robot actions. ``export_policy.py`` writes ``<path>.sha256`` (``sha256sum``
    format) next to every exported model; the load is rejected on mismatch.

    Scope: the sidecar shares the model's directory and therefore its trust domain,
    so this defends against accidental corruption and partial writes — not against an
    attacker with write access to the artifact store, who can rewrite the co-located
    ``.sha256`` to match a poisoned model. For that threat the sidecar digest must be
    pinned out-of-band (e.g. an AzureML model-asset property) and checked before the
    sidecar is trusted. A missing sidecar is a hard failure for integrity-gated
    inputs — pass a sidecar produced by the export pipeline.

    Raises:
        FileNotFoundError: If the sidecar manifest is absent.
        ValueError: If the file digest does not match the manifest.
    """
    manifest = Path(f"{model_path}.sha256")
    if not manifest.exists():
        raise FileNotFoundError(
            f"Missing integrity manifest {manifest}; exported models must ship a SHA256 sidecar from export"
        )
    tokens = manifest.read_text(encoding="utf-8").split()
    if not tokens:
        raise ValueError(f"Empty integrity manifest {manifest}; expected a SHA256 digest")
    expected = tokens[0].strip().lower()

    actual = sha256_file(Path(model_path))

    if actual != expected:
        raise ValueError(f"SHA256 mismatch for {model_path}: expected {expected}, got {actual}")
