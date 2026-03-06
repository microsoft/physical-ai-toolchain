"""Blob path validation for Azure Storage folder structure.

Validates blob paths against naming conventions:
- Lowercase only (no uppercase)
- Hyphens for separators (no underscores or spaces in folder names)
- Date format: YYYY-MM-DD
- Timestamp format: YYYYMMDD_HHMMSS
- File extensions: .mcap, .bag, .json, .npz, .mp4, .pt, .onnx, .jit

Validation enforces folder structure and naming patterns for:
- raw: ROS bags from edge devices
- converted: LeRobot datasets
- reports: Validation and inference reports
- checkpoints: Model checkpoints from training
"""

import re
from typing import Literal, Optional

DataType = Literal["raw", "converted", "reports", "checkpoints"]

# Regex patterns for each data type
PATTERNS = {
    "raw": r"^raw/[a-z0-9-]+/\d{4}-\d{2}-\d{2}/[a-z0-9-]+\.(mcap|bag)$",
    "converted": r"^converted/[a-z0-9-]+(-v\d+)?/(meta|data|videos)/.+$",
    "reports": r"^reports/[a-z0-9-]+/\d{4}-\d{2}-\d{2}/[a-z0-9-_]+\.(json|npz|mp4)$",
    "checkpoints": r"^checkpoints/[a-z0-9-]+/\d{8}_\d{6}(_step_\d+)?\.(pt|onnx|jit)$",
}


def validate_blob_path(blob_name: str, data_type: DataType) -> bool:
    """Validate blob path follows naming conventions.

    Args:
        blob_name: Full blob path (e.g., "raw/robot-01/2026-03-05/episode-001.mcap")
        data_type: Type of data ("raw", "converted", "reports", "checkpoints")

    Returns:
        True if path is valid, False otherwise

    Raises:
        ValueError: If data_type is not recognized
    """
    if data_type not in PATTERNS:
        raise ValueError(
            f"Unknown data type: {data_type}. Must be one of {list(PATTERNS.keys())}"
        )

    return bool(re.match(PATTERNS[data_type], blob_name))


def get_validation_error(blob_name: str, data_type: DataType) -> Optional[str]:
    """Get validation error message if path is invalid.

    Args:
        blob_name: Full blob path
        data_type: Type of data

    Returns:
        Error message if invalid, None if valid
    """
    if validate_blob_path(blob_name, data_type):
        return None

    errors = []
    if any(c.isupper() for c in blob_name):
        errors.append("contains uppercase characters (must be lowercase only)")
    if " " in blob_name:
        errors.append("contains spaces (use hyphens instead)")

    error_detail = ", ".join(errors) if errors else "does not match expected pattern"
    return (
        f"Invalid blob path '{blob_name}': {error_detail}. "
        "See docs/cloud/blob-storage-structure.md for path patterns."
    )
