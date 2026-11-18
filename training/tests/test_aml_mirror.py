from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from conftest import load_training_module

_fake_azure = ModuleType("azure")
_fake_ai = ModuleType("azure.ai")
_fake_ai_ml = ModuleType("azure.ai.ml")
_fake_identity = ModuleType("azure.identity")


class _FakeMLClient:
    def __init__(self, **_: object) -> None:
        self.workspaces = SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                name="ws-test",
                location="eastus",
                mlflow_tracking_uri="azureml://tracking",
            )
        )


_fake_ai_ml.MLClient = _FakeMLClient
_fake_identity.DefaultAzureCredential = MagicMock

_fake_azure.ai = _fake_ai
_fake_azure.identity = _fake_identity
sys.modules.setdefault("azure", _fake_azure)
sys.modules.setdefault("azure.ai", _fake_ai)
sys.modules.setdefault("azure.ai.ml", _fake_ai_ml)
sys.modules.setdefault("azure.identity", _fake_identity)

_fake_mlflow = MagicMock(name="mlflow")
_fake_registry_client = MagicMock(name="registry_client")


class _FakeMlflowException(Exception):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class _FakeRun:
    def __init__(self) -> None:
        self.info = SimpleNamespace(run_id="run-abc")

    def __enter__(self) -> _FakeRun:
        return self

    def __exit__(self, *_: object) -> None:
        return None


_fake_mlflow.start_run.return_value = _FakeRun()
_fake_mlflow.MlflowClient.return_value = _fake_registry_client
_fake_mlflow.exceptions.MlflowException = _FakeMlflowException
_fake_mlflow.get_artifact_uri.return_value = "azureml://artifacts/origin/container/model"
_fake_registry_client.create_model_version.return_value = SimpleNamespace(name="model-x", version="3")
sys.modules.setdefault("mlflow", _fake_mlflow)

_MOD = load_training_module("training_utils_aml_mirror", "training/utils/aml_mirror.py")


@pytest.fixture(autouse=True)
def _reset_mlflow() -> None:
    _fake_mlflow.reset_mock()
    _fake_registry_client.reset_mock()
    _fake_mlflow.start_run.return_value = _FakeRun()
    _fake_mlflow.MlflowClient.return_value = _fake_registry_client
    _fake_mlflow.get_artifact_uri.return_value = "azureml://artifacts/origin/container/model"
    _fake_registry_client.create_registered_model.side_effect = None
    _fake_registry_client.create_model_version.return_value = SimpleNamespace(name="model-x", version="3")


@pytest.fixture()
def _env_vars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-1")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-1")
    monkeypatch.setenv("AZUREML_WORKSPACE_NAME", "ws-test")
    monkeypatch.setenv("AZUREML_MODEL_NAME", "my-model")
    monkeypatch.setenv("RUN_ID", "run-42")
    monkeypatch.setenv("OUTPUT_DIR", str(output))
    return output


