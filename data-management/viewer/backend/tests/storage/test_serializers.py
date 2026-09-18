"""Tests for shared serialization utilities."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.api.storage.serializers import DateTimeEncoder


class TestDateTimeEncoder:
    def test_datetime_serialized_to_iso(self) -> None:
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = json.dumps({"ts": dt}, cls=DateTimeEncoder)
        assert result == '{"ts": "2024-01-15T10:30:00"}'

    def test_non_datetime_raises(self) -> None:
        with pytest.raises(TypeError):
            json.dumps({"value": object()}, cls=DateTimeEncoder)

    def test_nested_structure(self) -> None:
        data = {"outer": {"inner": datetime(2024, 6, 1)}}
        result = json.loads(json.dumps(data, cls=DateTimeEncoder))
        assert result == {"outer": {"inner": "2024-06-01T00:00:00"}}
