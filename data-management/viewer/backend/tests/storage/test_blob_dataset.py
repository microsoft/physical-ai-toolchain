"""Unit tests for the BlobDatasetProvider Azure Blob Storage adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import create_blob_dataset_provider


class _AsyncIter:
    """Minimal async iterator over an in-memory sequence."""

    def __init__(self, items: Iterable[object]) -> None:
        self._items = list(items)

    def __aiter__(self) -> AsyncIterator[object]:
        return self

    async def __anext__(self) -> object:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _make_blob(name: str) -> MagicMock:
    blob = MagicMock()
    blob.name = name
    return blob


def _make_directory_blob(name: str) -> MagicMock:
    blob = _make_blob(name)
    blob.metadata = {"hdi_isfolder": "true"}
    return blob


class TestImportGuard:
    """Module-level Azure availability guard."""

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", False)
    def test_init_raises_when_azure_unavailable(self):
        from src.api.storage.blob_dataset import BlobDatasetProvider

        with pytest.raises(ImportError, match="BlobDatasetProvider requires"):
            BlobDatasetProvider(account_name="a", container_name="c")


class TestPrefixHelpers:
    """Static helpers and prefix mapping."""

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_blob_prefix_replaces_double_dash(self):
        from src.api.storage.blob_dataset import BlobDatasetProvider

        assert BlobDatasetProvider.get_blob_prefix("org--repo") == "org/repo"
        assert BlobDatasetProvider.get_blob_prefix("a--b--c") == "a/b/c"


class TestGetClient:
    """Client construction and caching."""

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    @patch("src.api.storage.blob_dataset.BlobServiceClient")
    async def test_dataset_exists_uses_sas_when_provided(self, mock_blob_service: MagicMock) -> None:
        provider = create_blob_dataset_provider()
        mock_blob = mock_blob_service.return_value.get_container_client.return_value.get_blob_client.return_value
        mock_blob.get_blob_properties = AsyncMock()

        assert await provider.dataset_exists("org--repo") is True

        mock_blob_service.assert_called_once_with(
            account_url="https://testaccount.blob.core.windows.net",
            credential="sas-token",
        )
        assert await provider.dataset_exists("org--repo") is True
        assert mock_blob_service.call_count == 1

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    @patch("src.api.storage.blob_dataset.AsyncDefaultAzureCredential")
    @patch("src.api.storage.blob_dataset.BlobServiceClient")
    async def test_dataset_exists_uses_default_credential_without_sas(
        self,
        mock_blob_service: MagicMock,
        mock_credential_cls: MagicMock,
    ) -> None:
        from src.api.storage.blob_dataset import BlobDatasetProvider

        provider = BlobDatasetProvider(account_name="testaccount", container_name="testcontainer")
        cred_instance = MagicMock()
        mock_credential_cls.return_value = cred_instance
        mock_blob = mock_blob_service.return_value.get_container_client.return_value.get_blob_client.return_value
        mock_blob.get_blob_properties = AsyncMock()

        assert await provider.dataset_exists("org--repo") is True

        mock_credential_cls.assert_called_once_with()
        mock_blob_service.assert_called_once_with(
            account_url="https://testaccount.blob.core.windows.net",
            credential=cred_instance,
        )


class TestReadBlobBytes:
    """Blob-backed metadata reads."""

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_info_json_reads_blob_bytes(self) -> None:
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_download = AsyncMock()
        mock_download.readall = AsyncMock(return_value=b'{"value": 7}')
        mock_blob.download_blob = AsyncMock(return_value=mock_download)
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        provider = create_blob_dataset_provider(mock_client)
        result = await provider.get_info_json("org--repo")

        assert result == {"value": 7}
        mock_container.get_blob_client.assert_called_once_with("org/repo/meta/info.json")
        mock_blob.download_blob.assert_awaited_once_with()
        mock_download.readall.assert_awaited_once_with()

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_info_json_returns_none_on_not_found(self) -> None:
        _NotFound = type("ResourceNotFoundError", (Exception,), {})
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock(side_effect=_NotFound("missing"))
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        provider = create_blob_dataset_provider(mock_client)
        with patch("src.api.storage.blob_dataset.ResourceNotFoundError", _NotFound):
            assert await provider.get_info_json("org--repo") is None

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_info_json_returns_none_on_download_error(self) -> None:
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock(side_effect=RuntimeError("boom"))
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        provider = create_blob_dataset_provider(mock_client)
        assert await provider.get_info_json("org--repo") is None


class TestScanAllDatasetIds:
    """Container scan classifying LeRobot vs HDF5 datasets."""

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_scan_classifies_and_dedupes(self):
        _NotFound = type("ResourceNotFoundError", (Exception,), {})
        # Virtual directory tree (delimiter='/') with info.json markers.
        info_json_prefixes = {"org1/repo1/", "org2/repo2/"}
        children = {
            "": ["org1/", "org2/", "team/"],
            "org1/": ["org1/repo1/"],
            "org2/": ["org2/repo2/"],
            "team/": ["team/projectA/"],
        }
        hdf5_files = {"team/projectA/": ["team/projectA/episode_0.hdf5"]}

        def walk_blobs(name_starts_with="", delimiter="/", **kwargs):
            items = [_make_blob(c) for c in children.get(name_starts_with, [])]
            items.extend(_make_blob(f) for f in hdf5_files.get(name_starts_with, []))
            return _AsyncIter(items)

        def get_blob_client(path):
            blob = MagicMock()
            marker = "meta/info.json"
            if path.endswith(marker) and path[: -len(marker)] in info_json_prefixes:
                blob.get_blob_properties = AsyncMock(return_value=MagicMock())
            else:
                blob.get_blob_properties = AsyncMock(side_effect=_NotFound("missing"))
            return blob

        mock_container = MagicMock()
        mock_container.walk_blobs.side_effect = walk_blobs
        mock_container.get_blob_client.side_effect = get_blob_client
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = create_blob_dataset_provider(mock_client)
        with patch("src.api.storage.blob_dataset.ResourceNotFoundError", _NotFound):
            result = await provider.scan_all_dataset_ids()

        assert result["lerobot"] == ["org1--repo1", "org2--repo2"]
        assert result["hdf5"] == ["team--projectA"]

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_scan_swallows_outer_exception(self):
        mock_client = MagicMock()
        mock_client.get_container_client.side_effect = RuntimeError("network")

        provider = create_blob_dataset_provider(mock_client)
        result = await provider.scan_all_dataset_ids()
        assert result == {"lerobot": [], "hdf5": []}

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_list_dataset_ids_delegates_to_scan(self):
        provider = create_blob_dataset_provider(MagicMock())
        with patch.object(
            type(provider),
            "scan_all_dataset_ids",
            new=AsyncMock(return_value={"lerobot": ["a"], "hdf5": ["b"]}),
        ):
            assert await provider.list_dataset_ids() == ["a"]
            assert await provider.list_hdf5_dataset_ids() == ["b"]


class TestDatasetExists:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_dataset_exists_true(self):
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(return_value=MagicMock())
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = create_blob_dataset_provider(mock_client)
        assert await provider.dataset_exists("org--repo") is True
        mock_container.get_blob_client.assert_called_once_with("org/repo/meta/info.json")

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_dataset_exists_not_found(self):
        _NotFound = type("ResourceNotFoundError", (Exception,), {})
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(side_effect=_NotFound("nope"))
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = create_blob_dataset_provider(mock_client)
        with patch("src.api.storage.blob_dataset.ResourceNotFoundError", _NotFound):
            assert await provider.dataset_exists("org--repo") is False

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_dataset_exists_false_on_generic_error(self):
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(side_effect=RuntimeError("boom"))
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = create_blob_dataset_provider(mock_client)
        assert await provider.dataset_exists("org--repo") is False


class TestGetInfoJson:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_info_json_returns_parsed_and_caches(self):
        provider = create_blob_dataset_provider(MagicMock())
        payload = {"chunks_size": 1000}
        with patch.object(
            type(provider),
            "_read_blob_bytes",
            new=AsyncMock(return_value=json.dumps(payload).encode("utf-8")),
        ) as read_mock:
            assert await provider.get_info_json("org--repo") == payload
            # Second call hits cache; _read_blob_bytes not invoked again.
            assert await provider.get_info_json("org--repo") == payload
            assert read_mock.call_count == 1

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_info_json_returns_none_when_missing(self):
        provider = create_blob_dataset_provider(MagicMock())
        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=None)):
            assert await provider.get_info_json("org--repo") is None

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_info_json_returns_none_on_invalid_json(self):
        provider = create_blob_dataset_provider(MagicMock())
        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=b"not-json")):
            assert await provider.get_info_json("org--repo") is None


class TestGetBlobProperties:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_blob_properties_success(self):
        props = MagicMock()
        props.size = 42
        props.content_settings.content_type = "video/mp4"
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(return_value=props)
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = create_blob_dataset_provider(mock_client)
        result = await provider.get_blob_properties("path/to/blob")
        assert result == {"size": 42, "content_type": "video/mp4"}

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_blob_properties_default_content_type(self):
        props = MagicMock()
        props.size = 7
        props.content_settings.content_type = None
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(return_value=props)
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = create_blob_dataset_provider(mock_client)
        result = await provider.get_blob_properties("p")
        assert result == {"size": 7, "content_type": "application/octet-stream"}

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_blob_properties_not_found(self):
        _NotFound = type("ResourceNotFoundError", (Exception,), {})
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(side_effect=_NotFound())
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = create_blob_dataset_provider(mock_client)
        with patch("src.api.storage.blob_dataset.ResourceNotFoundError", _NotFound):
            assert await provider.get_blob_properties("p") is None

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_blob_properties_returns_none_on_error(self):
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(side_effect=RuntimeError("boom"))
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = create_blob_dataset_provider(mock_client)
        assert await provider.get_blob_properties("p") is None


class TestVideoPathCandidates:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_resolve_uses_template_layout(self) -> None:
        provider = create_blob_dataset_provider(MagicMock())
        info = {
            "chunks_size": 10,
            "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        }
        get_properties = AsyncMock(side_effect=[None, {"size": 1, "content_type": "video/mp4"}])
        with (
            patch.object(type(provider), "get_info_json", new=AsyncMock(return_value=info)),
            patch.object(type(provider), "get_blob_properties", new=get_properties),
            patch.object(type(provider), "_get_episode_video_entry", new=AsyncMock(return_value=None)),
        ):
            result = await provider.resolve_video_blob_path("p", 23, "cam0")

        assert result == "p/videos/cam0/chunk-002/file-003.mp4"
        assert [call.args[0] for call in get_properties.await_args_list] == [
            "p/videos/cam0/chunk-023/file-000.mp4",
            "p/videos/cam0/chunk-002/file-003.mp4",
        ]


class TestResolveVideoBlobPath:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_resolve_returns_first_existing_candidate(self):
        provider = create_blob_dataset_provider(MagicMock())
        with (
            patch.object(type(provider), "get_info_json", new=AsyncMock(return_value=None)),
            patch.object(
                type(provider),
                "get_blob_properties",
                new=AsyncMock(side_effect=[None, {"size": 1, "content_type": "video/mp4"}]),
            ),
        ):
            result = await provider.resolve_video_blob_path("org--repo", 5, "cam0")
            assert result == "org/repo/videos/cam0/chunk-000/file-005.mp4"

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_resolve_falls_back_to_scan(self):
        names = [
            "org/repo/videos/cam0/chunk-001/file-005.mp4",
        ]
        mock_container = MagicMock()

        def list_blobs(name_starts_with="", **kwargs):
            return _AsyncIter(_make_blob(n) for n in names if n.startswith(name_starts_with))

        mock_container.list_blobs.side_effect = list_blobs
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        with (
            patch.object(type(provider), "get_info_json", new=AsyncMock(return_value=None)),
            patch.object(
                type(provider),
                "_get_episode_video_entry",
                new=AsyncMock(return_value=None),
            ),
            patch.object(type(provider), "get_blob_properties", new=AsyncMock(return_value=None)),
        ):
            result = await provider.resolve_video_blob_path("org--repo", 5, "cam0")
            assert result == "org/repo/videos/cam0/chunk-001/file-005.mp4"


class TestEpisodeVideoWindow:
    """Per-episode video time-window lookup."""

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_window_returned_from_cache(self):
        provider = create_blob_dataset_provider(MagicMock())
        provider._episode_video_cache["org--repo"] = {
            5: {"cam0": (1, 2, 1.5, 4.25)},
        }
        result = await provider.get_episode_video_window("org--repo", 5, "cam0")
        assert result == (1.5, 4.25)

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_window_none_when_degenerate(self):
        provider = create_blob_dataset_provider(MagicMock())
        provider._episode_video_cache["org--repo"] = {
            5: {"cam0": (0, 0, 2.0, 2.0)},
        }
        assert await provider.get_episode_video_window("org--repo", 5, "cam0") is None

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_window_none_when_metadata_missing(self):
        provider = create_blob_dataset_provider(MagicMock())
        with patch.object(
            type(provider),
            "_load_episode_video_metadata",
            new=AsyncMock(return_value=None),
        ):
            assert await provider.get_episode_video_window("org--repo", 0, "cam0") is None

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_window_none_when_camera_absent(self):
        provider = create_blob_dataset_provider(MagicMock())
        provider._episode_video_cache["org--repo"] = {0: {"other": (0, 0, 0.0, 1.0)}}
        assert await provider.get_episode_video_window("org--repo", 0, "cam0") is None


class TestLoadEpisodeVideoMetadata:
    """Public handling of unavailable episode metadata."""

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_window_returns_none_on_container_failure(self) -> None:
        mock_client = MagicMock()
        mock_client.get_container_client.side_effect = RuntimeError("network down")
        provider = create_blob_dataset_provider(mock_client)
        assert await provider.get_episode_video_window("org--repo", 0, "cam0") is None

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_window_returns_none_without_parquet_blobs(self) -> None:
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter([_make_blob("org/repo/meta/episodes/readme.txt")])
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)
        assert await provider.get_episode_video_window("org--repo", 0, "cam0") is None


class TestStreamVideo:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_stream_video_yields_chunks(self):
        chunks = [b"a", b"bc", b"def"]
        mock_download = MagicMock()
        mock_download.chunks = MagicMock(return_value=_AsyncIter(chunks))
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock(return_value=mock_download)
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        async def collect():
            return [c async for c in provider.stream_video("p", offset=0, length=10)]

        result = await collect()
        assert result == chunks
        mock_blob.download_blob.assert_awaited_once_with(offset=0, length=10, max_concurrency=4)


class TestUploadVideo:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    @patch("src.api.storage.blob_dataset.BlobServiceClient")
    async def test_upload_video_success(self, mock_blob_service_cls: MagicMock, tmp_path: Path) -> None:
        local = tmp_path / "video.mp4"
        local.write_bytes(b"video-bytes")

        mock_blob = MagicMock()
        mock_blob.upload_blob = AsyncMock()
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_blob_service_cls.return_value = mock_client

        provider = create_blob_dataset_provider()
        result = await provider.upload_video("org--repo", "cam0", 7, local)

        assert result is True
        mock_container.get_blob_client.assert_called_once_with("org/repo/meta/videos/cam0/episode_000007.mp4")
        mock_blob.upload_blob.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    @patch("src.api.storage.blob_dataset.BlobServiceClient")
    async def test_upload_video_returns_false_on_error(self, mock_blob_service_cls):
        mock_blob_service_cls.side_effect = RuntimeError("nope")
        provider = create_blob_dataset_provider()
        result = await provider.upload_video("org--repo", "cam0", 0, Path("missing.mp4"))
        assert result is False


class TestSyncDatasetToLocal:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_sync_dataset_skips_videos_and_hdf5(self, tmp_path: Path) -> None:
        names = [
            "org/repo/meta/info.json",
            "org/repo/videos/cam0/chunk-000/file-000.mp4",  # skipped
            "org/repo/data/chunk-000/file-000.parquet",
            "org/repo/extra/episode_0.hdf5",  # skipped
        ]
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(
            [
                _make_directory_blob("org/repo/data"),
                _make_directory_blob("org/repo/data/chunk-000"),
                _make_blob("org/repo/.cache/huggingface/download.lock"),
                *(_make_blob(name) for name in names),
            ]
        )
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=b"data")) as read_mock:
            local_dir = tmp_path
            result = await provider.sync_dataset_to_local("org--repo", local_dir)
            assert result is True
            assert (local_dir / "meta" / "info.json").read_bytes() == b"data"
            assert (local_dir / "data" / "chunk-000" / "file-000.parquet").exists()
            assert not (local_dir / "videos").exists()
            assert not (local_dir / ".cache").exists()
            assert read_mock.await_count == 2

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_sync_dataset_returns_false_on_exception(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.get_container_client.side_effect = RuntimeError("boom")
        provider = create_blob_dataset_provider(mock_client)
        assert await provider.sync_dataset_to_local("org--repo", tmp_path) is False


class TestSyncMetaOnly:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_sync_meta_only_filters_to_allowed_blobs(self, tmp_path: Path) -> None:
        names = [
            "org/repo/meta/info.json",
            "org/repo/meta/stats.json",
            "org/repo/meta/episodes/chunk-0.parquet",
            "org/repo/meta/something_else.json",  # filtered out
        ]
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(
            [
                _make_directory_blob("org/repo/meta/episodes"),
                *(_make_blob(name) for name in names),
            ]
        )
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=b"x")):
            local_dir = tmp_path
            result = await provider.sync_meta_only_to_local("org--repo", local_dir)
            assert result is True
            assert (local_dir / "meta" / "info.json").exists()
            assert (local_dir / "meta" / "stats.json").exists()
            assert (local_dir / "meta" / "episodes" / "chunk-0.parquet").exists()
            assert not (local_dir / "meta" / "something_else.json").exists()

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_sync_meta_only_returns_false_when_info_missing(self, tmp_path: Path) -> None:
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter([])
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        assert await provider.sync_meta_only_to_local("org--repo", tmp_path) is False


class TestSyncHdf5Dataset:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_sync_hdf5_downloads_json_touches_hdf5_streams_video(self, tmp_path: Path) -> None:
        names = [
            "team/proj/dataset_config.json",
            "team/proj/episode_000000.hdf5",
            "team/proj/meta/videos/cam0/episode_000000.mp4",
        ]
        mock_download = MagicMock()
        mock_download.chunks = MagicMock(return_value=_AsyncIter([b"v1", b"v2"]))
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock(return_value=mock_download)
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(_make_blob(n) for n in names)
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=b"json-bytes")):
            local_dir = tmp_path
            result = await provider.sync_hdf5_dataset_to_local("team--proj", local_dir)
            assert result is True
            assert (local_dir / "dataset_config.json").read_bytes() == b"json-bytes"
            assert (local_dir / "episode_000000.hdf5").exists()
            video_path = local_dir / "meta" / "videos" / "cam0" / "episode_000000.mp4"
            assert video_path.read_bytes() == b"v1v2"

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_sync_hdf5_returns_false_on_error(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_client.get_container_client.side_effect = RuntimeError("boom")
        provider = create_blob_dataset_provider(mock_client)
        assert await provider.sync_hdf5_dataset_to_local("team--proj", tmp_path) is False


class TestSyncHdf5Episode:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_sync_hdf5_episode_streams_to_disk(self, tmp_path: Path) -> None:
        names = ["team/proj/episode_000003.hdf5"]
        mock_download = MagicMock()
        mock_download.chunks = MagicMock(return_value=_AsyncIter([b"chunk1", b"chunk2"]))
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock(return_value=mock_download)
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(_make_blob(n) for n in names)
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        result = await provider.sync_hdf5_episode_to_local("team--proj", tmp_path, 3)
        assert result is True
        assert (tmp_path / "episode_000003.hdf5").read_bytes() == b"chunk1chunk2"

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_sync_hdf5_episode_short_circuits_when_present(self, tmp_path: Path) -> None:
        names = ["team/proj/episode_000001.hdf5"]
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock()
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(_make_blob(n) for n in names)
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        (tmp_path / "episode_000001.hdf5").write_bytes(b"existing")
        result = await provider.sync_hdf5_episode_to_local("team--proj", tmp_path, 1)
        assert result is True
        mock_blob.download_blob.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_sync_hdf5_episode_returns_false_when_not_listed(self, tmp_path: Path) -> None:
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter([])
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        assert await provider.sync_hdf5_episode_to_local("team--proj", tmp_path, 9) is False


class TestHdf5Helpers:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_hdf5_dataset_config_parses_json(self):
        provider = create_blob_dataset_provider(MagicMock())
        with patch.object(
            type(provider),
            "_read_blob_bytes",
            new=AsyncMock(return_value=b'{"k": 1}'),
        ):
            assert await provider.get_hdf5_dataset_config("team--proj") == {"k": 1}

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_hdf5_dataset_config_returns_none_when_missing(self):
        provider = create_blob_dataset_provider(MagicMock())
        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=None)):
            assert await provider.get_hdf5_dataset_config("team--proj") is None

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_get_hdf5_dataset_config_returns_none_on_invalid_json(self):
        provider = create_blob_dataset_provider(MagicMock())
        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=b"not-json")):
            assert await provider.get_hdf5_dataset_config("team--proj") is None

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_count_hdf5_episodes_counts_only_hdf5(self):
        names = [
            "team/proj/episode_0.hdf5",
            "team/proj/episode_1.hdf5",
            "team/proj/dataset_config.json",
        ]
        mock_container = MagicMock()
        mock_container.list_blob_names.return_value = _AsyncIter(names)
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)
        assert await provider.count_hdf5_episodes("team--proj") == 2

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_count_hdf5_episodes_returns_zero_on_error(self):
        mock_client = MagicMock()
        mock_client.get_container_client.side_effect = RuntimeError("boom")
        provider = create_blob_dataset_provider(mock_client)
        assert await provider.count_hdf5_episodes("team--proj") == 0


class TestClose:
    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_close_releases_client(self):
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        provider = create_blob_dataset_provider(mock_client)
        await provider.close()
        mock_client.close.assert_awaited_once()
        assert provider._client is None

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_close_when_client_never_initialized(self):
        provider = create_blob_dataset_provider()
        # Should be a no-op without raising.
        await provider.close()
        assert provider._client is None


def _build_episodes_parquet(*, episodes, cameras_by_episode):
    """Build a real meta/episodes/ parquet payload as bytes.

    ``episodes`` is a list of episode indices; ``cameras_by_episode`` maps
    episode_index → {camera: (chunk, file, from_ts, to_ts)}.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    all_cameras: set[str] = set()
    for cams in cameras_by_episode.values():
        all_cameras.update(cams.keys())

    columns: dict[str, list] = {"episode_index": list(episodes)}
    for camera in sorted(all_cameras):
        chunk_col = f"videos/{camera}/chunk_index"
        file_col = f"videos/{camera}/file_index"
        from_col = f"videos/{camera}/from_timestamp"
        to_col = f"videos/{camera}/to_timestamp"
        columns[chunk_col] = []
        columns[file_col] = []
        columns[from_col] = []
        columns[to_col] = []
        for ep in episodes:
            entry = cameras_by_episode.get(ep, {}).get(camera)
            if entry is None:
                columns[chunk_col].append(None)
                columns[file_col].append(None)
                columns[from_col].append(None)
                columns[to_col].append(None)
            else:
                chunk, file_idx, from_ts, to_ts = entry
                columns[chunk_col].append(chunk)
                columns[file_col].append(file_idx)
                columns[from_col].append(from_ts)
                columns[to_col].append(to_ts)

    buf = pa.BufferOutputStream()
    pq.write_table(pa.table(columns), buf)
    return bytes(buf.getvalue())


