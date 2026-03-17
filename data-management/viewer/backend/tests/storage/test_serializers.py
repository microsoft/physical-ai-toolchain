"""Tests for shared serialization utilities."""

import json
import unittest
from datetime import datetime

from src.api.storage.serializers import DateTimeEncoder


class TestDateTimeEncoder(unittest.TestCase):
    def test_datetime_serialized_to_iso(self):
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = json.dumps({"ts": dt}, cls=DateTimeEncoder)
        self.assertEqual(result, '{"ts": "2024-01-15T10:30:00"}')

    def test_non_datetime_raises(self):
        with self.assertRaises(TypeError):
            json.dumps({"value": object()}, cls=DateTimeEncoder)

    def test_nested_structure(self):
        data = {"outer": {"inner": datetime(2024, 6, 1)}}
        result = json.loads(json.dumps(data, cls=DateTimeEncoder))
        self.assertEqual(result["outer"]["inner"], "2024-06-01T00:00:00")
