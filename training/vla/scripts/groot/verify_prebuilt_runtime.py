"""Verify a prebuilt GR00T N1.7 image against the requested Isaac-GR00T ref.

The image build writes ``/opt/groot-n17-image.json`` with the pinned
Isaac-GR00T commit and runtime package versions. A job fails before training
when the image was built from a different commit or its packages changed.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path


def verify_metadata(
    metadata: Mapping[str, object],
    requested_ref: str,
    version_of: Callable[[str], str] = importlib.metadata.version,
) -> None:
    """Raise ``RuntimeError`` when the image metadata does not match the runtime."""
    image_ref = metadata.get("isaac_groot_ref")
    if image_ref != requested_ref:
        raise RuntimeError(f"prebuilt Isaac-GR00T ref {image_ref} does not match requested ref {requested_ref}")
    expected = metadata.get("runtime_versions")
    if not isinstance(expected, Mapping) or not expected:
        raise RuntimeError("prebuilt image metadata has no runtime_versions")
    actual = {name: version_of(name) for name in expected}
    if actual != dict(expected):
        raise RuntimeError(f"prebuilt runtime versions changed: expected {dict(expected)}, found {actual}")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--metadata", type=Path, required=True, help="Image metadata JSON written at build time")
    parser.add_argument("--groot-ref", required=True, help="Requested Isaac-GR00T commit")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise RuntimeError(f"{args.metadata} is not a JSON object")
        verify_metadata(metadata, args.groot_ref)
    except (OSError, ValueError, RuntimeError, importlib.metadata.PackageNotFoundError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
