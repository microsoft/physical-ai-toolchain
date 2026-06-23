"""Environment variable parsing helpers for LeRobot training scripts.

Single source of truth for interpreting JSON-array env vars such as
``BLOB_URLS`` and ``DATASET_ASSETS``. Consumers in :mod:`train` and
:mod:`checkpoints` import these helpers instead of comparing the raw env
string to ``""``/``"[]"`` sentinels, which is fragile to whitespace,
pretty-printed JSON, and ``[""]``/``[null]`` payloads.
"""

from __future__ import annotations

import json
import os


def parse_url_list_env(env_value: str | None) -> list[str]:
    """Return the stripped non-empty string entries from a JSON-array env value.

    Returns ``[]`` for any of: ``None``, the empty string, malformed JSON,
    JSON values that aren't a list, an empty list, or a list whose entries
    are all rejected (non-string, empty, or whitespace-only).
    """
    if not env_value:
        return []
    try:
        parsed = json.loads(env_value)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item.strip() for item in parsed if isinstance(item, str) and item.strip()]


def has_blob_urls(env_value: str | None = None) -> bool:
    """Return ``True`` iff ``env_value`` resolves to at least one blob URL.

    When called without an argument, reads ``BLOB_URLS`` from
    ``os.environ``. Tolerates whitespace and pretty-printed JSON.
    """
    if env_value is None:
        env_value = os.environ.get("BLOB_URLS", "")
    return bool(parse_url_list_env(env_value))
