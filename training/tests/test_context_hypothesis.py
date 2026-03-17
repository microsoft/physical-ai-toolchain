"""Hypothesis property-based tests for Azure ML context utilities."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import types
from unittest.mock import Mock, patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

# === Mock-injection (same pattern as test_context.py) ===

_azure_module = types.ModuleType("azure")
_azure_ai_module = types.ModuleType("azure.ai")
_azure_ai_ml_module = types.ModuleType("azure.ai.ml")
_azure_identity_module = types.ModuleType("azure.identity")
_mlflow_module = types.ModuleType("mlflow")


class _PlaceholderMLClient:
    pass


class _PlaceholderDefaultAzureCredential:
    pass


_azure_ai_ml_module.MLClient = _PlaceholderMLClient
_azure_identity_module.DefaultAzureCredential = _PlaceholderDefaultAzureCredential
_mlflow_module.set_tracking_uri = lambda *a, **kw: None
_mlflow_module.set_experiment = lambda *a, **kw: None

_azure_module.ai = _azure_ai_module
_azure_module.identity = _azure_identity_module
_azure_ai_module.ml = _azure_ai_ml_module


def _import_context_module() -> types.ModuleType:
    deps = {
        "azure": _azure_module,
        "azure.ai": _azure_ai_module,
        "azure.ai.ml": _azure_ai_ml_module,
        "azure.identity": _azure_identity_module,
        "mlflow": _mlflow_module,
    }
    saved = {n: sys.modules.get(n) for n in deps}
    sys.modules.pop("training.utils.context", None)
    try:
        sys.modules.update(deps)
        return importlib.import_module("training.utils.context")
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev
        sys.modules.pop("training.utils.context", None)


_ctx = _import_context_module()

_ENV_KEY = "_HYPOTHESIS_CONTEXT_TEST_VAR"


# === _optional_env property tests ===


@given(value=st.text(min_size=1).filter(lambda s: "\x00" not in s))
def test_optional_env_returns_nonempty_value(value: str) -> None:
    """Non-empty env var values are returned unchanged."""
    os.environ[_ENV_KEY] = value
    try:
        assert _ctx._optional_env(_ENV_KEY) == value
    finally:
        del os.environ[_ENV_KEY]


@given(name=st.from_regex(r"_HYPOTHESIS_UNUSED_[A-Z]{1,10}", fullmatch=True))
def test_optional_env_returns_none_for_unset(name: str) -> None:
    """Unset env vars always return None."""
    os.environ.pop(name, None)
    assert _ctx._optional_env(name) is None


def test_optional_env_returns_none_for_empty_string() -> None:
    """Empty-string env var treated as unset."""
    os.environ[_ENV_KEY] = ""
    try:
        assert _ctx._optional_env(_ENV_KEY) is None
    finally:
        del os.environ[_ENV_KEY]


# === AzureStorageContext.upload_file property tests ===


@given(blob_name=st.from_regex(r"[a-z][a-z0-9/._-]{0,50}", fullmatch=True))
def test_upload_file_returns_blob_name(blob_name: str) -> None:
    """upload_file returns the exact blob_name argument when file exists."""
    fd, tmp_path = tempfile.mkstemp()
    try:
        os.write(fd, b"data")
        os.close(fd)
        blob_client = Mock()
        blob_client.get_blob_client.return_value = Mock()
        storage = _ctx.AzureStorageContext(blob_client=blob_client, container_name="c")
        assert storage.upload_file(local_path=tmp_path, blob_name=blob_name) == blob_name
    finally:
        os.unlink(tmp_path)


@given(path_suffix=st.from_regex(r"[a-z]{1,10}\.[a-z]{1,4}", fullmatch=True))
def test_upload_file_raises_for_missing_path(path_suffix: str) -> None:
    """Non-existent paths always raise FileNotFoundError."""
    storage = _ctx.AzureStorageContext(blob_client=Mock(), container_name="c")
    missing = os.path.join(tempfile.gettempdir(), f"nonexistent_{path_suffix}")
    with pytest.raises(FileNotFoundError):
        storage.upload_file(local_path=missing, blob_name="b")


# === AzureStorageContext.upload_checkpoint property tests ===


@given(
    model_name=st.from_regex(r"[a-z][a-z0-9-]{0,20}", fullmatch=True),
    step=st.one_of(st.none(), st.integers(min_value=0, max_value=1_000_000)),
    extension=st.sampled_from([".pt", ".pth", ".ckpt", ".bin", ""]),
)
def test_upload_checkpoint_blob_name_format(model_name: str, step: int | None, extension: str) -> None:
    """Blob name has correct prefix, extension, and optional step segment."""
    fd, tmp_path = tempfile.mkstemp(suffix=extension)
    try:
        os.write(fd, b"model")
        os.close(fd)
        captured: dict[str, str] = {}

        def mock_upload(self, *, local_path, blob_name):
            captured["blob_name"] = blob_name
            return blob_name

        with patch.object(_ctx.AzureStorageContext, "upload_file", mock_upload):
            storage = _ctx.AzureStorageContext(blob_client=Mock(), container_name="c")
            result = storage.upload_checkpoint(local_path=tmp_path, model_name=model_name, step=step)

        blob = captured["blob_name"]
        assert blob.startswith(f"checkpoints/{model_name}/")
        assert blob.endswith(extension)
        if step is not None:
            assert f"_step_{step}" in blob
        else:
            assert "_step_" not in blob
        assert result == blob
    finally:
        os.unlink(tmp_path)


# === AzureStorageContext.upload_files_batch property tests ===


def test_upload_files_batch_empty_returns_empty() -> None:
    """Empty file list returns empty result."""
    storage = _ctx.AzureStorageContext(blob_client=Mock(), container_name="c")
    assert storage.upload_files_batch([]) == []


@given(count=st.integers(min_value=1, max_value=5))
def test_upload_files_batch_all_succeed(count: int) -> None:
    """When all files exist, all blob names appear in result."""
    with tempfile.TemporaryDirectory() as tmpdir:
        files = []
        for i in range(count):
            path = os.path.join(tmpdir, f"file_{i}.bin")
            with open(path, "wb") as f:
                f.write(b"data")
            files.append((path, f"blob_{i}.bin"))

        blob_client = Mock()
        blob_client.get_blob_client.return_value = Mock()
        storage = _ctx.AzureStorageContext(blob_client=blob_client, container_name="c")
        result = storage.upload_files_batch(files)

        expected = {f"blob_{i}.bin" for i in range(count)}
        assert set(result) == expected
