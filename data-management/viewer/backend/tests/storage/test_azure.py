"""
Unit tests for Azure Blob Storage adapter.

These tests use mocking to avoid requiring actual Azure credentials.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import create_test_annotation


class TestAzureBlobStorageAdapter:
    """Tests for AzureBlobStorageAdapter."""

    @pytest.fixture(autouse=True)
    def _set_dataset_id(self, dataset_id: str) -> None:
        self.dataset_id = dataset_id

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_get_annotation_not_found(self, mock_blob_service):
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
            result = await adapter.get_annotation(self.dataset_id, 0)
        assert result is None

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_get_annotation_success(self, mock_blob_service):
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

        result = await adapter.get_annotation(self.dataset_id, 5)

        assert result == annotation
        mock_container.get_blob_client.assert_called_once_with("test-dataset/annotations/episodes/episode_000005.json")
        mock_blob.download_blob.assert_awaited_once_with()
        mock_download.readall.assert_awaited_once_with()

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.ContentSettings")
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_save_annotation(self, mock_blob_service, mock_content_settings):
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
        await adapter.save_annotation(self.dataset_id, 5, annotation)

        expected_payload = json.dumps(annotation.model_dump(mode="json"), indent=2).encode()
        mock_container.get_blob_client.assert_called_once_with("test-dataset/annotations/episodes/episode_000005.json")
        mock_blob.upload_blob.assert_awaited_once_with(
            expected_payload,
            overwrite=True,
            content_settings=mock_content_settings.return_value,
        )
        mock_content_settings.assert_called_once_with(content_type="application/json")

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.MatchConditions")
    @patch("src.api.storage.azure.ContentSettings")
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_save_annotation_uses_matching_etag(
        self,
        mock_blob_service,
        mock_content_settings,
        mock_match_conditions,
    ):
        from src.api.storage.azure import AzureBlobStorageAdapter

        mock_client = MagicMock()
        mock_blob = MagicMock()
        mock_blob.upload_blob = AsyncMock(return_value={"etag": '"revision-two"'})
        mock_client.get_container_client.return_value.get_blob_client.return_value = mock_blob
        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas-token",
        )
        adapter._client = mock_client

        etag = await adapter.save_annotation(
            self.dataset_id,
            5,
            create_test_annotation(episode_index=5),
            if_match='"revision-one"',
        )

        assert etag == '"revision-two"'
        kwargs = mock_blob.upload_blob.await_args.kwargs
        assert kwargs["etag"] == '"revision-one"'
        assert kwargs["match_condition"] == mock_match_conditions.IfNotModified
        assert kwargs["overwrite"] is True

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.ContentSettings")
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_save_annotation_uses_create_only_condition(self, mock_blob_service, mock_content_settings):
        from src.api.storage.azure import AzureBlobStorageAdapter

        mock_client = MagicMock()
        mock_blob = MagicMock()
        mock_blob.upload_blob = AsyncMock(return_value={"etag": '"created"'})
        mock_client.get_container_client.return_value.get_blob_client.return_value = mock_blob
        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas-token",
        )
        adapter._client = mock_client

        await adapter.save_annotation(
            self.dataset_id,
            5,
            create_test_annotation(episode_index=5),
            if_none_match=True,
        )

        kwargs = mock_blob.upload_blob.await_args.kwargs
        assert kwargs["if_none_match"] == "*"
        assert kwargs["overwrite"] is False

    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_list_annotated_episodes(self, mock_blob_service):
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

        result = await adapter.list_annotated_episodes(self.dataset_id)

        assert result == [1, 3, 5]

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_delete_annotation_success(self, mock_blob_service):
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

        result = await adapter.delete_annotation(self.dataset_id, 5)

        assert result is True
        mock_blob.delete_blob.assert_awaited_once_with()

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_delete_annotation_not_found(self, mock_blob_service):
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
            result = await adapter.delete_annotation(self.dataset_id, 5)

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

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    async def test_delete_uses_sas_authenticated_client(self) -> None:
        """Verify public operations construct a SAS-authenticated client."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas",
        )
        with patch("src.api.storage.azure.BlobServiceClient") as mock_cls:
            mock_blob = mock_cls.return_value.get_container_client.return_value.get_blob_client.return_value
            mock_blob.delete_blob = AsyncMock()

            assert await adapter.delete_annotation("my-dataset", 42) is True

            mock_cls.assert_called_once_with(
                account_url="https://testaccount.blob.core.windows.net",
                credential="test-sas",
            )
            mock_cls.return_value.get_container_client.assert_called_once_with("testcontainer")
            mock_cls.return_value.get_container_client.return_value.get_blob_client.assert_called_once_with(
                "my-dataset/annotations/episodes/episode_000042.json"
            )

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    async def test_public_operations_reuse_cached_client(self) -> None:
        """Verify consecutive public operations reuse the client."""
        from src.api.storage.azure import AzureBlobStorageAdapter

        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            sas_token="test-sas",
        )
        with patch("src.api.storage.azure.BlobServiceClient") as mock_cls:
            mock_blob = mock_cls.return_value.get_container_client.return_value.get_blob_client.return_value
            mock_blob.delete_blob = AsyncMock()

            assert await adapter.delete_annotation("dataset", 1) is True
            assert await adapter.delete_annotation("dataset", 2) is True

            mock_cls.assert_called_once_with(
                account_url="https://testaccount.blob.core.windows.net",
                credential="test-sas",
            )


