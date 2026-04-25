"""
Unit tests for Azure Blob Storage adapter.

These tests use mocking to avoid requiring actual Azure credentials.
"""

import asyncio
import json
import unittest.mock
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import create_test_annotation


class TestAzureBlobStorageAdapter(TestCase):
    """Tests for AzureBlobStorageAdapter."""

    def setUp(self):
        """Set up test fixtures."""
        self.dataset_id = "test-dataset"

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_get_annotation_not_found(self, mock_blob_service):
        """Test getting a non-existent annotation returns None."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        # Create a stand-in exception and patch it into the module
        _ResourceNotFoundError = type("ResourceNotFoundError", (Exception,), {})

        # Set up mock to raise ResourceNotFoundError
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock(side_effect=_ResourceNotFoundError("Not found"))

        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas-token",
        )
        adapter._client = mock_client

        with patch("src.api.storage.azure.ResourceNotFoundError", _ResourceNotFoundError):
            result = asyncio.run(adapter.get_annotation(self.dataset_id, 0))
        assert result is None

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_get_annotation_success(self, mock_blob_service):
        """Test successfully retrieving an annotation."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        # Create test annotation data
        annotation = create_test_annotation(episode_index=5)
        annotation_json = json.dumps(annotation.model_dump(mode="json"))

        # Set up mock
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()

        mock_download = AsyncMock()
        mock_download.readall = AsyncMock(return_value=annotation_json.encode("utf-8"))
        mock_blob.download_blob = AsyncMock(return_value=mock_download)

        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas-token",
        )
        adapter._client = mock_client

        result = asyncio.run(adapter.get_annotation(self.dataset_id, 5))

        assert result is not None
        assert result.episode_index == 5

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.ContentSettings")
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_save_annotation(self, mock_blob_service, mock_content_settings):
        """Test saving an annotation."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        # Set up mock
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.upload_blob = AsyncMock()

        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas-token",
        )
        adapter._client = mock_client

        annotation = create_test_annotation(episode_index=5)
        asyncio.run(adapter.save_annotation(self.dataset_id, 5, annotation))

        # Verify upload was called
        mock_blob.upload_blob.assert_called_once()
        call_args = mock_blob.upload_blob.call_args
        assert call_args[1]["overwrite"] is True
        mock_content_settings.assert_called_once_with(content_type="application/json")
        assert call_args[1]["content_settings"] == mock_content_settings.return_value

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_list_annotated_episodes(self, mock_blob_service):
        """Test listing annotated episodes."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        # Create mock blob list
        mock_blob_1 = MagicMock()
        mock_blob_1.name = "test-dataset/annotations/episodes/episode_000003.json"
        mock_blob_2 = MagicMock()
        mock_blob_2.name = "test-dataset/annotations/episodes/episode_000001.json"
        mock_blob_3 = MagicMock()
        mock_blob_3.name = "test-dataset/annotations/episodes/episode_000005.json"
        mock_blobs = [mock_blob_1, mock_blob_2, mock_blob_3]

        # Set up mock
        mock_client = MagicMock()
        mock_container = MagicMock()

        async def mock_list_blobs(name_starts_with):
            for blob in mock_blobs:
                yield blob

        mock_container.list_blobs = mock_list_blobs
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas-token",
        )
        adapter._client = mock_client

        result = asyncio.run(adapter.list_annotated_episodes(self.dataset_id))

        assert result == [1, 3, 5]

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_delete_annotation_success(self, mock_blob_service):
        """Test deleting an existing annotation."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        # Set up mock
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.delete_blob = AsyncMock()

        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas-token",
        )
        adapter._client = mock_client

        result = asyncio.run(adapter.delete_annotation(self.dataset_id, 5))

        assert result is True
        mock_blob.delete_blob.assert_called_once()

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_delete_annotation_not_found(self, mock_blob_service):
        """Test deleting a non-existent annotation returns False."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        _ResourceNotFoundError = type("ResourceNotFoundError", (Exception,), {})

        # Set up mock
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.delete_blob = AsyncMock(side_effect=_ResourceNotFoundError("Not found"))

        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas-token",
        )
        adapter._client = mock_client

        with patch("src.api.storage.azure.ResourceNotFoundError", _ResourceNotFoundError):
            result = asyncio.run(adapter.delete_annotation(self.dataset_id, 5))

        assert result is False

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    def test_requires_auth_method(self):
        """Test that adapter requires SAS token or managed identity."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        with pytest.raises(ValueError, match="sas_token or use_managed_identity"):
            AzureBlobStorageAdapter(
                account_name="testaccount",
                container_name="testcontainer",
            )

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    def test_blob_path_format(self):
        """Test blob path formatting."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas",
        )

        path = adapter._get_blob_path("my-dataset", 42)
        assert path == "my-dataset/annotations/episodes/episode_000042.json"

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    def test_get_client_uses_sas_token_when_provided(self):
        """Verify BlobServiceClient uses the SAS token credential."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas",
        )
        with unittest.mock.patch("src.api.storage.azure.BlobServiceClient") as mock_cls:
            asyncio.run(adapter._get_client())
            mock_cls.assert_called_once_with(
                account_url="https://testaccount.blob.core.windows.net",
                credential="test-sas",
            )

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    def test_get_client_reuses_cached_client(self):
        """Verify the client is only created once and then cached."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas",
        )
        with unittest.mock.patch("src.api.storage.azure.BlobServiceClient") as mock_cls:
            first_client = asyncio.run(adapter._get_client())
            second_client = asyncio.run(adapter._get_client())

            assert first_client is second_client
            mock_cls.assert_called_once()


