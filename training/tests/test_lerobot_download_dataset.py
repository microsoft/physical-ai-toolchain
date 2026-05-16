"""Tests for training/il/scripts/lerobot/download_dataset.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from conftest import load_training_module  # noqa: E402


def _install_azure_stubs(monkeypatch, list_blobs_return=(), download_payload=b"data"):
    azure_pkg = ModuleType("azure")
    azure_identity = ModuleType("azure.identity")
    azure_storage = ModuleType("azure.storage")
    azure_storage_blob = ModuleType("azure.storage.blob")

    azure_identity.DefaultAzureCredential = MagicMock(return_value="cred")

    download_stream = SimpleNamespace(readall=MagicMock(return_value=download_payload))
    container_client = SimpleNamespace(
        list_blobs=MagicMock(return_value=list(list_blobs_return)),
        download_blob=MagicMock(return_value=download_stream),
    )
    service_client = SimpleNamespace(
        get_container_client=MagicMock(return_value=container_client),
    )
    azure_storage_blob.BlobServiceClient = MagicMock(return_value=service_client)

    monkeypatch.setitem(sys.modules, "azure", azure_pkg)
    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)
    monkeypatch.setitem(sys.modules, "azure.storage", azure_storage)
    monkeypatch.setitem(sys.modules, "azure.storage.blob", azure_storage_blob)

    return SimpleNamespace(
        identity=azure_identity,
        blob_service_cls=azure_storage_blob.BlobServiceClient,
        service_client=service_client,
        container_client=container_client,
        download_stream=download_stream,
    )


_MOD = load_training_module(
    "training_il_scripts_lerobot_download_dataset",
    "training/il/scripts/lerobot/download_dataset.py",
)


class TestDownloadDataset:
    def test_downloads_and_skips_filtered_blobs(self, monkeypatch, tmp_path):
        prefix = "p"
        blobs = [
            SimpleNamespace(name=f"{prefix}/data/file.parquet"),
            SimpleNamespace(name=f"{prefix}/.cache/x"),
            SimpleNamespace(name=f"{prefix}/foo.lock"),
            SimpleNamespace(name=f"{prefix}/foo.metadata"),
            SimpleNamespace(name=f"{prefix}/meta/info.json"),
        ]
        stubs = _install_azure_stubs(monkeypatch, list_blobs_return=blobs, download_payload=b"abc")
        monkeypatch.setenv("AZURE_CLIENT_ID", "cid")
        monkeypatch.setenv("AZURE_AUTHORITY_HOST", "host")

        result = _MOD.download_dataset(
            storage_account="acct",
            storage_container="cont",
            blob_prefix=prefix,
            dataset_root=str(tmp_path),
            dataset_repo_id="user/ds",
        )

        assert result == tmp_path / "user" / "ds"
        assert (result / "data" / "file.parquet").read_bytes() == b"abc"
        assert (result / "meta" / "info.json").read_bytes() == b"abc"
        assert not (result / ".cache").exists()
        assert not (result / "foo.lock").exists()
        assert not (result / "foo.metadata").exists()
        stubs.blob_service_cls.assert_called_once()
        stubs.service_client.get_container_client.assert_called_once_with("cont")


class TestVerifyDataset:
    def test_returns_none_when_missing(self, tmp_path):
        assert _MOD.verify_dataset(tmp_path) is None

    def test_returns_info(self, tmp_path):
        meta = tmp_path / "meta"
        meta.mkdir()
        info = {"robot_type": "so100", "total_episodes": 2, "total_frames": 100}
        (meta / "info.json").write_text(json.dumps(info))
        assert _MOD.verify_dataset(tmp_path) == info


def _write_parquet(path: Path, columns: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(columns), path)


class TestPatchInfoPaths:
    def test_no_conversion_needed(self, tmp_path):
        info = {"data_path": "data/already.parquet"}
        _MOD.patch_info_paths(tmp_path, info)
        # info untouched
        assert info == {"data_path": "data/already.parquet"}

    def test_no_tables_returns(self, tmp_path):
        (tmp_path / "data").mkdir()
        info = {"data_path": "data/{chunk_index}/{file_index}.parquet"}
        _MOD.patch_info_paths(tmp_path, info)  # no parquet files - returns early
        assert "{chunk_index}" in info["data_path"]

    def test_full_conversion_with_videos(self, tmp_path):
        # Create monolithic parquet with two episodes
        data_dir = tmp_path / "data"
        _write_parquet(
            data_dir / "chunk-000" / "file-000.parquet",
            {"episode_index": [0, 0, 1, 1], "value": [1.0, 2.0, 3.0, 4.0]},
        )
        # Create an extra file-style parquet to be unlinked
        _write_parquet(
            data_dir / "chunk-000" / "file-001.parquet",
            {"episode_index": [2], "value": [5.0]},
        )

        # Create video files in arbitrary chunk directories
        cam_dir = tmp_path / "videos" / "observation.images.cam"
        (cam_dir / "chunk-000").mkdir(parents=True)
        (cam_dir / "chunk-000" / "file-000.mp4").write_bytes(b"v0")
        (cam_dir / "chunk-001").mkdir(parents=True)
        (cam_dir / "chunk-001" / "file-001.mp4").write_bytes(b"v1")
        # Bad-named video should be skipped (ValueError on int parse)
        (cam_dir / "chunk-000" / "file-bad.mp4").write_bytes(b"vx")

        # Empty video key dir to exercise the skip branches
        (tmp_path / "videos" / "missing").mkdir(parents=True)
        empty_key = tmp_path / "videos" / "observation.images.empty"
        empty_key.mkdir(parents=True)

        meta = tmp_path / "meta"
        meta.mkdir()
        info = {
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "chunks_size": 1000,
            "features": {
                "observation.images.cam": {"dtype": "video"},
                "observation.images.missing": {"dtype": "video"},
                "observation.images.empty": {"dtype": "image"},
                "value": {"dtype": "float32"},
            },
        }
        info_path = meta / "info.json"
        info_path.write_text(json.dumps(info))

        _MOD.patch_info_paths(tmp_path, info)

        assert info["codebase_version"] == "v2.1"
        assert "{episode_chunk" in info["data_path"]
        assert "{episode_chunk" in info["video_path"]
        # Per-episode parquet files created
        assert (data_dir / "chunk-000" / "episode_000000.parquet").exists()
        assert (data_dir / "chunk-000" / "episode_000001.parquet").exists()
        # Old file-*.parquet removed
        assert not (data_dir / "chunk-000" / "file-000.parquet").exists()
        # Video moved into episode-named layout
        assert (cam_dir / "chunk-000" / "episode_000000.mp4").exists()
        assert (cam_dir / "chunk-000" / "episode_000001.mp4").exists()
        # info.json on disk updated
        assert json.loads(info_path.read_text())["codebase_version"] == "v2.1"


class TestPatchImageStats:
    def test_missing_stats_returns(self, tmp_path):
        _MOD.patch_image_stats(tmp_path, {"features": {}})  # no exception

    def test_adds_image_stats(self, tmp_path):
        meta = tmp_path / "meta"
        meta.mkdir()
        stats_path = meta / "stats.json"
        stats_path.write_text(json.dumps({"existing": {}}))
        info = {
            "features": {
                "cam": {"dtype": "video"},
                "img": {"dtype": "image"},
                "vec": {"dtype": "float32"},
                "existing": {"dtype": "video"},
            }
        }
        _MOD.patch_image_stats(tmp_path, info)
        data = json.loads(stats_path.read_text())
        assert "cam" in data and "img" in data
        assert "vec" not in data
        # existing key untouched
        assert data["existing"] == {}

    def test_no_update_when_no_image_features(self, tmp_path):
        meta = tmp_path / "meta"
        meta.mkdir()
        stats_path = meta / "stats.json"
        stats_path.write_text(json.dumps({"vec": {"mean": 0}}))
        _MOD.patch_image_stats(tmp_path, {"features": {"vec": {"dtype": "float32"}}})
        assert json.loads(stats_path.read_text()) == {"vec": {"mean": 0}}


class TestFixVideoTimestamps:
    def test_no_video_keys_short_circuits(self, tmp_path):
        _MOD.fix_video_timestamps(tmp_path, {"fps": 30, "features": {}})

    def test_fixes_metadata_and_realigns(self, tmp_path):
        info = {
            "fps": 10,
            "features": {"cam": {"dtype": "video"}},
        }
        episodes_dir = tmp_path / "meta" / "episodes"
        # First file has cumulative timestamps that need fixing
        _write_parquet(
            episodes_dir / "ep0.parquet",
            {
                "length": [5, 5],
                "videos/cam/from_timestamp": [0.0, 10.0],
                "videos/cam/to_timestamp": [5.0, 15.0],
            },
        )
        # Second file already aligned (no change)
        _write_parquet(
            episodes_dir / "ep1.parquet",
            {
                "length": [5],
                "videos/cam/from_timestamp": [0.0],
                "videos/cam/to_timestamp": [0.5],
            },
        )
        # File missing the columns is a no-op pass
        _write_parquet(
            episodes_dir / "ep_other.parquet",
            {"length": [5]},
        )

        data_dir = tmp_path / "data"
        # File with drifted timestamps that should be realigned
        _write_parquet(
            data_dir / "chunk-000" / "episode_000000.parquet",
            {"timestamp": [0.0, 0.05, 0.5, 1.5], "value": [1, 2, 3, 4]},
        )
        # File already aligned (no realign)
        _write_parquet(
            data_dir / "chunk-000" / "episode_000001.parquet",
            {"timestamp": [0.0, 0.1, 0.2], "value": [1, 2, 3]},
        )
        # Empty timestamp file
        _write_parquet(
            data_dir / "chunk-000" / "episode_000002.parquet",
            {"timestamp": [], "value": []},
        )

        _MOD.fix_video_timestamps(tmp_path, info)

        first = pq.read_table(episodes_dir / "ep0.parquet")
        from_vals = first["videos/cam/from_timestamp"].to_pylist()
        to_vals = first["videos/cam/to_timestamp"].to_pylist()
        assert from_vals == [0.0, 0.0]
        assert to_vals == [0.5, 0.5]

        drifted = pq.read_table(data_dir / "chunk-000" / "episode_000000.parquet")
        assert drifted["timestamp"].to_pylist() == [0.0, 0.1, 0.2, 0.3]


class TestReadEpisodeLengths:
    def test_reads_lengths(self, tmp_path):
        episodes_dir = tmp_path / "meta" / "episodes"
        _write_parquet(episodes_dir / "a.parquet", {"length": [5, 6]})
        _write_parquet(episodes_dir / "b.parquet", {"length": [7]})
        out = _MOD._read_episode_lengths(tmp_path, total_episodes=3)
        assert out == {0: 5, 1: 6, 2: 7}

    def test_skips_files_without_length(self, tmp_path):
        episodes_dir = tmp_path / "meta" / "episodes"
        _write_parquet(episodes_dir / "x.parquet", {"foo": [1]})
        assert _MOD._read_episode_lengths(tmp_path, total_episodes=0) == {}


class TestEnsureTasksJsonl:
    def test_existing_short_circuits(self, tmp_path):
        meta = tmp_path / "meta"
        meta.mkdir()
        (meta / "tasks.jsonl").write_text("existing")
        _MOD.ensure_tasks_jsonl(tmp_path, {"total_episodes": 1, "robot_type": "so100"})
        assert (meta / "tasks.jsonl").read_text() == "existing"

    def test_creates_tasks_and_episodes(self, tmp_path):
        meta = tmp_path / "meta"
        meta.mkdir()
        episodes_dir = meta / "episodes"
        _write_parquet(episodes_dir / "a.parquet", {"length": [5, 6]})
        info = {"total_episodes": 2, "robot_type": "so100"}
        _MOD.ensure_tasks_jsonl(tmp_path, info)
        tasks_lines = (meta / "tasks.jsonl").read_text().strip().splitlines()
        assert json.loads(tasks_lines[0])["task_index"] == 0
        ep_lines = (meta / "episodes.jsonl").read_text().strip().splitlines()
        assert len(ep_lines) == 2
        assert json.loads(ep_lines[0])["length"] == 5

    def test_skips_episodes_when_total_zero(self, tmp_path):
        meta = tmp_path / "meta"
        meta.mkdir()
        _MOD.ensure_tasks_jsonl(tmp_path, {"total_episodes": 0})
        assert (meta / "tasks.jsonl").exists()
        assert not (meta / "episodes.jsonl").exists()


class TestEnsureEpisodesStats:
    def test_existing_short_circuits(self, tmp_path):
        meta = tmp_path / "meta"
        meta.mkdir()
        (meta / "episodes_stats.jsonl").write_text("x")
        _MOD.ensure_episodes_stats(tmp_path, {"total_episodes": 1})
        assert (meta / "episodes_stats.jsonl").read_text() == "x"

    def test_zero_episodes_returns(self, tmp_path):
        (tmp_path / "meta").mkdir()
        _MOD.ensure_episodes_stats(tmp_path, {"total_episodes": 0})
        assert not (tmp_path / "meta" / "episodes_stats.jsonl").exists()

    def test_no_data_files_returns(self, tmp_path):
        (tmp_path / "meta").mkdir()
        (tmp_path / "data").mkdir()
        _MOD.ensure_episodes_stats(tmp_path, {"total_episodes": 1, "features": {}})
        assert not (tmp_path / "meta" / "episodes_stats.jsonl").exists()

    def test_computes_stats(self, tmp_path):
        (tmp_path / "meta").mkdir()
        data_dir = tmp_path / "data"
        _write_parquet(
            data_dir / "ep.parquet",
            {
                "episode_index": [0, 0, 1],
                "value": [1.0, 3.0, 5.0],
                "task_index": [0, 0, 0],
            },
        )
        info = {
            "total_episodes": 2,
            "features": {
                "value": {"dtype": "float32"},
                "task_index": {"dtype": "int64"},
                "cam": {"dtype": "video"},
            },
        }
        _MOD.ensure_episodes_stats(tmp_path, info)
        stats_lines = (tmp_path / "meta" / "episodes_stats.jsonl").read_text().strip().splitlines()
        records = [json.loads(line) for line in stats_lines]
        assert len(records) == 2
        assert records[0]["episode_index"] == 0
        assert "value" in records[0]["stats"]
        assert "cam" in records[0]["stats"]
        assert records[0]["stats"]["value"]["count"] == [2]


class TestVerifyFilePaths:
    def test_runs_with_missing_and_present(self, tmp_path, capsys):
        # data file present for ep 0 only
        data_dir = tmp_path / "data"
        (data_dir / "chunk-000").mkdir(parents=True)
        (data_dir / "chunk-000" / "episode_000000.parquet").write_bytes(b"x")

        videos_dir = tmp_path / "videos" / "cam" / "chunk-000"
        videos_dir.mkdir(parents=True)
        (videos_dir / "episode_000000.mp4").write_bytes(b"v")

        info = {
            "total_episodes": 6,
            "chunks_size": 1000,
            "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
            "video_path": "videos/{video_key}/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.mp4",
            "features": {"cam": {"dtype": "video"}},
        }
        _MOD._verify_file_paths(tmp_path, info)
        captured = capsys.readouterr().out
        assert "[verify] data_path template" in captured
        assert "MISSING data files" in captured
        assert "MISSING video files" in captured

    def test_runs_without_videos_dir(self, tmp_path, capsys):
        info = {
            "total_episodes": 1,
            "chunks_size": 1000,
            "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
            "video_path": "",
            "features": {},
        }
        _MOD._verify_file_paths(tmp_path, info)
        out = capsys.readouterr().out
        assert "video_keys: []" in out


class TestPrepareDataset:
    def test_exits_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("BLOB_URLS", raising=False)
        monkeypatch.delenv("DATASET_REPO_ID", raising=False)
        with pytest.raises(SystemExit) as exc:
            _MOD.prepare_dataset()
        assert exc.value.code == _MOD.EXIT_FAILURE

    def test_full_flow_no_info(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BLOB_URLS", '["https://acct.blob.core.windows.net/c/p"]')
        monkeypatch.setenv("DATASET_ROOT", str(tmp_path))
        monkeypatch.setenv("DATASET_REPO_ID", "u/d")

        staging_dir = tmp_path / ".staging" / "0"

        def fake_download(url, root, idx):
            staging_dir.mkdir(parents=True, exist_ok=True)
            (staging_dir / "marker.txt").write_text("x")
            return staging_dir

        monkeypatch.setattr(_MOD, "download_dataset_from_url", MagicMock(side_effect=fake_download))
        monkeypatch.setattr(_MOD, "verify_dataset", MagicMock(return_value=None))
        sentinel_calls = MagicMock()
        for name in (
            "patch_info_paths",
            "patch_image_stats",
            "fix_video_timestamps",
            "ensure_tasks_jsonl",
            "ensure_episodes_stats",
            "_verify_file_paths",
        ):
            monkeypatch.setattr(_MOD, name, sentinel_calls)

        result = _MOD.prepare_dataset()
        assert result == tmp_path / "u" / "d"
        # None info -> none of the patch helpers called
        sentinel_calls.assert_not_called()

    def test_full_flow_with_info(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BLOB_URLS", '["https://acct.blob.core.windows.net/c/p"]')
        monkeypatch.setenv("DATASET_REPO_ID", "u/d")
        monkeypatch.setenv("DATASET_ROOT", str(tmp_path))

        staging_dir = tmp_path / ".staging" / "0"

        def fake_download(url, root, idx):
            staging_dir.mkdir(parents=True, exist_ok=True)
            (staging_dir / "marker.txt").write_text("x")
            return staging_dir

        info = {"total_episodes": 0, "features": {}}
        monkeypatch.setattr(_MOD, "download_dataset_from_url", MagicMock(side_effect=fake_download))
        monkeypatch.setattr(_MOD, "verify_dataset", MagicMock(return_value=info))
        for name in (
            "patch_info_paths",
            "patch_image_stats",
            "fix_video_timestamps",
            "ensure_tasks_jsonl",
            "ensure_episodes_stats",
            "_verify_file_paths",
        ):
            monkeypatch.setattr(_MOD, name, MagicMock())

        result = _MOD.prepare_dataset()
        final = tmp_path / "u" / "d"
        assert result == final
        _MOD._verify_file_paths.assert_called_once()
        # Helpers receive the staged (hidden sibling) directory, not the final one
        called_dir = _MOD._verify_file_paths.call_args[0][0]
        assert called_dir == tmp_path / "u" / ".d.new"


class TestParseBlobUrl:
    def test_valid_url_with_prefix(self):
        account, container, prefix = _MOD.parse_blob_url(
            "https://myacct.blob.core.windows.net/mycontainer/myprefix/data"
        )
        assert account == "myacct"
        assert container == "mycontainer"
        assert prefix == "myprefix/data"

    def test_valid_url_without_prefix(self):
        account, container, prefix = _MOD.parse_blob_url("https://myacct.blob.core.windows.net/mycontainer")
        assert account == "myacct"
        assert container == "mycontainer"
        assert prefix == ""

    def test_valid_url_single_level_prefix(self):
        account, container, prefix = _MOD.parse_blob_url("https://acct.blob.core.windows.net/cont/p")
        assert account == "acct"
        assert container == "cont"
        assert prefix == "p"

    def test_invalid_url_no_scheme(self):
        with pytest.raises(ValueError, match="Invalid blob URL"):
            _MOD.parse_blob_url("http://acct.blob.core.windows.net/cont/p")

    def test_invalid_url_wrong_domain(self):
        with pytest.raises(ValueError, match="Invalid blob URL"):
            _MOD.parse_blob_url("https://acct.storage.azure.com/cont/p")

    def test_invalid_url_no_container(self):
        with pytest.raises(ValueError, match="Invalid blob URL"):
            _MOD.parse_blob_url("https://acct.blob.core.windows.net")

    def test_multipart_account_name(self):
        account, _container, _prefix = _MOD.parse_blob_url(
            "https://my-account-name.blob.core.windows.net/container/prefix"
        )
        assert account == "my-account-name"


class TestDownloadDatasetFromUrl:
    def test_parses_url_and_downloads(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            _MOD,
            "download_dataset",
            MagicMock(return_value=tmp_path / ".staging" / "0"),
        )
        result = _MOD.download_dataset_from_url(
            "https://acct.blob.core.windows.net/cont/prefix",
            str(tmp_path),
            0,
        )
        assert result == tmp_path / ".staging" / "0"
        _MOD.download_dataset.assert_called_once_with(
            storage_account="acct",
            storage_container="cont",
            blob_prefix="prefix",
            dataset_root=str(tmp_path),
            dataset_repo_id="0",
        )

    def test_creates_staging_directory(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_MOD, "download_dataset", MagicMock(return_value=tmp_path / ".staging" / "1"))
        _MOD.download_dataset_from_url(
            "https://acct.blob.core.windows.net/cont/p",
            str(tmp_path),
            1,
        )
        assert (tmp_path / ".staging" / "1").exists()


class TestMergeDatasets:
    def test_calls_lerobot_edit_dataset_with_correct_args(self, monkeypatch, tmp_path):
        staging_0 = tmp_path / ".staging" / "0"
        staging_1 = tmp_path / ".staging" / "1"
        staging_0.mkdir(parents=True)
        staging_1.mkdir(parents=True)
        destination = tmp_path / "out"

        def fake_run(cmd, **_kwargs):
            Path(cmd[cmd.index("--new_root") + 1]).mkdir(parents=True)
            return MagicMock(returncode=0)

        subprocess_mock = MagicMock(side_effect=fake_run)
        monkeypatch.setattr("subprocess.run", subprocess_mock)

        _MOD.merge_datasets([staging_0, staging_1], destination)

        assert subprocess_mock.call_count == 1
        call_args = subprocess_mock.call_args[0][0]
        assert call_args[0] == "lerobot-edit-dataset"
        assert call_args[call_args.index("--new_repo_id") + 1] == "merged"
        assert call_args[call_args.index("--operation.type") + 1] == "merge"
        assert "--operation.repo_ids" in call_args
        assert "--operation.roots" in call_args
        assert call_args[call_args.index("--new_root") + 1] == str(destination)

    def test_raises_on_merge_failure(self, monkeypatch, tmp_path):
        staging_0 = tmp_path / ".staging" / "0"
        staging_0.mkdir(parents=True)

        subprocess_mock = MagicMock(return_value=MagicMock(returncode=1))
        monkeypatch.setattr("subprocess.run", subprocess_mock)

        with pytest.raises(RuntimeError, match="Dataset merge failed"):
            _MOD.merge_datasets([staging_0], tmp_path / "out")

    def test_raises_when_destination_missing_after_run(self, monkeypatch, tmp_path):
        staging_0 = tmp_path / ".staging" / "0"
        staging_0.mkdir(parents=True)

        # subprocess returns success but does not create the directory
        subprocess_mock = MagicMock(return_value=MagicMock(returncode=0))
        monkeypatch.setattr("subprocess.run", subprocess_mock)

        with pytest.raises(RuntimeError, match="did not create"):
            _MOD.merge_datasets([staging_0], tmp_path / "out")

    def test_self_cleans_stale_destination(self, monkeypatch, tmp_path):
        staging_0 = tmp_path / ".staging" / "0"
        staging_0.mkdir(parents=True)
        destination = tmp_path / "out"
        destination.mkdir()
        (destination / "stale.txt").write_text("old")

        def fake_run(cmd, **_kwargs):
            new_root = Path(cmd[cmd.index("--new_root") + 1])
            assert not new_root.exists(), "merge_datasets must clean destination before invoking lerobot"
            new_root.mkdir(parents=True)
            return MagicMock(returncode=0)

        monkeypatch.setattr("subprocess.run", MagicMock(side_effect=fake_run))

        _MOD.merge_datasets([staging_0], destination)
        assert not (destination / "stale.txt").exists()


class TestMultiBlobFlow:
    def test_multiple_blobs_triggers_merge(self, monkeypatch, tmp_path):
        monkeypatch.setenv(
            "BLOB_URLS", '["https://a.blob.core.windows.net/c1/p1", "https://b.blob.core.windows.net/c2/p2"]'
        )
        monkeypatch.setenv("DATASET_REPO_ID", "merged")
        monkeypatch.setenv("DATASET_ROOT", str(tmp_path))

        # Create staging directories with data
        staging_0 = tmp_path / ".staging" / "0"
        staging_1 = tmp_path / ".staging" / "1"
        staging_0.mkdir(parents=True)
        staging_1.mkdir(parents=True)
        (staging_0 / "data.txt").write_text("data0")
        (staging_1 / "data.txt").write_text("data1")

        download_mock = MagicMock(side_effect=lambda url, root, idx: [staging_0, staging_1][idx])

        def fake_merge(sources, destination):
            destination.mkdir(parents=True)

        verify_mock = MagicMock(return_value=None)

        monkeypatch.setattr(_MOD, "download_dataset_from_url", download_mock)
        monkeypatch.setattr(_MOD, "merge_datasets", MagicMock(side_effect=fake_merge))
        monkeypatch.setattr(_MOD, "verify_dataset", verify_mock)
        for name in (
            "patch_info_paths",
            "patch_image_stats",
            "fix_video_timestamps",
            "ensure_tasks_jsonl",
            "ensure_episodes_stats",
            "_verify_file_paths",
        ):
            monkeypatch.setattr(_MOD, name, MagicMock())

        _MOD.prepare_dataset()

        # Verify both URLs were downloaded
        assert download_mock.call_count == 2
        # Verify merge was called
        _MOD.merge_datasets.assert_called_once()
        merge_sources = _MOD.merge_datasets.call_args[0][0]
        assert len(merge_sources) == 2

    def test_single_blob_skips_merge(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BLOB_URLS", '["https://a.blob.core.windows.net/c/p"]')
        monkeypatch.setenv("DATASET_REPO_ID", "single")
        monkeypatch.setenv("DATASET_ROOT", str(tmp_path))

        staging_0 = tmp_path / ".staging" / "0"
        staging_0.mkdir(parents=True)

        download_mock = MagicMock(return_value=staging_0)
        merge_mock = MagicMock()
        verify_mock = MagicMock(return_value=None)

        monkeypatch.setattr(_MOD, "download_dataset_from_url", download_mock)
        monkeypatch.setattr(_MOD, "merge_datasets", merge_mock)
        monkeypatch.setattr(_MOD, "verify_dataset", verify_mock)
        for name in (
            "patch_info_paths",
            "patch_image_stats",
            "fix_video_timestamps",
            "ensure_tasks_jsonl",
            "ensure_episodes_stats",
            "_verify_file_paths",
        ):
            monkeypatch.setattr(_MOD, name, MagicMock())

        _MOD.prepare_dataset()

        download_mock.assert_called_once()
        merge_mock.assert_not_called()

    def test_merge_destination_is_staged_sibling_of_final(self, monkeypatch, tmp_path):
        """Merge writes to ``<final>.new`` (hidden sibling of final). A crash
        during verification/patching cannot touch the final location."""
        monkeypatch.setenv(
            "BLOB_URLS",
            '["https://a.blob.core.windows.net/c1/p1", "https://b.blob.core.windows.net/c2/p2"]',
        )
        monkeypatch.setenv("DATASET_REPO_ID", "isolation_check")
        monkeypatch.setenv("DATASET_ROOT", str(tmp_path))

        staging_0 = tmp_path / ".staging" / "0"
        staging_1 = tmp_path / ".staging" / "1"
        staging_0.mkdir(parents=True)
        staging_1.mkdir(parents=True)

        observed = {}

        def fake_merge(sources, destination):
            observed["destination"] = destination
            destination.mkdir(parents=True)

        download_mock = MagicMock(side_effect=lambda url, root, idx: [staging_0, staging_1][idx])
        monkeypatch.setattr(_MOD, "download_dataset_from_url", download_mock)
        monkeypatch.setattr(_MOD, "merge_datasets", fake_merge)
        monkeypatch.setattr(_MOD, "verify_dataset", MagicMock(return_value=None))
        for name in (
            "patch_info_paths",
            "patch_image_stats",
            "fix_video_timestamps",
            "ensure_tasks_jsonl",
            "ensure_episodes_stats",
            "_verify_file_paths",
        ):
            monkeypatch.setattr(_MOD, name, MagicMock())

        _MOD.prepare_dataset()

        assert observed["destination"] == tmp_path / ".isolation_check.new"

    def test_hierarchical_repo_id_publishes_to_nested_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BLOB_URLS", '["https://a.blob.core.windows.net/c/p"]')
        monkeypatch.setenv("DATASET_REPO_ID", "user/dataset")
        monkeypatch.setenv("DATASET_ROOT", str(tmp_path))

        staging_0 = tmp_path / ".staging" / "0"
        staging_0.mkdir(parents=True)
        (staging_0 / "data.txt").write_text("x")

        monkeypatch.setattr(_MOD, "download_dataset_from_url", MagicMock(return_value=staging_0))
        monkeypatch.setattr(_MOD, "verify_dataset", MagicMock(return_value=None))
        for name in (
            "patch_info_paths",
            "patch_image_stats",
            "fix_video_timestamps",
            "ensure_tasks_jsonl",
            "ensure_episodes_stats",
            "_verify_file_paths",
        ):
            monkeypatch.setattr(_MOD, name, MagicMock())

        result = _MOD.prepare_dataset()

        assert result == tmp_path / "user" / "dataset"
        assert (tmp_path / "user" / "dataset" / "data.txt").read_text() == "x"
