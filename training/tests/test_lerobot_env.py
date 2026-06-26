"""Tests for training/il/scripts/lerobot/_env.py."""

from __future__ import annotations

import pytest
from conftest import load_training_module

_ENV = load_training_module(
    "training_il_scripts_lerobot_env",
    "training/il/scripts/lerobot/_env.py",
)


class TestParseUrlListEnv:
    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "[]",
            "[ ]",
            "[\n]",
            "[\n  \n]",
            '[""]',
            '[" "]',
            '["\t"]',
            "[null]",
            "[null, null]",
            "null",
            '{"a": 1}',
            "not-json",
            "[",
            '"single-string"',
            "42",
            "true",
        ],
    )
    def test_returns_empty_for_non_url_payloads(self, raw):
        assert _ENV.parse_url_list_env(raw) == []

    def test_parses_single_url(self):
        raw = '["https://acct.blob.core.windows.net/c/p"]'
        assert _ENV.parse_url_list_env(raw) == ["https://acct.blob.core.windows.net/c/p"]

    def test_parses_multiple_urls_in_order(self):
        raw = '["https://a.blob.core.windows.net/c1/p1", "https://b.blob.core.windows.net/c2/p2"]'
        assert _ENV.parse_url_list_env(raw) == [
            "https://a.blob.core.windows.net/c1/p1",
            "https://b.blob.core.windows.net/c2/p2",
        ]

    def test_tolerates_outer_whitespace(self):
        raw = '  ["https://a.blob.core.windows.net/c/p"]  '
        assert _ENV.parse_url_list_env(raw) == ["https://a.blob.core.windows.net/c/p"]

    def test_tolerates_pretty_printed_json(self):
        raw = '[\n  "https://a.blob.core.windows.net/c/p",\n  "https://b.blob.core.windows.net/c/p"\n]'
        assert _ENV.parse_url_list_env(raw) == [
            "https://a.blob.core.windows.net/c/p",
            "https://b.blob.core.windows.net/c/p",
        ]

    def test_strips_whitespace_from_each_entry(self):
        raw = '["  https://a.blob.core.windows.net/c/p  "]'
        assert _ENV.parse_url_list_env(raw) == ["https://a.blob.core.windows.net/c/p"]

    def test_filters_empty_and_whitespace_entries_keeping_valid(self):
        raw = '["", "https://a.blob.core.windows.net/c/p", "  "]'
        assert _ENV.parse_url_list_env(raw) == ["https://a.blob.core.windows.net/c/p"]

    def test_filters_non_string_entries(self):
        raw = '[1, "https://a.blob.core.windows.net/c/p", null, true, {}]'
        assert _ENV.parse_url_list_env(raw) == ["https://a.blob.core.windows.net/c/p"]


class TestHasBlobUrls:
    def test_explicit_argument_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("BLOB_URLS", '["https://a.blob.core.windows.net/c/p"]')
        assert _ENV.has_blob_urls("[]") is False
        assert _ENV.has_blob_urls("") is False

    def test_reads_blob_urls_env_when_no_argument(self, monkeypatch):
        monkeypatch.setenv("BLOB_URLS", '["https://a.blob.core.windows.net/c/p"]')
        assert _ENV.has_blob_urls() is True

    def test_returns_false_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("BLOB_URLS", raising=False)
        assert _ENV.has_blob_urls() is False

    def test_returns_false_for_env_with_only_empty_entries(self, monkeypatch):
        monkeypatch.setenv("BLOB_URLS", '[""]')
        assert _ENV.has_blob_urls() is False

    def test_returns_false_for_pretty_printed_empty_env(self, monkeypatch):
        monkeypatch.setenv("BLOB_URLS", "[\n]")
        assert _ENV.has_blob_urls() is False

    def test_returns_true_for_mixed_valid_and_empty(self, monkeypatch):
        monkeypatch.setenv("BLOB_URLS", '["", "https://a.blob.core.windows.net/c/p"]')
        assert _ENV.has_blob_urls() is True