class TestAzureBlobStorageAdapterErrorPaths(TestCase):
    """Branch and error-path coverage for AzureBlobStorageAdapter."""

    def setUp(self):
        self.dataset_id = "test-dataset"

    def _make_http_error(self, status_code: int = 500, error_code: str = "ServerError") -> Exception:
        err = Exception("http error")
        err.status_code = status_code
        err.error_code = error_code
        return err

    @patch("src.api.storage.azure.AZURE_AVAILABLE", False)
    def test_init_raises_import_error_when_sdk_unavailable(self):
        from src.api.storage.azure import AzureBlobStorageAdapter

        with pytest.raises(ImportError, match="azure-storage-blob"):
            AzureBlobStorageAdapter(
                account_name="testaccount",
                container_name="testcontainer",
                sas_token="test-sas",
            )

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.DefaultAzureCredential")
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_get_client_uses_managed_identity(self, mock_blob_service, mock_credential):
        from src.api.storage.azure import AzureBlobStorageAdapter

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            use_managed_identity=True,
        )
        asyncio.run(adapter._get_client())
        mock_credential.assert_called_once_with()
        mock_blob_service.assert_called_once_with(
            account_url="https://testaccount.blob.core.windows.net",
            credential=mock_credential.return_value,
        )

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_get_annotation_invalid_json_raises_storage_error(self, mock_blob_service):
        from src.api.storage.azure import AzureBlobStorageAdapter, StorageError

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_download = AsyncMock()
        mock_download.readall = AsyncMock(return_value=b"{not json")
        mock_blob.download_blob = AsyncMock(return_value=mock_download)
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(
            account_name="a", container_name="c", sas_token="s",
        )
        adapter._client = mock_client

        with pytest.raises(StorageError, match="Invalid JSON"):
            asyncio.run(adapter.get_annotation(self.dataset_id, 0))

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_get_annotation_http_error_raises_storage_error(self, mock_blob_service):
        from src.api.storage.azure import AzureBlobStorageAdapter, StorageError

        _HttpError = type("HttpResponseError", (Exception,), {})
        err = _HttpError("boom")
        err.status_code = 503
        err.error_code = "ServiceUnavailable"

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock(side_effect=err)
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        adapter._client = mock_client

        with patch("src.api.storage.azure.HttpResponseError", _HttpError):
            with pytest.raises(StorageError, match="status=503"):
                asyncio.run(adapter.get_annotation(self.dataset_id, 0))

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_get_annotation_unexpected_error_wraps_storage_error(self, mock_blob_service):
        from src.api.storage.azure import AzureBlobStorageAdapter, StorageError

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock(side_effect=RuntimeError("kaboom"))
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        adapter._client = mock_client

        with pytest.raises(StorageError, match="Failed to read blob"):
            asyncio.run(adapter.get_annotation(self.dataset_id, 0))

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.ContentSettings")
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_save_annotation_http_error_raises_storage_error(self, mock_blob_service, mock_content_settings):
        from src.api.storage.azure import AzureBlobStorageAdapter, StorageError

        _HttpError = type("HttpResponseError", (Exception,), {})
        err = _HttpError("boom")
        err.status_code = 409
        err.error_code = "Conflict"

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.upload_blob = AsyncMock(side_effect=err)
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        adapter._client = mock_client
        annotation = create_test_annotation(episode_index=1)

        with patch("src.api.storage.azure.HttpResponseError", _HttpError):
            with pytest.raises(StorageError, match="status=409"):
                asyncio.run(adapter.save_annotation(self.dataset_id, 1, annotation))

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.ContentSettings")
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_save_annotation_unexpected_error_wraps_storage_error(self, mock_blob_service, mock_content_settings):
        from src.api.storage.azure import AzureBlobStorageAdapter, StorageError

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.upload_blob = AsyncMock(side_effect=RuntimeError("disk full"))
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        adapter._client = mock_client
        annotation = create_test_annotation(episode_index=1)

        with pytest.raises(StorageError, match="Failed to save blob"):
            asyncio.run(adapter.save_annotation(self.dataset_id, 1, annotation))

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_list_annotated_episodes_skips_invalid_filename(self, mock_blob_service):
        from src.api.storage.azure import AzureBlobStorageAdapter

        bad = MagicMock()
        bad.name = "test-dataset/annotations/episodes/episode_NOTANUM.json"
        good = MagicMock()
        good.name = "test-dataset/annotations/episodes/episode_000007.json"

        mock_client = MagicMock()
        mock_container = MagicMock()

        async def mock_list_blobs(name_starts_with):
            for blob in [bad, good]:
                yield blob

        mock_container.list_blobs = mock_list_blobs
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        adapter._client = mock_client

        result = asyncio.run(adapter.list_annotated_episodes(self.dataset_id))
        assert result == [7]

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_list_annotated_episodes_http_error_raises_storage_error(self, mock_blob_service):
        from src.api.storage.azure import AzureBlobStorageAdapter, StorageError

        _HttpError = type("HttpResponseError", (Exception,), {})
        err = _HttpError("boom")
        err.status_code = 500
        err.error_code = "ServerError"

        mock_client = MagicMock()
        mock_container = MagicMock()

        async def mock_list_blobs(name_starts_with):
            raise err
            yield  # pragma: no cover

        mock_container.list_blobs = mock_list_blobs
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        adapter._client = mock_client

        with patch("src.api.storage.azure.HttpResponseError", _HttpError):
            with pytest.raises(StorageError, match="status=500"):
                asyncio.run(adapter.list_annotated_episodes(self.dataset_id))

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_list_annotated_episodes_unexpected_error_wraps_storage_error(self, mock_blob_service):
        from src.api.storage.azure import AzureBlobStorageAdapter, StorageError

        mock_client = MagicMock()
        mock_container = MagicMock()

        async def mock_list_blobs(name_starts_with):
            raise RuntimeError("network down")
            yield  # pragma: no cover

        mock_container.list_blobs = mock_list_blobs
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        adapter._client = mock_client

        with pytest.raises(StorageError, match="Failed to list annotations"):
            asyncio.run(adapter.list_annotated_episodes(self.dataset_id))

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_delete_annotation_http_error_raises_storage_error(self, mock_blob_service):
        from src.api.storage.azure import AzureBlobStorageAdapter, StorageError

        _HttpError = type("HttpResponseError", (Exception,), {})
        err = _HttpError("boom")
        err.status_code = 403
        err.error_code = "Forbidden"

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.delete_blob = AsyncMock(side_effect=err)
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        adapter._client = mock_client

        with patch("src.api.storage.azure.HttpResponseError", _HttpError):
            with pytest.raises(StorageError, match="status=403"):
                asyncio.run(adapter.delete_annotation(self.dataset_id, 0))

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    def test_delete_annotation_unexpected_error_wraps_storage_error(self, mock_blob_service):
        from src.api.storage.azure import AzureBlobStorageAdapter, StorageError

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.delete_blob = AsyncMock(side_effect=RuntimeError("kaboom"))
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        adapter._client = mock_client

        with pytest.raises(StorageError, match="Failed to delete blob"):
            asyncio.run(adapter.delete_annotation(self.dataset_id, 0))

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    def test_close_releases_client(self):
        from src.api.storage.azure import AzureBlobStorageAdapter

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        adapter._client = mock_client

        asyncio.run(adapter.close())

        mock_client.close.assert_called_once()
        assert adapter._client is None

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    def test_close_when_client_never_created_is_noop(self):
        from src.api.storage.azure import AzureBlobStorageAdapter

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        # Should not raise even though _client is None
        asyncio.run(adapter.close())
        assert adapter._client is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
