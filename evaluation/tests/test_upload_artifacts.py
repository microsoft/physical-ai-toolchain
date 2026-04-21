"""Unit tests for ``metrics.upload_artifacts``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from metrics.upload_artifacts import (
    get_video_search_paths,
    load_metrics,
    main,
    upload_to_blob_fallback,
    upload_to_mlflow,
)


class TestLoadMetrics:
    def test_both_files_exist(self, tmp_path: Path) -> None:
        onnx_payload = {"latency_ms": 12.3}
        jit_payload = {"latency_ms": 45.6}
        (tmp_path / "onnx_metrics.json").write_text(json.dumps(onnx_payload))
        (tmp_path / "jit_metrics.json").write_text(json.dumps(jit_payload))

        onnx, jit = load_metrics(tmp_path)

        assert onnx == onnx_payload
        assert jit == jit_payload

    def test_only_onnx(self, tmp_path: Path) -> None:
        (tmp_path / "onnx_metrics.json").write_text(json.dumps({"a": 1}))

        onnx, jit = load_metrics(tmp_path)

        assert onnx == {"a": 1}
        assert jit == {}

    def test_only_jit(self, tmp_path: Path) -> None:
        (tmp_path / "jit_metrics.json").write_text(json.dumps({"b": 2}))

        onnx, jit = load_metrics(tmp_path)

        assert onnx == {}
        assert jit == {"b": 2}

    def test_neither_exists(self, tmp_path: Path) -> None:
        onnx, jit = load_metrics(tmp_path)

        assert onnx == {}
        assert jit == {}


class TestGetVideoSearchPaths:
    def test_returns_five_paths(self, tmp_path: Path) -> None:
        paths = get_video_search_paths(tmp_path / "export")
        assert len(paths) == 5
        assert all(isinstance(p, Path) for p in paths)

    def test_first_path_is_relative(self, tmp_path: Path) -> None:
        export_dir = tmp_path / "export"
        paths = get_video_search_paths(export_dir)
        assert paths[0] == export_dir / "videos"

    def test_parent_videos_path(self, tmp_path: Path) -> None:
        export_dir = tmp_path / "nested" / "export"
        paths = get_video_search_paths(export_dir)
        assert paths[1] == tmp_path / "nested" / "videos"


class TestUploadToMlflow:
    @staticmethod
    def _inject_mock_modules(monkeypatch):
        """Inject mock mlflow and training.utils into sys.modules."""
        mock_mlflow = MagicMock()
        mock_run = MagicMock()
        mock_run.info.run_id = "run-123"
        mock_mlflow.start_run.return_value.__enter__ = MagicMock(return_value=mock_run)
        mock_mlflow.start_run.return_value.__exit__ = MagicMock(return_value=False)

        config_error = type("AzureConfigError", (RuntimeError,), {})
        mock_utils = MagicMock()
        mock_utils.AzureConfigError = config_error

        context = MagicMock()
        context.workspace_name = "ws"
        context.tracking_uri = "https://tracking"
        context.storage = None
        mock_utils.bootstrap_azure_ml.return_value = context

        monkeypatch.setitem(sys.modules, "mlflow", mock_mlflow)
        monkeypatch.setitem(sys.modules, "training", MagicMock())
        monkeypatch.setitem(sys.modules, "training.utils", mock_utils)

        return mock_mlflow, mock_utils, config_error

    def test_import_error_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "mlflow", None)
        result = upload_to_mlflow(
            task="t",
            export_dir=tmp_path,
            metrics_dir=tmp_path,
            checkpoint_uri="",
            onnx_success=False,
            jit_success=False,
            onnx_metrics={},
            jit_metrics={},
            timestamp="20240101_000000",
        )
        assert result is False

    def test_azure_config_error_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        _, mock_utils, config_error = self._inject_mock_modules(monkeypatch)
        mock_utils.bootstrap_azure_ml.side_effect = config_error("nope")
        result = upload_to_mlflow(
            task="t",
            export_dir=tmp_path,
            metrics_dir=tmp_path,
            checkpoint_uri="",
            onnx_success=False,
            jit_success=False,
            onnx_metrics={},
            jit_metrics={},
            timestamp="20240101_000000",
        )
        assert result is False

    def test_connection_error_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        self._inject_mock_modules(monkeypatch)
        sys.modules["training.utils"].bootstrap_azure_ml.side_effect = ConnectionError("refused")
        result = upload_to_mlflow(
            task="t",
            export_dir=tmp_path,
            metrics_dir=tmp_path,
            checkpoint_uri="",
            onnx_success=False,
            jit_success=False,
            onnx_metrics={},
            jit_metrics={},
            timestamp="20240101_000000",
        )
        assert result is False

    def test_success_returns_true(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NUM_ENVS", "4")
        monkeypatch.setenv("MAX_STEPS", "500")
        monkeypatch.setenv("VIDEO_LENGTH", "200")
        monkeypatch.setenv("INFERENCE_FORMAT", "both")

        mock_mlflow, _, _ = self._inject_mock_modules(monkeypatch)
        result = upload_to_mlflow(
            task="t",
            export_dir=tmp_path,
            metrics_dir=tmp_path,
            checkpoint_uri="uri",
            onnx_success=True,
            jit_success=False,
            onnx_metrics={},
            jit_metrics={},
            timestamp="20240101_000000",
        )
        assert result is True
        mock_mlflow.start_run.assert_called_once()
        mock_mlflow.set_tags.assert_called_once()
        mock_mlflow.log_params.assert_called_once()

    def test_onnx_metrics_logged(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NUM_ENVS", "4")
        monkeypatch.setenv("MAX_STEPS", "500")
        monkeypatch.setenv("VIDEO_LENGTH", "200")
        monkeypatch.setenv("INFERENCE_FORMAT", "onnx")

        mock_mlflow, _, _ = self._inject_mock_modules(monkeypatch)
        upload_to_mlflow(
            task="t",
            export_dir=tmp_path,
            metrics_dir=tmp_path,
            checkpoint_uri="uri",
            onnx_success=True,
            jit_success=False,
            onnx_metrics={"mean_episode_reward": 5.0, "total_episodes": 10},
            jit_metrics={},
            timestamp="20240101_000000",
        )
        assert mock_mlflow.log_metrics.call_count >= 2
        mock_mlflow.log_artifact.assert_called()

    def test_storage_upload_called(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NUM_ENVS", "4")
        monkeypatch.setenv("MAX_STEPS", "500")
        monkeypatch.setenv("VIDEO_LENGTH", "200")
        monkeypatch.setenv("INFERENCE_FORMAT", "both")

        _, mock_utils, _ = self._inject_mock_modules(monkeypatch)
        mock_storage = MagicMock()
        mock_utils.bootstrap_azure_ml.return_value.storage = mock_storage

        (tmp_path / "policy.onnx").write_bytes(b"data")
        upload_to_mlflow(
            task="t",
            export_dir=tmp_path,
            metrics_dir=tmp_path,
            checkpoint_uri="uri",
            onnx_success=True,
            jit_success=False,
            onnx_metrics={},
            jit_metrics={},
            timestamp="20240101_000000",
        )
        mock_storage.upload_files_batch.assert_called_once()

    def test_storage_no_files_to_upload(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NUM_ENVS", "4")
        monkeypatch.setenv("MAX_STEPS", "500")
        monkeypatch.setenv("VIDEO_LENGTH", "200")
        monkeypatch.setenv("INFERENCE_FORMAT", "both")

        _, mock_utils, _ = self._inject_mock_modules(monkeypatch)
        mock_storage = MagicMock()
        mock_utils.bootstrap_azure_ml.return_value.storage = mock_storage

        export_dir = tmp_path / "empty_export"
        export_dir.mkdir()
        result = upload_to_mlflow(
            task="t",
            export_dir=export_dir,
            metrics_dir=tmp_path,
            checkpoint_uri="uri",
            onnx_success=False,
            jit_success=False,
            onnx_metrics={},
            jit_metrics={},
            timestamp="20240101_000000",
        )
        assert result is True
        mock_storage.upload_files_batch.assert_not_called()

    def test_jit_metrics_logged(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NUM_ENVS", "4")
        monkeypatch.setenv("MAX_STEPS", "500")
        monkeypatch.setenv("VIDEO_LENGTH", "200")
        monkeypatch.setenv("INFERENCE_FORMAT", "jit")

        mock_mlflow, _, _ = self._inject_mock_modules(monkeypatch)
        upload_to_mlflow(
            task="t",
            export_dir=tmp_path,
            metrics_dir=tmp_path,
            checkpoint_uri="uri",
            onnx_success=False,
            jit_success=True,
            onnx_metrics={},
            jit_metrics={"mean_episode_reward": 7.0, "total_episodes": 5},
            timestamp="20240101_000000",
        )

        logged_keys = set()
        for call in mock_mlflow.log_metrics.call_args_list:
            logged_keys.update(call[0][0])
        assert "jit/mean_episode_reward" in logged_keys
        mock_mlflow.log_artifact.assert_called()

    def test_video_path_classification(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NUM_ENVS", "4")
        monkeypatch.setenv("MAX_STEPS", "500")
        monkeypatch.setenv("VIDEO_LENGTH", "200")
        monkeypatch.setenv("INFERENCE_FORMAT", "both")

        export_dir = tmp_path / "export"
        videos_dir = export_dir / "videos"
        videos_dir.mkdir(parents=True)
        (videos_dir / "onnx_run.mp4").write_bytes(b"\x00")
        (videos_dir / "jit_run.mp4").write_bytes(b"\x00")
        (videos_dir / "general_run.mp4").write_bytes(b"\x00")

        mock_mlflow, _, _ = self._inject_mock_modules(monkeypatch)
        upload_to_mlflow(
            task="t",
            export_dir=export_dir,
            metrics_dir=tmp_path,
            checkpoint_uri="uri",
            onnx_success=True,
            jit_success=True,
            onnx_metrics={},
            jit_metrics={},
            timestamp="20240101_000000",
        )

        artifact_paths = [c.kwargs["artifact_path"] for c in mock_mlflow.log_artifact.call_args_list]
        assert "videos/onnx" in artifact_paths
        assert "videos/jit" in artifact_paths
        assert "videos" in artifact_paths

    def test_storage_upload_includes_videos(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NUM_ENVS", "4")
        monkeypatch.setenv("MAX_STEPS", "500")
        monkeypatch.setenv("VIDEO_LENGTH", "200")
        monkeypatch.setenv("INFERENCE_FORMAT", "both")

        _, mock_utils, _ = self._inject_mock_modules(monkeypatch)
        mock_storage = MagicMock()
        mock_utils.bootstrap_azure_ml.return_value.storage = mock_storage

        export_dir = tmp_path / "export"
        export_dir.mkdir()
        (export_dir / "policy.onnx").write_bytes(b"data")
        videos_dir = export_dir / "videos"
        videos_dir.mkdir()
        (videos_dir / "test.mp4").write_bytes(b"\x00")

        upload_to_mlflow(
            task="t",
            export_dir=export_dir,
            metrics_dir=tmp_path,
            checkpoint_uri="uri",
            onnx_success=True,
            jit_success=False,
            onnx_metrics={},
            jit_metrics={},
            timestamp="20240101_000000",
        )

        files = mock_storage.upload_files_batch.call_args[0][0]
        blob_names = [f[1] for f in files]
        assert any("models/policy.onnx" in n for n in blob_names)
        assert any("videos/test.mp4" in n for n in blob_names)

    def test_mlflow_run_exception_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NUM_ENVS", "4")
        monkeypatch.setenv("MAX_STEPS", "500")
        monkeypatch.setenv("VIDEO_LENGTH", "200")
        monkeypatch.setenv("INFERENCE_FORMAT", "both")

        mock_mlflow, _, _ = self._inject_mock_modules(monkeypatch)
        mock_mlflow.start_run.side_effect = RuntimeError("boom")

        result = upload_to_mlflow(
            task="t",
            export_dir=tmp_path,
            metrics_dir=tmp_path,
            checkpoint_uri="uri",
            onnx_success=True,
            jit_success=False,
            onnx_metrics={},
            jit_metrics={},
            timestamp="20240101_000000",
        )
        assert result is False


class TestUploadToBlobFallback:
    @staticmethod
    def _inject_azure_mocks(monkeypatch):
        """Inject mock azure.identity and azure.storage.blob into sys.modules."""
        mock_credential = MagicMock()
        mock_identity = MagicMock()
        mock_identity.DefaultAzureCredential.return_value = mock_credential

        mock_container = MagicMock()
        mock_blob_service = MagicMock()
        mock_blob_service.get_container_client.return_value = mock_container

        mock_blob = MagicMock()
        mock_blob.BlobServiceClient.return_value = mock_blob_service

        monkeypatch.setitem(sys.modules, "azure", MagicMock())
        monkeypatch.setitem(sys.modules, "azure.identity", mock_identity)
        monkeypatch.setitem(sys.modules, "azure.storage", MagicMock())
        monkeypatch.setitem(sys.modules, "azure.storage.blob", mock_blob)

        return mock_blob, mock_container

    def test_no_storage_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
        result = upload_to_blob_fallback(
            task="t",
            export_dir=tmp_path,
            blob_account="",
            blob_container="",
            checkpoint_uri="",
            timestamp="20240101_000000",
        )
        assert result is False

    def test_import_error_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "azure", None)
        monkeypatch.setitem(sys.modules, "azure.identity", None)
        monkeypatch.setitem(sys.modules, "azure.storage", None)
        monkeypatch.setitem(sys.modules, "azure.storage.blob", None)
        result = upload_to_blob_fallback(
            task="t",
            export_dir=tmp_path,
            blob_account="myaccount",
            blob_container="mycontainer",
            checkpoint_uri="",
            timestamp="20240101_000000",
        )
        assert result is False

    def test_url_parsing_extracts_account(self, tmp_path: Path, monkeypatch) -> None:
        mock_blob, _ = self._inject_azure_mocks(monkeypatch)
        upload_to_blob_fallback(
            task="t",
            export_dir=tmp_path,
            blob_account="",
            blob_container="",
            checkpoint_uri="https://myaccount.blob.core.windows.net/mycontainer/path/model.pt",
            timestamp="20240101_000000",
        )
        call_args = str(mock_blob.BlobServiceClient.call_args)
        assert "myaccount" in call_args

    def test_https_url_non_blob_falls_through(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
        monkeypatch.delenv("AZURE_STORAGE_CONTAINER_NAME", raising=False)
        result = upload_to_blob_fallback(
            task="t",
            export_dir=tmp_path,
            blob_account="",
            blob_container="",
            checkpoint_uri="https://example.com/not-a-blob/file",
            timestamp="20240101_000000",
        )
        assert result is False

    def test_env_var_fallback(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "envaccount")
        monkeypatch.setenv("AZURE_STORAGE_CONTAINER_NAME", "envcontainer")
        mock_blob, _ = self._inject_azure_mocks(monkeypatch)
        upload_to_blob_fallback(
            task="t",
            export_dir=tmp_path,
            blob_account="",
            blob_container="",
            checkpoint_uri="",
            timestamp="20240101_000000",
        )
        call_args = str(mock_blob.BlobServiceClient.call_args)
        assert "envaccount" in call_args

    def test_success_with_policy_files(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "policy.onnx").write_bytes(b"model-data")
        _, mock_container = self._inject_azure_mocks(monkeypatch)
        result = upload_to_blob_fallback(
            task="t",
            export_dir=tmp_path,
            blob_account="acct",
            blob_container="ctr",
            checkpoint_uri="",
            timestamp="20240101_000000",
        )
        assert result is True
        mock_container.upload_blob.assert_called()

    def test_no_files_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        self._inject_azure_mocks(monkeypatch)
        result = upload_to_blob_fallback(
            task="t",
            export_dir=tmp_path,
            blob_account="acct",
            blob_container="ctr",
            checkpoint_uri="",
            timestamp="20240101_000000",
        )
        assert result is False

    def test_per_file_upload_exception_continues(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "policy.onnx").write_bytes(b"model-onnx")
        (tmp_path / "policy.jit").write_bytes(b"model-jit")
        _, mock_container = self._inject_azure_mocks(monkeypatch)
        mock_container.upload_blob.side_effect = [Exception("fail"), None]

        result = upload_to_blob_fallback(
            task="t",
            export_dir=tmp_path,
            blob_account="acct",
            blob_container="ctr",
            checkpoint_uri="",
            timestamp="20240101_000000",
        )
        assert result is True
        assert mock_container.upload_blob.call_count == 2

    def test_video_upload_success_and_exception(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "policy.onnx").write_bytes(b"model-data")
        videos_dir = tmp_path / "videos"
        videos_dir.mkdir()
        (videos_dir / "ep1.mp4").write_bytes(b"video-1")
        (videos_dir / "ep2.mp4").write_bytes(b"video-2")
        _, mock_container = self._inject_azure_mocks(monkeypatch)

        # First call (model) succeeds, then one video succeeds and one raises.
        mock_container.upload_blob.side_effect = [None, None, Exception("video-fail")]

        result = upload_to_blob_fallback(
            task="t",
            export_dir=tmp_path,
            blob_account="acct",
            blob_container="ctr",
            checkpoint_uri="",
            timestamp="20240101_000000",
        )
        assert result is True
        # 1 model upload + 2 video upload attempts.
        assert mock_container.upload_blob.call_count == 3
        blob_names = [call.kwargs.get("name", "") for call in mock_container.upload_blob.call_args_list]
        assert any("videos/ep1.mp4" in name for name in blob_names)
        assert any("videos/ep2.mp4" in name for name in blob_names)

    def test_credential_exception_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        mock_identity = MagicMock()
        mock_identity.DefaultAzureCredential.side_effect = Exception("auth failed")
        monkeypatch.setitem(sys.modules, "azure", MagicMock())
        monkeypatch.setitem(sys.modules, "azure.identity", mock_identity)
        monkeypatch.setitem(sys.modules, "azure.storage", MagicMock())
        monkeypatch.setitem(sys.modules, "azure.storage.blob", MagicMock())

        result = upload_to_blob_fallback(
            task="t",
            export_dir=tmp_path,
            blob_account="acct",
            blob_container="ctr",
            checkpoint_uri="",
            timestamp="20240101_000000",
        )
        assert result is False


class TestMain:
    """Tests for the main() entry point."""

    @staticmethod
    def _env_vars(tmp_path: Path) -> dict[str, str]:
        return {
            "TASK": "pick_place",
            "EXPORT_DIR": str(tmp_path / "exported"),
            "METRICS_DIR": str(tmp_path / "metrics"),
            "ONNX_SUCCESS": "1",
            "JIT_SUCCESS": "0",
            "NUM_ENVS": "4",
            "MAX_STEPS": "500",
            "VIDEO_LENGTH": "200",
            "INFERENCE_FORMAT": "both",
            "CHECKPOINT_URI": "https://example.com/model.pt",
            "BLOB_STORAGE_ACCOUNT": "acct",
            "BLOB_CONTAINER": "ctr",
        }

    def test_mlflow_success_skips_blob_fallback(self, tmp_path: Path, monkeypatch) -> None:
        mock_set_defaults = MagicMock()
        monkeypatch.setitem(sys.modules, "training", MagicMock())
        monkeypatch.setitem(sys.modules, "training.utils", MagicMock(set_env_defaults=mock_set_defaults))

        for key, val in self._env_vars(tmp_path).items():
            monkeypatch.setenv(key, val)

        with (
            patch("metrics.upload_artifacts.load_metrics", return_value=({}, {})) as mock_load,
            patch("metrics.upload_artifacts.upload_to_mlflow", return_value=True) as mock_mlflow,
            patch("metrics.upload_artifacts.upload_to_blob_fallback") as mock_blob,
        ):
            main()

            mock_set_defaults.assert_called_once()
            mock_load.assert_called_once()
            mock_mlflow.assert_called_once()
            mock_blob.assert_not_called()

    def test_mlflow_failure_triggers_blob_fallback(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "training", MagicMock())
        monkeypatch.setitem(sys.modules, "training.utils", MagicMock(set_env_defaults=MagicMock()))

        for key, val in self._env_vars(tmp_path).items():
            monkeypatch.setenv(key, val)

        with (
            patch("metrics.upload_artifacts.load_metrics", return_value=({}, {})),
            patch("metrics.upload_artifacts.upload_to_mlflow", return_value=False),
            patch("metrics.upload_artifacts.upload_to_blob_fallback") as mock_blob,
        ):
            main()

            mock_blob.assert_called_once()
            call_kwargs = mock_blob.call_args[1]
            assert call_kwargs["task"] == "pick_place"
            assert call_kwargs["blob_account"] == "acct"
            assert call_kwargs["blob_container"] == "ctr"

    def test_env_defaults_passed_correctly(self, tmp_path: Path, monkeypatch) -> None:
        mock_set_defaults = MagicMock()
        monkeypatch.setitem(sys.modules, "training", MagicMock())
        monkeypatch.setitem(sys.modules, "training.utils", MagicMock(set_env_defaults=mock_set_defaults))

        for key, val in self._env_vars(tmp_path).items():
            monkeypatch.setenv(key, val)

        with (
            patch("metrics.upload_artifacts.load_metrics", return_value=({}, {})),
            patch("metrics.upload_artifacts.upload_to_mlflow", return_value=True),
        ):
            main()

            defaults = mock_set_defaults.call_args[0][0]
            assert defaults["TASK"] == "unknown"
            assert defaults["EXPORT_DIR"] == "/tmp/exported"
            assert defaults["NUM_ENVS"] == "4"

    def test_onnx_and_jit_flags_parsed(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "training", MagicMock())
        monkeypatch.setitem(sys.modules, "training.utils", MagicMock(set_env_defaults=MagicMock()))

        env = self._env_vars(tmp_path)
        env["ONNX_SUCCESS"] = "1"
        env["JIT_SUCCESS"] = "1"
        for key, val in env.items():
            monkeypatch.setenv(key, val)

        with (
            patch("metrics.upload_artifacts.load_metrics", return_value=({"a": 1}, {"b": 2})),
            patch("metrics.upload_artifacts.upload_to_mlflow", return_value=True) as mock_mlflow,
        ):
            main()

            call_kwargs = mock_mlflow.call_args[1]
            assert call_kwargs["onnx_success"] is True
            assert call_kwargs["jit_success"] is True
            assert call_kwargs["onnx_metrics"] == {"a": 1}
            assert call_kwargs["jit_metrics"] == {"b": 2}

    def test_optional_env_vars_default_to_empty(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "training", MagicMock())
        monkeypatch.setitem(sys.modules, "training.utils", MagicMock(set_env_defaults=MagicMock()))

        env = self._env_vars(tmp_path)
        del env["CHECKPOINT_URI"]
        del env["BLOB_STORAGE_ACCOUNT"]
        del env["BLOB_CONTAINER"]
        del env["METRICS_DIR"]
        monkeypatch.delenv("CHECKPOINT_URI", raising=False)
        monkeypatch.delenv("BLOB_STORAGE_ACCOUNT", raising=False)
        monkeypatch.delenv("BLOB_CONTAINER", raising=False)
        monkeypatch.delenv("METRICS_DIR", raising=False)
        for key, val in env.items():
            monkeypatch.setenv(key, val)

        with (
            patch("metrics.upload_artifacts.load_metrics", return_value=({}, {})),
            patch("metrics.upload_artifacts.upload_to_mlflow", return_value=False),
            patch("metrics.upload_artifacts.upload_to_blob_fallback") as mock_blob,
        ):
            main()

            call_kwargs = mock_blob.call_args[1]
            assert call_kwargs["checkpoint_uri"] == ""
            assert call_kwargs["blob_account"] == ""
            assert call_kwargs["blob_container"] == ""
