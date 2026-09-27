from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path

EXPECTED_RUNTIME = {
    "accelerate": "1.13.0",
    "flash-attn": "2.7.4.post1",
    "numpy": "1.26.4",
    "torch": "2.7.1+cu128",
    "torchvision": "0.22.1+cu128",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--isaac-groot-ref", required=True)
    parser.add_argument("--uv-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    actual = {
        name: importlib.metadata.version(name)
        for name in EXPECTED_RUNTIME
    }
    if actual != EXPECTED_RUNTIME:
        raise RuntimeError(f"locked runtime differs from Item 9: {actual}")

    args.output.write_text(
        json.dumps(
            {
                "isaac_groot_ref": args.isaac_groot_ref,
                "runtime_versions": actual,
                "uv_version": args.uv_version,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
