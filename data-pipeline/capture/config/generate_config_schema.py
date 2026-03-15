#!/usr/bin/env python3
"""Generate JSON Schema for ROS 2 recording configuration YAML files.

Reads Pydantic models from config_models and generates a JSON Schema compatible
with JSON Schema Draft 2020-12. The schema enables IDE autocomplete, validation,
and documentation for recording configuration YAML files.

Usage:
    python data-pipeline/capture/config/generate_config_schema.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# data-pipeline contains a hyphen, making it an invalid Python package name.
# Resolve the capture directory relative to this script for direct imports.
_CAPTURE_DIR = str(Path(__file__).resolve().parent.parent)
if _CAPTURE_DIR not in sys.path:
    sys.path.insert(0, _CAPTURE_DIR)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1


def main() -> int:
    """Generate JSON Schema from Pydantic models and write to output file.

    Returns:
        EXIT_SUCCESS on successful schema generation, EXIT_FAILURE on error.
    """
    try:
        from models.config_models import RecordingConfig

        schema = RecordingConfig.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = (
            "https://raw.githubusercontent.com/microsoft/physical-ai-toolchain/main/data-pipeline/capture/config/recording_config.schema.json"
        )

        output_path = Path("data-pipeline/capture/config/recording_config.schema.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        output_path.write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        print(f"Successfully generated schema: {output_path}")
        return EXIT_SUCCESS

    except Exception as e:
        print(f"Error generating schema: {e}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