class TestAzureBlobStorageAdapterErrorPaths:
    """Branch and error-path coverage for AzureBlobStorageAdapter."""

    @pytest.fixture(autouse=True)
    def _set_dataset_id(self, dataset_id: str) -> None:
        self.dataset_id = dataset_id

    @patch("src.api.storage.azure.AZURE_AVAILABLE", False)
    def test_init_raises_import_error_when_sdk_unavailable(self):
        from src.api.storage.azure import AzureBlobStorageAdapter

        with pytest.raises(ImportError, match="azure-storage-blob"):
            AzureBlobStorageAdapter(
                account_name="testaccount",
                container_name="testcontainer",
                sas_token="test-sas",
            )

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.DefaultAzureCredential")
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_delete_uses_managed_identity(self, mock_blob_service, mock_credential):
        from src.api.storage.azure import AzureBlobStorageAdapter

        mock_blob = mock_blob_service.return_value.get_container_client.return_value.get_blob_client.return_value
        mock_blob.delete_blob = AsyncMock()
        adapter = AzureBlobStorageAdapter(
            account_name="testaccount",
            container_name="testcontainer",
            use_managed_identity=True,
        )

        assert await adapter.delete_annotation("dataset", 1) is True

        mock_credential.assert_called_once_with()
        mock_blob_service.assert_called_once_with(
            account_url="https://testaccount.blob.core.windows.net",
            credential=mock_credential.return_value,
        )

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_get_annotation_invalid_json_raises_storage_error(self, mock_blob_service):
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
            account_name="a",
            container_name="c",
            sas_token="s",
        )
        adapter._client = mock_client

        with pytest.raises(StorageError, match="Invalid JSON"):
            await adapter.get_annotation(self.dataset_id, 0)

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_get_annotation_http_error_raises_storage_error(self, mock_blob_service):
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

        with (
            patch("src.api.storage.azure.HttpResponseError", _HttpError),
            pytest.raises(StorageError, match="status=503"),
        ):
            await adapter.get_annotation(self.dataset_id, 0)

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_get_annotation_unexpected_error_wraps_storage_error(self, mock_blob_service):
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
            await adapter.get_annotation(self.dataset_id, 0)

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.ContentSettings")
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_save_annotation_http_error_raises_storage_error(self, mock_blob_service, mock_content_settings):
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

        with (
            patch("src.api.storage.azure.HttpResponseError", _HttpError),
            pytest.raises(StorageError, match="status=409"),
        ):
            await adapter.save_annotation(self.dataset_id, 1, annotation)

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.ContentSettings")
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_save_annotation_unexpected_error_wraps_storage_error(self, mock_blob_service, mock_content_settings):
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
            await adapter.save_annotation(self.dataset_id, 1, annotation)

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_list_annotated_episodes_skips_invalid_filename(self, mock_blob_service):
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

        result = await adapter.list_annotated_episodes(self.dataset_id)
        assert result == [7]

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_list_annotated_episodes_http_error_raises_storage_error(self, mock_blob_service):
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

        with (
            patch("src.api.storage.azure.HttpResponseError", _HttpError),
            pytest.raises(StorageError, match="status=500"),
        ):
            await adapter.list_annotated_episodes(self.dataset_id)

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_list_annotated_episodes_unexpected_error_wraps_storage_error(self, mock_blob_service):
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
            await adapter.list_annotated_episodes(self.dataset_id)

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_delete_annotation_http_error_raises_storage_error(self, mock_blob_service):
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

        with (
            patch("src.api.storage.azure.HttpResponseError", _HttpError),
            pytest.raises(StorageError, match="status=403"),
        ):
            await adapter.delete_annotation(self.dataset_id, 0)

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    @patch("src.api.storage.azure.BlobServiceClient")
    async def test_delete_annotation_unexpected_error_wraps_storage_error(self, mock_blob_service):
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
            await adapter.delete_annotation(self.dataset_id, 0)

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    async def test_close_releases_client(self):
        from src.api.storage.azure import AzureBlobStorageAdapter

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        adapter._client = mock_client

        await adapter.close()

        mock_client.close.assert_awaited_once_with()
        assert adapter._client is None

    @pytest.mark.asyncio
    @patch("src.api.storage.azure.AZURE_AVAILABLE", True)
    async def test_close_when_client_never_created_is_noop(self):
        from src.api.storage.azure import AzureBlobStorageAdapter

        adapter = AzureBlobStorageAdapter(account_name="a", container_name="c", sas_token="s")
        # Should not raise even though _client is None
        await adapter.close()
        assert adapter._client is None