class TestLogTensorboardMetrics:
    def test_tbparse_not_installed_returns_zero(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)
        import builtins

        real_import = builtins.__import__

        def reject_tbparse(name: str, *args: object, **kwargs: object) -> object:
            if name == "tbparse":
                raise ImportError("no tbparse")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", reject_tbparse)
        assert _MOD._log_tensorboard_metrics(tmp_path) == 0

    def test_empty_scalars_returns_zero(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        fake_df = SimpleNamespace(empty=True)
        fake_reader = SimpleNamespace(scalars=fake_df)
        fake_tbparse = ModuleType("tbparse")
        fake_tbparse.SummaryReader = lambda _path: fake_reader
        monkeypatch.setitem(sys.modules, "tbparse", fake_tbparse)
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)
        assert _MOD._log_tensorboard_metrics(tmp_path) == 0

    def test_logs_metrics_for_each_scalar(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        rows = [
            {"tag": "loss", "step": 1, "value": 0.5},
            {"tag": "loss", "step": 2, "value": 0.3},
            {"tag": "reward", "step": 1, "value": 1.0},
        ]

        class _FakeScalars:
            empty = False

            def __getitem__(self, key: str) -> _FakeScalars:
                self._key = key
                return self

            def __eq__(self, other: object) -> _FakeScalars:
                self._filter_value = other
                return self

            def unique(self) -> list[str]:
                return ["loss", "reward"]

            def sort_values(self, _by: str) -> _FakeScalars:
                return self

            def iterrows(self) -> list[tuple[int, dict[str, object]]]:
                return [(i, r) for i, r in enumerate(rows) if r["tag"] == self._filter_value]

        fake_reader = SimpleNamespace(scalars=_FakeScalars())
        fake_tbparse = ModuleType("tbparse")
        fake_tbparse.SummaryReader = lambda _path: fake_reader
        monkeypatch.setitem(sys.modules, "tbparse", fake_tbparse)
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        count = _MOD._log_tensorboard_metrics(tmp_path)

        assert count == 3
        assert _fake_mlflow.log_metric.call_count == 3


class TestMain:
    def test_missing_env_vars_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in _MOD.REQUIRED_ENV:
            monkeypatch.delenv(key, raising=False)
        assert _MOD.main() == 1

    def test_output_dir_not_exists_returns_1(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub")
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg")
        monkeypatch.setenv("AZUREML_WORKSPACE_NAME", "ws")
        monkeypatch.setenv("AZUREML_MODEL_NAME", "m")
        monkeypatch.setenv("RUN_ID", "r")
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "nonexistent"))
        assert _MOD.main() == 1

    def test_no_checkpoints_returns_1(self, _env_vars: Path) -> None:
        assert _MOD.main() == 1

    def test_happy_path_stages_and_registers(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ckpt = _env_vars / "checkpoint-100"
        ckpt.mkdir()
        (ckpt / "model.safetensors").write_bytes(b"weights")
        (ckpt / "config.json").write_text("{}")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        result = _MOD.main()

        assert result == 0
        _fake_mlflow.set_tracking_uri.assert_called_once()
        _fake_mlflow.set_experiment.assert_called_once_with("my-model")
        _fake_registry_client.create_registered_model.assert_called_once_with("my-model")
        _fake_registry_client.create_model_version.assert_called_once_with(
            name="my-model",
            source="azureml://artifacts/origin/container/model",
            run_id="run-abc",
        )

    def test_skip_files_excluded(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ckpt = _env_vars / "checkpoint-50"
        ckpt.mkdir()
        (ckpt / "model.bin").write_bytes(b"ok")
        (ckpt / "optimizer.pt").write_bytes(b"skip")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        result = _MOD.main()

        assert result == 0
        log_artifacts_calls = _fake_mlflow.log_artifacts.call_args_list
        staged_call = [
            c
            for c in log_artifacts_calls
            if c.kwargs.get("artifact_path") == "model" or (c.args and len(c.args) > 1 and "model" in str(c))
        ]
        assert len(staged_call) >= 1

    def test_replay_run_id_tags(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ckpt = _env_vars / "checkpoint-1"
        ckpt.mkdir()
        (ckpt / "model.bin").write_bytes(b"x")
        monkeypatch.setenv("REPLAY_RUN_ID", "original-run-99")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        _MOD.main()

        tag_calls = {c.args[0]: c.args[1] for c in _fake_mlflow.set_tag.call_args_list}
        assert tag_calls["osmo.replay"] == "true"
        assert tag_calls["osmo.replay_source"] == "original-run-99"

    def test_existing_registered_model_creates_new_version(
        self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ckpt = _env_vars / "checkpoint-1"
        ckpt.mkdir()
        (ckpt / "model.bin").write_bytes(b"x")
        _fake_registry_client.create_registered_model.side_effect = _FakeMlflowException("RESOURCE_ALREADY_EXISTS")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        assert _MOD.main() == 0
        _fake_registry_client.create_model_version.assert_called_once()

    def test_tensorboard_dir_logged(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tb_dir = _env_vars / "runs"
        tb_dir.mkdir()
        (tb_dir / "events.out.tfevents.123").write_bytes(b"data")
        ckpt = _env_vars / "checkpoint-5"
        ckpt.mkdir()
        (ckpt / "model.bin").write_bytes(b"x")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)
        monkeypatch.setattr(_MOD, "_log_tensorboard_metrics", lambda _: 42)

        _MOD.main()

        artifact_calls = [
            c for c in _fake_mlflow.log_artifacts.call_args_list if c.kwargs.get("artifact_path") == "tensorboard"
        ]
        assert len(artifact_calls) == 1

    def test_hardlink_fallback_to_copy(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ckpt = _env_vars / "checkpoint-10"
        ckpt.mkdir()
        (ckpt / "model.bin").write_bytes(b"x")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        monkeypatch.setattr(os, "link", MagicMock(side_effect=OSError("cross-device")))

        result = _MOD.main()

        assert result == 0

    def test_selects_highest_checkpoint(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        for i in (5, 20, 10):
            d = _env_vars / f"checkpoint-{i}"
            d.mkdir()
            (d / "model.bin").write_text(f"v{i}")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        _MOD.main()

        register_call = _fake_registry_client.create_model_version.call_args
        assert register_call is not None

    def test_trainer_state_fallback_and_ready_seal(
        self,
        _env_vars: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import json

        monkeypatch.setenv("AZUREML_EXPERIMENT_NAME", "groot-smoke")
        monkeypatch.setenv("PROJECT_ID", "prj-robot")
        monkeypatch.setenv("EXTERNAL_JOB_ID", "ext-trn-001")
        monkeypatch.setenv("AML_EXTRA_TAGS", "scheduler.queue=gpu-high")

        ckpt = _env_vars / "checkpoint-2"
        ckpt.mkdir()
        (ckpt / "model.safetensors").write_bytes(b"groot-weights")
        (ckpt / "config.json").write_text('{"arch": "groot-n1.7"}', encoding="utf-8")
        (ckpt / "trainer_state.json").write_text(
            json.dumps(
                {
                    "log_history": [
                        {"step": 1, "loss": 1.25, "grad_norm": 0.85, "learning_rate": 1e-4},
                        {"step": 2, "loss": 0.98, "grad_norm": 0.72, "learning_rate": 1e-4},
                    ]
                }
            ),
            encoding="utf-8",
        )

        captured_staged_files: dict[str, str] = {}

        def _inspect_staged(path_str: str, *, artifact_path: str = "") -> None:
            if artifact_path == "model":
                staged_dir = Path(path_str)
                for p in staged_dir.iterdir():
                    if p.is_file():
                        captured_staged_files[p.name] = p.read_text(encoding="utf-8", errors="ignore")

        _fake_mlflow.log_artifacts.side_effect = _inspect_staged
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        try:
            result = _MOD.main()
        finally:
            _fake_mlflow.log_artifacts.side_effect = None

        assert result == 0
        _fake_mlflow.set_experiment.assert_called_once_with("groot-smoke")
        assert "checksums.sha256" in captured_staged_files
        assert "model.safetensors" in captured_staged_files["checksums.sha256"]
        assert "ready.json" in captured_staged_files
        tag_calls = {c.args[0]: c.args[1] for c in _fake_mlflow.set_tag.call_args_list}
        assert tag_calls["project_id"] == "prj-robot"
        assert tag_calls["external.job_id"] == "ext-trn-001"
        assert tag_calls["scheduler.queue"] == "gpu-high"
        assert tag_calls["checkpoint.name"] == "checkpoint-2"
        out = capsys.readouterr().out
        assert '"metrics_logged": 6' in out

    def test_emit_runhook_and_checkpoint_blob_ready_last_readback(
        self,
        _env_vars: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import json

        monkeypatch.setenv("PROJECT_ID", "prj-robotics")
        monkeypatch.setenv("SLURM_JOB_ID", "9912")
        (_env_vars / "hydration-receipt.json").write_text(
            json.dumps(
                {
                    "dataset_version": "2026.11.17-rc1",
                    "manifest_sha256": "deadbeef" * 8,
                    "source_kind": "aml_data_asset",
                    "throughput_mb_s": 412.5,
                    "elapsed_s": 8.4,
                }
            ),
            encoding="utf-8",
        )

        posted: list[tuple[str, dict[str, str], dict[str, object]]] = []

        class _FakeResp:
            status = 200

            def __enter__(self) -> _FakeResp:
                return self

            def __exit__(self, *_: object) -> None:
                return None

        def _fake_urlopen(req: object, timeout: int = 15) -> _FakeResp:
            headers = {k.lower(): v for k, v in req.header_items()}  # type: ignore[attr-defined]
            body = json.loads(req.data.decode("utf-8"))  # type: ignore[attr-defined]
            posted.append((req.full_url, headers, body))  # type: ignore[attr-defined]
            return _FakeResp()

        monkeypatch.setenv("RUNHOOK_URL", "https://ca-pai.internal/v1/projects/prj-robotics/runs:record")
        monkeypatch.setattr(_MOD.urllib.request, "urlopen", _fake_urlopen)

        rc = _MOD.main(["--emit-runhook", "--phase", "RUNNING", "--hydrate-s", "8.4"])
        assert rc == 0
        manifest = json.loads((_env_vars / "run-manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "RUNNING"
        assert manifest["dataset"]["dataset_version"] == "2026.11.17-rc1"
        assert len(posted) == 1
        assert posted[0][1]["idempotency-key"] == "run-42-running-1"

        ckpt = _env_vars / "checkpoint-2"
        ckpt.mkdir()
        (ckpt / "model.safetensors").write_bytes(b"groot-weights")
        (ckpt / "trainer_state.json").write_text(
            json.dumps({"log_history": [{"step": 2, "loss": 0.42}]}),
            encoding="utf-8",
        )

        uploaded_blobs: dict[str, bytes] = {}
        upload_order: list[str] = []

        class _FakeContainerClient:
            def __init__(self, *_: object, **__: object) -> None:
                pass

            def upload_blob(self, *, name: str, data: object, overwrite: bool = False) -> None:
                raw = data.read()  # type: ignore[attr-defined]
                upload_order.append(name)
                uploaded_blobs[name] = raw

            def download_blob(self, name: str) -> SimpleNamespace:
                return SimpleNamespace(chunks=lambda: iter([uploaded_blobs[name]]))

        fake_storage_pkg = ModuleType("azure.storage")
        fake_blob_pkg = ModuleType("azure.storage.blob")
        fake_blob_pkg.ContainerClient = _FakeContainerClient  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "azure.storage", fake_storage_pkg)
        monkeypatch.setitem(sys.modules, "azure.storage.blob", fake_blob_pkg)
        monkeypatch.setenv("CHECKPOINT_BLOB_URL", "https://models01.blob.core.windows.net/models/prj-robotics/run-42")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        assert _MOD.main() == 0
        assert upload_order[-1].endswith("/ready.json")
        tag_calls = {c.args[0]: c.args[1] for c in _fake_mlflow.set_tag.call_args_list}
        assert tag_calls["dataset.version"] == "2026.11.17-rc1"
        assert tag_calls["checkpoint.blob_readback_verified"] == "true"
        final_manifest = json.loads((_env_vars / "run-manifest.json").read_text(encoding="utf-8"))
        assert final_manifest["status"] == "COMPLETED"
        assert final_manifest["aml"]["model_version"] == "3"


class TestGrootDownloadBlobHydration:
    def test_verify_hydrated_dataset_validates_checksums_and_manifest_pin(self, tmp_path: Path) -> None:
        import hashlib
        import json

        dl_mod = load_training_module(
            "training_vla_groot_download_blob",
            "training/vla/scripts/groot/download_blob.py",
        )
        ds = tmp_path / "so101_v1"
        (ds / "meta").mkdir(parents=True)
        (ds / "metadata").mkdir(parents=True)
        info_bytes = json.dumps({"robot_type": "so101", "total_episodes": 12, "total_frames": 3600}).encode()
        (ds / "meta" / "info.json").write_bytes(info_bytes)
        manifest_bytes = json.dumps({"schema_version": "6.0", "release_name": "2026.11.17"}).encode()
        (ds / "metadata" / "release-manifest.json").write_bytes(manifest_bytes)
        (ds / ".published.json").write_text("{}", encoding="utf-8")

        info_sha = hashlib.sha256(info_bytes).hexdigest()
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        (ds / "checksums.sha256").write_text(
            f"{info_sha}  meta/info.json\n{manifest_sha}  metadata/release-manifest.json\n",
            encoding="utf-8",
        )

        receipt_path = tmp_path / "hydration-receipt.json"
        rc = dl_mod.main(
            [
                "--verify-only",
                "--dataset-path",
                str(ds),
                "--expected-manifest-sha256",
                manifest_sha,
                "--receipt-out",
                str(receipt_path),
            ]
        )
        assert rc == 0
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["checksums_verified"] == 2
        assert receipt["published_marker_present"] is True
        assert receipt["robot_type"] == "so101"

        (ds / "meta" / "info.json").write_bytes(b'{"robot_type": "tampered"}')
        with pytest.raises(SystemExit, match="Corrupt dataset file"):
            dl_mod.verify_hydrated_dataset(ds, expected_manifest_sha256=manifest_sha)

    def test_resolve_aml_data_asset_datastore_uri(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dl_mod = load_training_module(
            "training_vla_groot_download_blob_arm",
            "training/vla/scripts/groot/download_blob.py",
        )
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-test")
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-test")
        monkeypatch.setenv("AZUREML_WORKSPACE_NAME", "mlw-test")

        def _fake_arm_get(url: str, _cred: object) -> dict[str, object]:
            if "/data/pick_place_ds/versions/1?" in url:
                return {
                    "properties": {
                        "dataUri": "azureml://datastores/workspaceblobstore/paths/exports/releases/pick_place_ds/v1/",
                        "properties": {"manifest_sha256": "facefeed" * 8, "release_id": "pick-place-v1"},
                    }
                }
            if "/datastores/workspaceblobstore?" in url:
                return {
                    "properties": {
                        "accountName": "datasets01",
                        "containerName": "datasets",
                        "endpoint": "core.windows.net",
                    }
                }
            raise AssertionError(f"unexpected ARM URL: {url}")

        monkeypatch.setattr(dl_mod, "_arm_get_json", _fake_arm_get)
        resolved = dl_mod.resolve_aml_data_asset_to_blob_url("azureml:pick_place_ds:1", credential=MagicMock())
        assert (
            resolved["blob_url"]
            == "https://datasets01.blob.core.windows.net/datasets/exports/releases/pick_place_ds/v1"
        )
        assert resolved["manifest_sha256"] == "facefeed" * 8
        assert resolved["release_id"] == "pick-place-v1"

    def test_download_from_blob_url_filters_traversal_and_enforces_no_sas(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        dl_mod = load_training_module(
            "training_vla_groot_download_blob_dl",
            "training/vla/scripts/groot/download_blob.py",
        )
        fake_core = ModuleType("azure.core")
        fake_exc = ModuleType("azure.core.exceptions")

        class _HttpResponseError(Exception):
            def __init__(self, error_code: str = "") -> None:
                super().__init__(error_code)
                self.error_code = error_code

        fake_exc.HttpResponseError = _HttpResponseError  # type: ignore[attr-defined]
        fake_storage = ModuleType("azure.storage")
        fake_blob = ModuleType("azure.storage.blob")

        blobs = [
            SimpleNamespace(name="releases/v1/meta/info.json"),
            SimpleNamespace(name="releases/v1/../../escape.txt"),
            SimpleNamespace(name="releases/v1/.cache/temp"),
        ]

        class _Container:
            def __init__(self, *_: object, **__: object) -> None:
                pass

            def list_blobs(self, *, name_starts_with: str) -> list[SimpleNamespace]:
                assert name_starts_with == "releases/v1/"
                return blobs

            def download_blob(self, _blob: object) -> SimpleNamespace:
                return SimpleNamespace(chunks=lambda: iter([b'{"robot_type": "so101"}']))

        fake_blob.ContainerClient = _Container  # type: ignore[attr-defined]
        fake_id = ModuleType("azure.identity")
        fake_id.DefaultAzureCredential = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "azure.identity", fake_id)
        monkeypatch.setitem(sys.modules, "azure.core", fake_core)
        monkeypatch.setitem(sys.modules, "azure.core.exceptions", fake_exc)
        monkeypatch.setitem(sys.modules, "azure.storage", fake_storage)
        monkeypatch.setitem(sys.modules, "azure.storage.blob", fake_blob)
        monkeypatch.setenv("AZURE_CLIENT_ID", "cid-1")

        with pytest.raises(SystemExit, match="SAS token query string is forbidden"):
            dl_mod.download_from_blob_url(
                "https://datasets01.blob.core.windows.net/datasets/releases/v1?sig=abc",
                tmp_path / "blocked",
                forbid_static_secrets=True,
            )

        dest = tmp_path / "hydrated"
        receipt_out = tmp_path / "receipt.json"
        rc = dl_mod.main(
            [
                "--blob-url",
                "https://datasets01.blob.core.windows.net/datasets/releases/v1",
                "--dataset-path",
                str(dest),
                "--forbid-static-secrets",
                "--receipt-out",
                str(receipt_out),
            ]
        )
        assert rc == 0
        assert (dest / "meta" / "info.json").is_file()
        assert not (tmp_path / "escape.txt").exists()