class TestLoadEpisodeVideoMetadataHappyPath:
    """Public video-window behavior backed by real parquet metadata."""

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_video_windows_parse_real_parquet(self) -> None:
        payload = _build_episodes_parquet(
            episodes=[0, 1, 2],
            cameras_by_episode={
                0: {"cam0": (0, 0, 0.0, 1.5), "cam1": (0, 1, 0.0, 1.5)},
                1: {"cam0": (0, 0, 1.5, 3.0), "cam1": (0, 1, 1.5, 3.0)},
                2: {"cam0": (0, 0, 3.0, 4.25), "cam1": (0, 1, 3.0, 4.25)},
            },
        )
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(
            [_make_blob("org/repo/meta/episodes/chunk-000/file-000.parquet")]
        )
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=payload)):
            assert await provider.get_episode_video_window("org--repo", 0, "cam0") == (0.0, 1.5)
            assert await provider.get_episode_video_window("org--repo", 1, "cam1") == (1.5, 3.0)
            assert await provider.get_episode_video_window("org--repo", 2, "cam0") == (3.0, 4.25)

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_window_ignores_parquet_without_episode_index_column(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        buf = pa.BufferOutputStream()
        pq.write_table(pa.table({"length": [10, 20]}), buf)
        payload = bytes(buf.getvalue())

        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(
            [_make_blob("org/repo/meta/episodes/chunk-000/file-000.parquet")]
        )
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=payload)):
            assert await provider.get_episode_video_window("org--repo", 0, "cam0") is None

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_window_ignores_incomplete_camera_columns(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        buf = pa.BufferOutputStream()
        # Has chunk_index for cam0 but no file/from/to columns → entry skipped.
        pq.write_table(
            pa.table({"episode_index": [0], "videos/cam0/chunk_index": [0]}),
            buf,
        )
        payload = bytes(buf.getvalue())

        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(
            [_make_blob("org/repo/meta/episodes/chunk-000/file-000.parquet")]
        )
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=payload)):
            assert await provider.get_episode_video_window("org--repo", 0, "cam0") is None

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_window_returns_none_for_unreadable_parquet(self) -> None:
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(
            [_make_blob("org/repo/meta/episodes/chunk-000/file-000.parquet")]
        )
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=None)):
            assert await provider.get_episode_video_window("org--repo", 0, "cam0") is None


