"""Tests for training/il/scripts/lerobot/download_dataset.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from conftest import load_training_module


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
        monkeypatch.delenv("STORAGE_ACCOUNT", raising=False)
        monkeypatch.delenv("BLOB_PREFIX", raising=False)
        monkeypatch.delenv("DATASET_REPO_ID", raising=False)
        with pytest.raises(SystemExit) as exc:
            _MOD.prepare_dataset()
        assert exc.value.code == _MOD.EXIT_FAILURE

    def test_full_flow_no_info(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STORAGE_ACCOUNT", "acct")
        monkeypatch.setenv("STORAGE_CONTAINER", "c")
        monkeypatch.setenv("BLOB_PREFIX", "p")
        monkeypatch.setenv("DATASET_ROOT", str(tmp_path))
        monkeypatch.setenv("DATASET_REPO_ID", "u/d")

        download_dir = tmp_path / "u" / "d"
        monkeypatch.setattr(_MOD, "download_dataset", MagicMock(return_value=download_dir))
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
        assert result == download_dir
        # None info -> none of the patch helpers called
        sentinel_calls.assert_not_called()

    def test_full_flow_with_info(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STORAGE_ACCOUNT", "acct")
        monkeypatch.setenv("BLOB_PREFIX", "p")
        monkeypatch.setenv("DATASET_REPO_ID", "u/d")
        monkeypatch.delenv("STORAGE_CONTAINER", raising=False)
        monkeypatch.delenv("DATASET_ROOT", raising=False)

        info = {"total_episodes": 0, "features": {}}
        monkeypatch.setattr(_MOD, "download_dataset", MagicMock(return_value=tmp_path))
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

        assert _MOD.prepare_dataset() == tmp_path
        _MOD.patch_info_paths.assert_called_once_with(tmp_path, info)
        _MOD._verify_file_paths.assert_called_once_with(tmp_path, info)
