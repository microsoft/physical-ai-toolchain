"""Shared JSON serialization utilities for storage adapters."""

import json
from datetime import datetime


class DateTimeEncoder(json.JSONEncoder):
    """JSON encoder that serializes datetime objects to ISO 8601 strings."""

    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)