class TestGetEpisodeVideoEntryCache:
    """Public video-window metadata cache behavior."""

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_window_cache_miss_loads_metadata_once(self) -> None:
        provider = create_blob_dataset_provider(MagicMock())
        loaded = {0: {"cam0": (1, 2, 0.0, 1.0)}}
        with patch.object(
            type(provider),
            "_load_episode_video_metadata",
            new=AsyncMock(return_value=loaded),
        ) as load_mock:
            first = await provider.get_episode_video_window("org--repo", 0, "cam0")
            second = await provider.get_episode_video_window("org--repo", 0, "cam0")
        assert first == (0.0, 1.0)
        assert second == (0.0, 1.0)
        load_mock.assert_awaited_once_with("org--repo")
        assert provider._episode_video_cache["org--repo"] is loaded

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_window_returns_none_when_loader_returns_none(self) -> None:
        provider = create_blob_dataset_provider(MagicMock())
        with patch.object(
            type(provider),
            "_load_episode_video_metadata",
            new=AsyncMock(return_value=None),
        ):
            assert await provider.get_episode_video_window("org--repo", 0, "cam0") is None
        assert "org--repo" not in provider._episode_video_cache


class TestResolveVideoBlobPathMetaDriven:
    """resolve_video_blob_path branches that use meta-driven (chunk, file)."""

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_meta_driven_candidate_resolves_first(self):
        provider = create_blob_dataset_provider(MagicMock())
        info = {
            "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        }
        with (
            patch.object(type(provider), "get_info_json", new=AsyncMock(return_value=info)),
            patch.object(
                type(provider),
                "_get_episode_video_entry",
                new=AsyncMock(return_value=(2, 5, 0.0, 1.0)),
            ),
            patch.object(
                type(provider),
                "get_blob_properties",
                new=AsyncMock(return_value={"size": 1, "content_type": "video/mp4"}),
            ),
        ):
            result = await provider.resolve_video_blob_path("org--repo", 7, "cam0")
        assert result == "org/repo/videos/cam0/chunk-002/file-005.mp4"

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_meta_entry_with_invalid_template_falls_through(self):
        provider = create_blob_dataset_provider(MagicMock())
        # Template only references {video_key} → format succeeds but no
        # chunk/file substitution, so the templated string is just the path
        # with {video_key} replaced. We instead verify the broader behavior:
        # an invalid placeholder is silently skipped and we fall back to the
        # generic candidate list.
        bad_info = {"video_path": "videos/{video_key}/chunk-{nope}/file-{nope}.mp4"}
        with (
            patch.object(type(provider), "get_info_json", new=AsyncMock(return_value=bad_info)),
            patch.object(
                type(provider),
                "_get_episode_video_entry",
                new=AsyncMock(return_value=(0, 0, 0.0, 1.0)),
            ),
            patch.object(
                type(provider),
                "get_blob_properties",
                new=AsyncMock(return_value={"size": 1, "content_type": "video/mp4"}),
            ),
        ):
            result = await provider.resolve_video_blob_path("org--repo", 0, "cam0")
        # Falls through to generic candidates; first one resolves.
        assert result is not None
        assert result.startswith("org/repo/videos/cam0/")

    @pytest.mark.asyncio
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    async def test_fallback_scan_recognizes_episode_suffix(self):
        names = [
            "org/repo/videos/cam0/chunk-007/episode_000005.mp4",
        ]
        mock_container = MagicMock()

        def list_blobs(name_starts_with="", **_kwargs):
            return _AsyncIter(_make_blob(n) for n in names if n.startswith(name_starts_with))

        mock_container.list_blobs.side_effect = list_blobs
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = create_blob_dataset_provider(mock_client)

        with (
            patch.object(type(provider), "get_info_json", new=AsyncMock(return_value=None)),
            patch.object(
                type(provider),
                "_get_episode_video_entry",
                new=AsyncMock(return_value=None),
            ),
            patch.object(type(provider), "get_blob_properties", new=AsyncMock(return_value=None)),
        ):
            result = await provider.resolve_video_blob_path("org--repo", 5, "cam0")
        assert result == "org/repo/videos/cam0/chunk-007/episode_000005.mp4"
