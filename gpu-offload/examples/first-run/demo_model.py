from __future__ import annotations

import os


def predict(values: list[int]) -> dict[str, list[int] | str]:
    return {
        "executed_by": os.environ.get("HOSTNAME", "unknown"),
        "predictions": [value * value for value in values],
    }
