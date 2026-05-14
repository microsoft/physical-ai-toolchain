"""Unit tests for storage path helper."""

from __future__ import annotations

from src.api.storage.paths import dataset_id_to_blob_prefix


class TestDatasetIdToBlobPrefix:
    def test_no_separator_passthrough(self):
        assert dataset_id_to_blob_prefix("dataset") == "dataset"

    def test_single_separator_to_slash(self):
        assert dataset_id_to_blob_prefix("group--dataset") == "group/dataset"

    def test_multiple_separators_to_slashes(self):
        assert dataset_id_to_blob_prefix("a--b--c") == "a/b/c"

    def test_empty_string(self):
        assert dataset_id_to_blob_prefix("") == ""

    def test_leading_separator(self):
        assert dataset_id_to_blob_prefix("--leading") == "/leading"

    def test_trailing_separator(self):
        assert dataset_id_to_blob_prefix("trailing--") == "trailing/"

    def test_single_dash_unchanged(self):
        assert dataset_id_to_blob_prefix("a-b") == "a-b"
