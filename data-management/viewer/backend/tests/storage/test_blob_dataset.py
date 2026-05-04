"""Unit tests for the BlobDatasetProvider Azure Blob Storage adapter."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _AsyncIter:
    """Minimal async iterator over an in-memory sequence."""

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _make_blob(name: str):
    blob = MagicMock()
    blob.name = name
    return blob


def _build_provider(mock_client=None):
    from src.api.storage.blob_dataset import BlobDatasetProvider

    provider = BlobDatasetProvider(
        account_name="testaccount",
        container_name="testcontainer",
        sas_token="sas-token",
    )
    if mock_client is not None:
        provider._client = mock_client
    return provider


class TestImportGuard(TestCase):
    """Module-level Azure availability guard."""

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", False)
    def test_init_raises_when_azure_unavailable(self):
        from src.api.storage.blob_dataset import BlobDatasetProvider

        with pytest.raises(ImportError, match="BlobDatasetProvider requires"):
            BlobDatasetProvider(account_name="a", container_name="c")


class TestPrefixHelpers(TestCase):
    """Static helpers and prefix mapping."""

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_blob_prefix_replaces_double_dash(self):
        from src.api.storage.blob_dataset import BlobDatasetProvider

        assert BlobDatasetProvider.get_blob_prefix("org--repo") == "org/repo"
        assert BlobDatasetProvider.get_blob_prefix("a--b--c") == "a/b/c"


class TestGetClient(TestCase):
    """Client construction and caching."""

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    @patch("src.api.storage.blob_dataset.BlobServiceClient")
    def test_get_client_uses_sas_when_provided(self, mock_blob_service):
        provider = _build_provider()
        mock_blob_service.return_value = MagicMock()

        client = asyncio.run(provider._get_client())

        mock_blob_service.assert_called_once_with(
            account_url="https://testaccount.blob.core.windows.net",
            credential="sas-token",
        )
        assert client is mock_blob_service.return_value
        # Cached on second call.
        client2 = asyncio.run(provider._get_client())
        assert client2 is client
        assert mock_blob_service.call_count == 1

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    @patch("src.api.storage.blob_dataset.AsyncDefaultAzureCredential")
    @patch("src.api.storage.blob_dataset.BlobServiceClient")
    def test_get_client_uses_default_credential_without_sas(self, mock_blob_service, mock_credential_cls):
        from src.api.storage.blob_dataset import BlobDatasetProvider

        provider = BlobDatasetProvider(account_name="testaccount", container_name="testcontainer")
        cred_instance = MagicMock()
        mock_credential_cls.return_value = cred_instance

        asyncio.run(provider._get_client())

        mock_credential_cls.assert_called_once_with()
        mock_blob_service.assert_called_once_with(
            account_url="https://testaccount.blob.core.windows.net",
            credential=cred_instance,
        )


class TestReadBlobBytes(TestCase):
    """Low-level blob reader."""

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_read_blob_bytes_success(self):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_download = AsyncMock()
        mock_download.readall = AsyncMock(return_value=b"payload")
        mock_blob.download_blob = AsyncMock(return_value=mock_download)
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        provider = _build_provider(mock_client)
        result = asyncio.run(provider._read_blob_bytes("some/path"))
        assert result == b"payload"

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_read_blob_bytes_returns_none_on_not_found(self):
        _NotFound = type("ResourceNotFoundError", (Exception,), {})
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock(side_effect=_NotFound("missing"))
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        provider = _build_provider(mock_client)
        with patch("src.api.storage.blob_dataset.ResourceNotFoundError", _NotFound):
            assert asyncio.run(provider._read_blob_bytes("missing")) is None

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_read_blob_bytes_returns_none_on_generic_error(self):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock(side_effect=RuntimeError("boom"))
        mock_container.get_blob_client.return_value = mock_blob
        mock_client.get_container_client.return_value = mock_container

        provider = _build_provider(mock_client)
        assert asyncio.run(provider._read_blob_bytes("x")) is None


class TestScanAllDatasetIds(TestCase):
    """Container scan classifying LeRobot vs HDF5 datasets."""

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_scan_classifies_and_dedupes(self):
        names = [
            "org1/repo1/meta/info.json",
            "org1/repo1/data/chunk-0.parquet",
            "org2/repo2/meta/info.json",
            # HDF5 datasets
            "team/projectA/episode_0.hdf5",
            "team/projectA/episode_1.hdf5",
            # HDF5 path under an existing LeRobot org — joined id differs and is kept
            "org1/repo1/extra/episode_0.hdf5",
            # Too-deep HDF5 layout (>5 segments) → ignored
            "a/b/c/d/e/f/episode_0.hdf5",
        ]
        mock_container = MagicMock()
        mock_container.list_blob_names.return_value = _AsyncIter(names)
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = _build_provider(mock_client)
        result = asyncio.run(provider.scan_all_dataset_ids())

        assert result["lerobot"] == ["org1", "org2"]
        assert result["hdf5"] == ["org1--repo1--extra", "team--projectA"]

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_scan_swallows_outer_exception(self):
        mock_client = MagicMock()
        mock_client.get_container_client.side_effect = RuntimeError("network")

        provider = _build_provider(mock_client)
        result = asyncio.run(provider.scan_all_dataset_ids())
        assert result == {"lerobot": [], "hdf5": []}

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_list_dataset_ids_delegates_to_scan(self):
        provider = _build_provider(MagicMock())
        with patch.object(
            type(provider),
            "scan_all_dataset_ids",
            new=AsyncMock(return_value={"lerobot": ["a"], "hdf5": ["b"]}),
        ):
            assert asyncio.run(provider.list_dataset_ids()) == ["a"]
            assert asyncio.run(provider.list_hdf5_dataset_ids()) == ["b"]


class TestDatasetExists(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_dataset_exists_true(self):
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(return_value=MagicMock())
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = _build_provider(mock_client)
        assert asyncio.run(provider.dataset_exists("org--repo")) is True
        mock_container.get_blob_client.assert_called_once_with("org/repo/meta/info.json")

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_dataset_exists_not_found(self):
        _NotFound = type("ResourceNotFoundError", (Exception,), {})
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(side_effect=_NotFound("nope"))
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = _build_provider(mock_client)
        with patch("src.api.storage.blob_dataset.ResourceNotFoundError", _NotFound):
            assert asyncio.run(provider.dataset_exists("org--repo")) is False

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_dataset_exists_false_on_generic_error(self):
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(side_effect=RuntimeError("boom"))
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = _build_provider(mock_client)
        assert asyncio.run(provider.dataset_exists("org--repo")) is False


class TestGetInfoJson(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_info_json_returns_parsed_and_caches(self):
        provider = _build_provider(MagicMock())
        payload = {"chunks_size": 1000}
        with patch.object(
            type(provider),
            "_read_blob_bytes",
            new=AsyncMock(return_value=json.dumps(payload).encode("utf-8")),
        ) as read_mock:
            assert asyncio.run(provider.get_info_json("org--repo")) == payload
            # Second call hits cache; _read_blob_bytes not invoked again.
            assert asyncio.run(provider.get_info_json("org--repo")) == payload
            assert read_mock.call_count == 1

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_info_json_returns_none_when_missing(self):
        provider = _build_provider(MagicMock())
        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=None)):
            assert asyncio.run(provider.get_info_json("org--repo")) is None

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_info_json_returns_none_on_invalid_json(self):
        provider = _build_provider(MagicMock())
        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=b"not-json")):
            assert asyncio.run(provider.get_info_json("org--repo")) is None


class TestGetBlobProperties(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_blob_properties_success(self):
        props = MagicMock()
        props.size = 42
        props.content_settings.content_type = "video/mp4"
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(return_value=props)
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = _build_provider(mock_client)
        result = asyncio.run(provider.get_blob_properties("path/to/blob"))
        assert result == {"size": 42, "content_type": "video/mp4"}

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_blob_properties_default_content_type(self):
        props = MagicMock()
        props.size = 7
        props.content_settings.content_type = None
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(return_value=props)
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = _build_provider(mock_client)
        result = asyncio.run(provider.get_blob_properties("p"))
        assert result == {"size": 7, "content_type": "application/octet-stream"}

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_blob_properties_not_found(self):
        _NotFound = type("ResourceNotFoundError", (Exception,), {})
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(side_effect=_NotFound())
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = _build_provider(mock_client)
        with patch("src.api.storage.blob_dataset.ResourceNotFoundError", _NotFound):
            assert asyncio.run(provider.get_blob_properties("p")) is None

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_blob_properties_returns_none_on_error(self):
        mock_blob = MagicMock()
        mock_blob.get_blob_properties = AsyncMock(side_effect=RuntimeError("boom"))
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container

        provider = _build_provider(mock_client)
        assert asyncio.run(provider.get_blob_properties("p")) is None


class TestVideoPathCandidates(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_template_one_per_chunk_and_chunked_layout(self):
        from src.api.storage.blob_dataset import BlobDatasetProvider

        info = {
            "chunks_size": 10,
            "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        }
        result = BlobDatasetProvider._build_video_path_candidates(info, "p", "cam0", 23)
        assert result == [
            "p/videos/cam0/chunk-023/file-000.mp4",
            "p/videos/cam0/chunk-002/file-003.mp4",
        ]

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_fallback_when_no_template(self):
        from src.api.storage.blob_dataset import BlobDatasetProvider

        result = BlobDatasetProvider._build_video_path_candidates(None, "p", "cam0", 5)
        assert result == [
            "p/videos/cam0/chunk-005/file-005.mp4",
            "p/videos/cam0/chunk-000/file-005.mp4",
        ]


class TestResolveVideoBlobPath(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_resolve_returns_first_existing_candidate(self):
        provider = _build_provider(MagicMock())
        with (
            patch.object(type(provider), "get_info_json", new=AsyncMock(return_value=None)),
            patch.object(
                type(provider),
                "get_blob_properties",
                new=AsyncMock(side_effect=[None, {"size": 1, "content_type": "video/mp4"}]),
            ),
        ):
            result = asyncio.run(provider.resolve_video_blob_path("org--repo", 5, "cam0"))
            assert result == "org/repo/videos/cam0/chunk-000/file-005.mp4"

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_resolve_falls_back_to_scan(self):
        names = [
            "org/repo/videos/cam0/chunk-001/file-005.mp4",
        ]
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(_make_blob(n) for n in names)
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = _build_provider(mock_client)

        with (
            patch.object(type(provider), "get_info_json", new=AsyncMock(return_value=None)),
            patch.object(type(provider), "get_blob_properties", new=AsyncMock(return_value=None)),
        ):
            result = asyncio.run(provider.resolve_video_blob_path("org--repo", 5, "cam0"))
            assert result == "org/repo/videos/cam0/chunk-001/file-005.mp4"


class TestStreamVideo(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_stream_video_yields_chunks(self):
        chunks = [b"a", b"bc", b"def"]
        mock_download = MagicMock()
        mock_download.chunks = MagicMock(return_value=_AsyncIter(chunks))
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock(return_value=mock_download)
        mock_container = MagicMock()
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = _build_provider(mock_client)

        async def collect():
            return [c async for c in provider.stream_video("p", offset=0, length=10)]

        result = asyncio.run(collect())
        assert result == chunks
        mock_blob.download_blob.assert_awaited_once_with(offset=0, length=10, max_concurrency=4)


class TestUploadVideo(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    @patch("src.api.storage.blob_dataset.BlobServiceClient")
    def test_upload_video_success(self, mock_blob_service_cls, tmp_path: Path | None = None):
        # tmp_path is provided by pytest fixture in pytest-style tests; fall back manually
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as td:
            local = Path(td) / "video.mp4"
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

            provider = _build_provider()
            result = asyncio.run(provider.upload_video("org--repo", "cam0", 7, local))

            assert result is True
            mock_container.get_blob_client.assert_called_once_with("org/repo/meta/videos/cam0/episode_000007.mp4")
            mock_blob.upload_blob.assert_awaited_once()

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    @patch("src.api.storage.blob_dataset.BlobServiceClient")
    def test_upload_video_returns_false_on_error(self, mock_blob_service_cls):
        mock_blob_service_cls.side_effect = RuntimeError("nope")
        provider = _build_provider()
        result = asyncio.run(provider.upload_video("org--repo", "cam0", 0, Path("missing.mp4")))
        assert result is False


class TestSyncDatasetToLocal(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_sync_dataset_skips_videos_and_hdf5(self):
        from tempfile import TemporaryDirectory

        names = [
            "org/repo/meta/info.json",
            "org/repo/videos/cam0/chunk-000/file-000.mp4",  # skipped
            "org/repo/data/chunk-000/file-000.parquet",
            "org/repo/extra/episode_0.hdf5",  # skipped
        ]
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(_make_blob(n) for n in names)
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = _build_provider(mock_client)

        with (
            TemporaryDirectory() as td,
            patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=b"data")) as read_mock,
        ):
            local_dir = Path(td)
            result = asyncio.run(provider.sync_dataset_to_local("org--repo", local_dir))
            assert result is True
            assert (local_dir / "meta" / "info.json").read_bytes() == b"data"
            assert (local_dir / "data" / "chunk-000" / "file-000.parquet").exists()
            assert not (local_dir / "videos").exists()
            assert read_mock.await_count == 2

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_sync_dataset_returns_false_on_exception(self):
        from tempfile import TemporaryDirectory

        mock_client = MagicMock()
        mock_client.get_container_client.side_effect = RuntimeError("boom")
        provider = _build_provider(mock_client)
        with TemporaryDirectory() as td:
            assert asyncio.run(provider.sync_dataset_to_local("org--repo", Path(td))) is False


class TestSyncMetaOnly(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_sync_meta_only_filters_to_allowed_blobs(self):
        from tempfile import TemporaryDirectory

        names = [
            "org/repo/meta/info.json",
            "org/repo/meta/stats.json",
            "org/repo/meta/episodes/chunk-0.parquet",
            "org/repo/meta/something_else.json",  # filtered out
        ]
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(_make_blob(n) for n in names)
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = _build_provider(mock_client)

        with (
            TemporaryDirectory() as td,
            patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=b"x")),
        ):
            local_dir = Path(td)
            result = asyncio.run(provider.sync_meta_only_to_local("org--repo", local_dir))
            assert result is True
            assert (local_dir / "meta" / "info.json").exists()
            assert (local_dir / "meta" / "stats.json").exists()
            assert (local_dir / "meta" / "episodes" / "chunk-0.parquet").exists()
            assert not (local_dir / "meta" / "something_else.json").exists()

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_sync_meta_only_returns_false_when_info_missing(self):
        from tempfile import TemporaryDirectory

        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter([])
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = _build_provider(mock_client)

        with TemporaryDirectory() as td:
            assert asyncio.run(provider.sync_meta_only_to_local("org--repo", Path(td))) is False


class TestSyncHdf5Dataset(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_sync_hdf5_downloads_json_touches_hdf5_streams_video(self):
        from tempfile import TemporaryDirectory

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
        provider = _build_provider(mock_client)

        with (
            TemporaryDirectory() as td,
            patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=b"json-bytes")),
        ):
            local_dir = Path(td)
            result = asyncio.run(provider.sync_hdf5_dataset_to_local("team--proj", local_dir))
            assert result is True
            assert (local_dir / "dataset_config.json").read_bytes() == b"json-bytes"
            assert (local_dir / "episode_000000.hdf5").exists()
            video_path = local_dir / "meta" / "videos" / "cam0" / "episode_000000.mp4"
            assert video_path.read_bytes() == b"v1v2"

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_sync_hdf5_returns_false_on_error(self):
        from tempfile import TemporaryDirectory

        mock_client = MagicMock()
        mock_client.get_container_client.side_effect = RuntimeError("boom")
        provider = _build_provider(mock_client)
        with TemporaryDirectory() as td:
            assert asyncio.run(provider.sync_hdf5_dataset_to_local("team--proj", Path(td))) is False


class TestSyncHdf5Episode(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_sync_hdf5_episode_streams_to_disk(self):
        from tempfile import TemporaryDirectory

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
        provider = _build_provider(mock_client)

        with TemporaryDirectory() as td:
            local_dir = Path(td)
            result = asyncio.run(provider.sync_hdf5_episode_to_local("team--proj", local_dir, 3))
            assert result is True
            assert (local_dir / "episode_000003.hdf5").read_bytes() == b"chunk1chunk2"

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_sync_hdf5_episode_short_circuits_when_present(self):
        from tempfile import TemporaryDirectory

        names = ["team/proj/episode_000001.hdf5"]
        mock_blob = MagicMock()
        mock_blob.download_blob = AsyncMock()
        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter(_make_blob(n) for n in names)
        mock_container.get_blob_client.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = _build_provider(mock_client)

        with TemporaryDirectory() as td:
            local_dir = Path(td)
            (local_dir / "episode_000001.hdf5").write_bytes(b"existing")
            result = asyncio.run(provider.sync_hdf5_episode_to_local("team--proj", local_dir, 1))
            assert result is True
            mock_blob.download_blob.assert_not_called()

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_sync_hdf5_episode_returns_false_when_not_listed(self):
        from tempfile import TemporaryDirectory

        mock_container = MagicMock()
        mock_container.list_blobs.return_value = _AsyncIter([])
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = _build_provider(mock_client)

        with TemporaryDirectory() as td:
            assert asyncio.run(provider.sync_hdf5_episode_to_local("team--proj", Path(td), 9)) is False


class TestHdf5Helpers(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_hdf5_dataset_config_parses_json(self):
        provider = _build_provider(MagicMock())
        with patch.object(
            type(provider),
            "_read_blob_bytes",
            new=AsyncMock(return_value=b'{"k": 1}'),
        ):
            assert asyncio.run(provider.get_hdf5_dataset_config("team--proj")) == {"k": 1}

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_hdf5_dataset_config_returns_none_when_missing(self):
        provider = _build_provider(MagicMock())
        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=None)):
            assert asyncio.run(provider.get_hdf5_dataset_config("team--proj")) is None

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_get_hdf5_dataset_config_returns_none_on_invalid_json(self):
        provider = _build_provider(MagicMock())
        with patch.object(type(provider), "_read_blob_bytes", new=AsyncMock(return_value=b"not-json")):
            assert asyncio.run(provider.get_hdf5_dataset_config("team--proj")) is None

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_count_hdf5_episodes_counts_only_hdf5(self):
        names = [
            "team/proj/episode_0.hdf5",
            "team/proj/episode_1.hdf5",
            "team/proj/dataset_config.json",
        ]
        mock_container = MagicMock()
        mock_container.list_blob_names.return_value = _AsyncIter(names)
        mock_client = MagicMock()
        mock_client.get_container_client.return_value = mock_container
        provider = _build_provider(mock_client)
        assert asyncio.run(provider.count_hdf5_episodes("team--proj")) == 2

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_count_hdf5_episodes_returns_zero_on_error(self):
        mock_client = MagicMock()
        mock_client.get_container_client.side_effect = RuntimeError("boom")
        provider = _build_provider(mock_client)
        assert asyncio.run(provider.count_hdf5_episodes("team--proj")) == 0


class TestClose(TestCase):
    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_close_releases_client(self):
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        provider = _build_provider(mock_client)
        asyncio.run(provider.close())
        mock_client.close.assert_awaited_once()
        assert provider._client is None

    @patch("src.api.storage.blob_dataset.AZURE_AVAILABLE", True)
    def test_close_when_client_never_initialized(self):
        provider = _build_provider()
        # Should be a no-op without raising.
        asyncio.run(provider.close())
        assert provider._client is None
