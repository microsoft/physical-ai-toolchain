"""Tests for training/utils/tracking.py."""

from __future__ import annotations

import posixpath
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from training.utils import tracking


class _FakeMlflowException(Exception):
    def __init__(self, message: str, error_code: str = "INTERNAL_ERROR") -> None:
        super().__init__(message)
        self.error_code = error_code


class _FakeMlflowClient:
    """Records MlflowClient calls the registry backend makes."""

    def __init__(self) -> None:
        self.init_kwargs: dict[str, Any] = {}
        self.existing_models: set[str] = set()
        self.created_models: list[str] = []
        self.versions: list[dict[str, Any]] = []
        self.logged: list[tuple[str, str, str, str | None]] = []
        self.downloads: list[tuple[str, str]] = []
        # Bytes of logged files, keyed by run-relative path.
        self.stored: dict[str, bytes] = {}
        self.create_model_error: Exception | None = None
        # Run-relative artifact paths mapped to whether they are directories.
        self.artifacts: dict[str, bool] = {
            "checkpoints": True,
            "checkpoints/000010": True,
            "skrl-run": True,
            "skrl-run/checkpoints": True,
            "skrl-run/checkpoints/best_agent.pt": False,
        }

    def __call__(self, **kwargs: Any) -> _FakeMlflowClient:
        self.init_kwargs = kwargs
        return self

    def get_run(self, run_id: str) -> SimpleNamespace:
        return SimpleNamespace(info=SimpleNamespace(artifact_uri=f"s3://mlflow-artifacts/7/{run_id}/artifacts/"))

    def create_registered_model(self, name: str) -> None:
        if self.create_model_error is not None:
            raise self.create_model_error
        if name in self.existing_models:
            raise _FakeMlflowException(f"{name} exists", error_code="RESOURCE_ALREADY_EXISTS")
        self.created_models.append(name)

    def create_model_version(
        self,
        name: str,
        source: str,
        run_id: str | None = None,
        tags: dict[str, str] | None = None,
        description: str | None = None,
    ) -> SimpleNamespace:
        self.versions.append(
            {"name": name, "source": source, "run_id": run_id, "tags": tags, "description": description}
        )
        return SimpleNamespace(version=len(self.versions))

    def list_artifacts(self, run_id: str, path: str | None = None) -> list[SimpleNamespace]:
        prefix = f"{path}/" if path else ""
        return [
            SimpleNamespace(path=entry, is_dir=is_dir)
            for entry, is_dir in self.artifacts.items()
            if entry.startswith(prefix) and "/" not in entry[len(prefix) :]
        ]

    def download_artifacts(self, run_id: str, path: str, dst_path: str) -> str:
        self.downloads.append((run_id, path))
        local = Path(dst_path) / Path(path).name
        local.write_bytes(b"checkpoint")
        return str(local)

    def _store(self, path: str, data: bytes) -> None:
        """Record a logged file and its parent directories as run artifacts."""
        parts = path.split("/")
        for index in range(1, len(parts)):
            self.artifacts["/".join(parts[:index])] = True
        self.artifacts[path] = False
        self.stored[path] = data

    def log_artifact(self, run_id: str, local_path: str, artifact_path: str | None = None) -> None:
        self.logged.append(("file", run_id, local_path, artifact_path))
        source = Path(local_path)
        if source.is_file():
            self._store(posixpath.join(artifact_path or "", source.name), source.read_bytes())

    def log_artifacts(self, run_id: str, local_dir: str, artifact_path: str | None = None) -> None:
        self.logged.append(("dir", run_id, local_dir, artifact_path))
        root = Path(local_dir)
        for source in sorted(root.rglob("*")):
            if source.is_file():
                relative = source.relative_to(root).as_posix()
                self._store(posixpath.join(artifact_path or "", relative), source.read_bytes())


@pytest.fixture
def fake_mlflow(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Install a fake ``mlflow`` package with tracking, exceptions, and a recording client."""
    client = _FakeMlflowClient()
    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = MagicMock()
    mlflow.set_registry_uri = MagicMock()
    mlflow.set_experiment = MagicMock()
    mlflow.active_run = MagicMock(return_value=None)
    mlflow.last_active_run = MagicMock(return_value=None)
    mlflow_tracking = ModuleType("mlflow.tracking")
    mlflow_tracking.MlflowClient = client
    mlflow_exceptions = ModuleType("mlflow.exceptions")
    mlflow_exceptions.MlflowException = _FakeMlflowException
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", mlflow_tracking)
    monkeypatch.setitem(sys.modules, "mlflow.exceptions", mlflow_exceptions)
    return SimpleNamespace(module=mlflow, client=client)


@pytest.fixture
def fake_azure_entities(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    entities = ModuleType("azure.ai.ml.entities")
    model_cls = MagicMock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
    entities.Model = model_cls
    monkeypatch.setitem(sys.modules, "azure.ai.ml.entities", entities)
    return model_cls


def _azure_context(version: str = "5") -> SimpleNamespace:
    models = SimpleNamespace(create_or_update=MagicMock(return_value=SimpleNamespace(version=version)))
    return SimpleNamespace(
        client=SimpleNamespace(models=models),
        tracking_uri="azureml://tracking",
        workspace_name="ws-1",
        storage=None,
    )


class TestResolveBackendName:
    def test_defaults_to_azureml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRACKING_BACKEND", raising=False)
        assert tracking.resolve_tracking_backend_name() == "azureml"

    def test_reads_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACKING_BACKEND", " MLflow ")
        assert tracking.resolve_tracking_backend_name() == "mlflow"

    def test_explicit_value_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACKING_BACKEND", "mlflow")
        assert tracking.resolve_tracking_backend_name("none") == "none"

    def test_blank_environment_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACKING_BACKEND", "  ")
        assert tracking.resolve_tracking_backend_name() == "azureml"

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(tracking.TrackingConfigError, match="Unknown tracking backend 'wandb'"):
            tracking.resolve_tracking_backend_name("wandb")


class TestBackendRequirements:
    def test_azure_modules_only_for_azureml(self) -> None:
        assert set(tracking.backend_requirements("azureml")) == {"azure.identity", "azure.ai.ml", "mlflow"}
        assert set(tracking.backend_requirements("mlflow")) == {"mlflow"}
        assert tracking.backend_requirements("none") == {}

    def test_returns_a_copy(self) -> None:
        tracking.backend_requirements("mlflow")["extra"] = "pkg"
        assert "extra" not in tracking.backend_requirements("mlflow")


class TestCreateTrackingBackend:
    def test_azureml_defers_bootstrap(self) -> None:
        bootstrap = MagicMock(return_value=_azure_context())
        backend = tracking.create_tracking_backend("azureml", experiment_name="exp", azure_bootstrap=bootstrap)
        assert isinstance(backend, tracking.AzureMLTracking)
        bootstrap.assert_not_called()
        assert backend.tracking_uri == "azureml://tracking"
        assert backend.workspace_name == "ws-1"
        bootstrap.assert_called_once_with(experiment_name="exp")

    def test_mlflow_uses_environment_uri(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.local:5000")
        backend = tracking.create_tracking_backend("mlflow", experiment_name="exp")
        assert isinstance(backend, tracking.MLflowTracking)
        assert backend.tracking_uri == "http://mlflow.local:5000"
        assert backend.experiment_name == "exp"

    def test_mlflow_without_uri_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        with pytest.raises(tracking.TrackingConfigError, match="MLFLOW_TRACKING_URI"):
            tracking.create_tracking_backend("mlflow")

    def test_none_disables_tracking(self) -> None:
        backend = tracking.create_tracking_backend("none")
        assert isinstance(backend, tracking.NoTracking)
        assert backend.enabled is False
        assert backend.tracking_uri is None

    def test_environment_selects_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACKING_BACKEND", "none")
        assert isinstance(tracking.create_tracking_backend(), tracking.NoTracking)


class TestConfigureMlflow:
    def test_mlflow_backend_selects_its_experiment(self, fake_mlflow: SimpleNamespace) -> None:
        backend = tracking.MLflowTracking("http://mlflow.local:5000", experiment_name="exp")
        backend.configure_mlflow(fake_mlflow.module)
        fake_mlflow.module.set_tracking_uri.assert_called_once_with("http://mlflow.local:5000")
        fake_mlflow.module.set_registry_uri.assert_called_once_with("http://mlflow.local:5000")
        fake_mlflow.module.set_experiment.assert_called_once_with("exp")

    def test_explicit_experiment_overrides_backend(self, fake_mlflow: SimpleNamespace) -> None:
        backend = tracking.MLflowTracking("http://mlflow.local:5000", experiment_name="exp")
        backend.configure_mlflow(fake_mlflow.module, experiment_name="other")
        fake_mlflow.module.set_experiment.assert_called_once_with("other")

    def test_azureml_leaves_experiment_to_bootstrap(self, fake_mlflow: SimpleNamespace) -> None:
        backend = tracking.AzureMLTracking(context=_azure_context())
        backend.configure_mlflow(fake_mlflow.module)
        fake_mlflow.module.set_tracking_uri.assert_called_once_with("azureml://tracking")
        fake_mlflow.module.set_experiment.assert_not_called()

    def test_none_backend_leaves_mlflow_untouched(self, fake_mlflow: SimpleNamespace) -> None:
        tracking.NoTracking().configure_mlflow(fake_mlflow.module, experiment_name="exp")
        fake_mlflow.module.set_tracking_uri.assert_not_called()
        fake_mlflow.module.set_experiment.assert_not_called()


class TestRunTags:
    def test_backends_identify_themselves(self) -> None:
        assert tracking.AzureMLTracking(context=_azure_context()).run_tags() == {"azureml_workspace": "ws-1"}
        assert tracking.MLflowTracking("http://m:5000").run_tags() == {"tracking_backend": "mlflow"}
        assert tracking.NoTracking().run_tags() == {}


class TestAzureMLRegistration:
    def test_prefers_local_path(self, fake_azure_entities: MagicMock) -> None:
        context = _azure_context(version="5")
        backend = tracking.AzureMLTracking(context=context)
        registered = backend.register_checkpoint(
            name="cartpole",
            artifact_uri="runs:/r1/checkpoints/model.pt",
            local_path=Path("/logs/model.pt"),
            description="d",
            tags={"t": "v"},
            properties={"p": "1"},
        )
        model_kwargs = fake_azure_entities.call_args.kwargs
        assert model_kwargs == {
            "name": "cartpole",
            "path": "/logs/model.pt",
            "type": "custom_model",
            "description": "d",
            "tags": {"t": "v"},
            "properties": {"p": "1"},
        }
        context.client.models.create_or_update.assert_called_once()
        assert registered == tracking.RegisteredCheckpoint("cartpole", "5", "azureml:cartpole:5")

    def test_uses_artifact_uri_without_local_path(self, fake_azure_entities: MagicMock) -> None:
        backend = tracking.AzureMLTracking(context=_azure_context())
        backend.register_checkpoint(name="m", artifact_uri="runs:/r1/ckpt")
        assert fake_azure_entities.call_args.kwargs["path"] == "runs:/r1/ckpt"

    def test_requires_a_source(self, fake_azure_entities: MagicMock) -> None:
        with pytest.raises(ValueError, match="local_path or artifact_uri"):
            tracking.AzureMLTracking(context=_azure_context()).register_checkpoint(name="m")

    def test_bootstraps_on_first_registration(self, fake_azure_entities: MagicMock) -> None:
        bootstrap = MagicMock(return_value=_azure_context())
        backend = tracking.AzureMLTracking(experiment_name="exp", bootstrap=bootstrap)
        backend.register_checkpoint(name="m", artifact_uri="runs:/r1/ckpt")
        backend.register_checkpoint(name="m", artifact_uri="runs:/r1/ckpt")
        bootstrap.assert_called_once_with(experiment_name="exp")


def _sdk_layout_download(files: dict[str, str]) -> MagicMock:
    """Fake ``ml_client.models.download`` that writes files the way the Azure ML SDK lays them out.

    The SDK writes a model under ``<download_path>/<name>``, and a directory model one level deeper under the
    registered directory's own name; ``files`` maps paths relative to ``<download_path>/<name>`` to contents.
    """

    def download(*, name: str, version: str, download_path: str) -> None:
        for relative, content in files.items():
            target = Path(download_path) / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    return MagicMock(side_effect=download)


class TestAzureMLDownload:
    def test_directory_model_returns_the_directory_holding_its_files(self, tmp_path: Path) -> None:
        context = _azure_context()
        context.client.models.download = _sdk_layout_download(
            {"pretrained_model/config.json": "{}", "pretrained_model/model.safetensors": "w"}
        )

        local = tracking.AzureMLTracking(context=context).download_artifacts("models:/act-policy/3", tmp_path)

        assert local == tmp_path / "act-policy" / "pretrained_model"
        assert (local / "config.json").is_file()
        context.client.models.download.assert_called_once_with(
            name="act-policy", version="3", download_path=str(tmp_path)
        )

    def test_flat_model_directory_returns_the_model_root(self, tmp_path: Path) -> None:
        context = _azure_context()
        context.client.models.download = _sdk_layout_download({"best_agent.pt": "w", "params.yaml": "p"})

        local = tracking.AzureMLTracking(context=context).download_artifacts("models:/anymal/1", tmp_path)

        assert local == tmp_path / "anymal"

    def test_missing_download_fails_clearly(self, tmp_path: Path) -> None:
        context = _azure_context()
        context.client.models.download = _sdk_layout_download({})

        with pytest.raises(FileNotFoundError, match="wrote no files for models:/anymal/1"):
            tracking.AzureMLTracking(context=context).download_artifacts("models:/anymal/1", tmp_path)


class TestMLflowRegistration:
    def test_requires_tracking_uri(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        with pytest.raises(tracking.TrackingConfigError):
            tracking.MLflowTracking("  ")

    def test_directory_artifact_registers_in_place(self, fake_mlflow: SimpleNamespace) -> None:
        backend = tracking.MLflowTracking("http://mlflow.local:5000")
        registered = backend.register_checkpoint(
            name="act-pick",
            artifact_uri="runs:/run-1/checkpoints/000010",
            local_path=Path("/outputs/checkpoints/000010/pretrained_model"),
            description="LeRobot checkpoint",
            tags={"framework": "lerobot", "evaluated": "false"},
            properties={"success_threshold": 0.7, "framework": "overridden"},
        )
        client = fake_mlflow.client
        assert client.init_kwargs == {
            "tracking_uri": "http://mlflow.local:5000",
            "registry_uri": "http://mlflow.local:5000",
        }
        assert client.created_models == ["act-pick"]
        assert client.logged == []
        assert client.versions == [
            {
                "name": "act-pick",
                "source": "s3://mlflow-artifacts/7/run-1/artifacts/checkpoints/000010",
                "run_id": "run-1",
                "tags": {"success_threshold": "0.7", "framework": "lerobot", "evaluated": "false"},
                "description": "LeRobot checkpoint",
            }
        ]
        assert registered == tracking.RegisteredCheckpoint("act-pick", "1", "models:/act-pick/1")

    def test_file_artifact_gets_its_own_directory(self, fake_mlflow: SimpleNamespace) -> None:
        backend = tracking.MLflowTracking("http://mlflow.local:5000")
        backend.register_checkpoint(name="cartpole", artifact_uri="runs:/run-1/skrl-run/checkpoints/best_agent.pt")

        client = fake_mlflow.client
        assert client.downloads == [("run-1", "skrl-run/checkpoints/best_agent.pt")]
        assert len(client.logged) == 1
        kind, run_id, local_path, artifact_dir = client.logged[0]
        assert (kind, run_id, Path(local_path).name, artifact_dir) == (
            "file",
            "run-1",
            "best_agent.pt",
            "checkpoints/registered/best_agent",
        )
        assert not Path(local_path).exists()
        assert (
            client.versions[0]["source"] == "s3://mlflow-artifacts/7/run-1/artifacts/checkpoints/registered/best_agent"
        )
        assert client.versions[0]["run_id"] == "run-1"

    def test_file_artifact_uses_local_copy(self, fake_mlflow: SimpleNamespace, tmp_path: Path) -> None:
        checkpoint = tmp_path / "best_agent.pt"
        checkpoint.write_bytes(b"x")
        backend = tracking.MLflowTracking("http://mlflow.local:5000")
        backend.register_checkpoint(
            name="cartpole",
            artifact_uri="runs:/run-1/skrl-run/checkpoints/best_agent.pt",
            local_path=checkpoint,
        )
        assert fake_mlflow.client.downloads == []
        assert fake_mlflow.client.logged == [("file", "run-1", str(checkpoint), "checkpoints/registered/best_agent")]

    def test_missing_run_artifact_raises(self, fake_mlflow: SimpleNamespace) -> None:
        backend = tracking.MLflowTracking("http://mlflow.local:5000")
        with pytest.raises(ValueError, match="has no artifact at checkpoints/999999"):
            backend.register_checkpoint(name="m", artifact_uri="runs:/run-1/checkpoints/999999")
        assert fake_mlflow.client.versions == []

    def test_existing_model_gains_a_version(self, fake_mlflow: SimpleNamespace) -> None:
        fake_mlflow.client.existing_models.add("cartpole")
        backend = tracking.MLflowTracking("http://mlflow.local:5000")
        registered = backend.register_checkpoint(name="cartpole", artifact_uri="runs:/run-1")
        assert fake_mlflow.client.versions[0]["source"] == "s3://mlflow-artifacts/7/run-1/artifacts"
        assert fake_mlflow.client.versions[0]["description"] is None
        assert registered.uri == "models:/cartpole/1"

    def test_other_registry_errors_propagate(self, fake_mlflow: SimpleNamespace) -> None:
        fake_mlflow.client.create_model_error = _FakeMlflowException("denied", error_code="PERMISSION_DENIED")
        backend = tracking.MLflowTracking("http://mlflow.local:5000")
        with pytest.raises(_FakeMlflowException, match="denied"):
            backend.register_checkpoint(name="cartpole", artifact_uri="runs:/run-1/checkpoints/000010")

    def test_concrete_uri_registers_without_run(self, fake_mlflow: SimpleNamespace) -> None:
        backend = tracking.MLflowTracking("http://mlflow.local:5000")
        backend.register_checkpoint(name="m", artifact_uri="s3://bucket/models/m")
        assert fake_mlflow.client.versions[0]["source"] == "s3://bucket/models/m"
        assert fake_mlflow.client.versions[0]["run_id"] is None

    def test_malformed_run_uri_raises(self, fake_mlflow: SimpleNamespace) -> None:
        backend = tracking.MLflowTracking("http://mlflow.local:5000")
        with pytest.raises(ValueError, match="Malformed run artifact URI"):
            backend.register_checkpoint(name="m", artifact_uri="runs://model")

    def test_local_file_logs_to_last_run(self, fake_mlflow: SimpleNamespace, tmp_path: Path) -> None:
        checkpoint = tmp_path / "model_99.pt"
        checkpoint.write_bytes(b"x")
        fake_mlflow.module.last_active_run.return_value = SimpleNamespace(info=SimpleNamespace(run_id="run-2"))
        backend = tracking.MLflowTracking("http://mlflow.local:5000")

        backend.register_checkpoint(name="m", local_path=checkpoint)

        assert fake_mlflow.client.logged == [("file", "run-2", str(checkpoint), "checkpoints/registered/model_99")]
        assert fake_mlflow.client.versions[0]["source"] == (
            "s3://mlflow-artifacts/7/run-2/artifacts/checkpoints/registered/model_99"
        )

    def test_local_directory_logs_to_active_run(self, fake_mlflow: SimpleNamespace, tmp_path: Path) -> None:
        checkpoint = tmp_path / "pretrained_model"
        checkpoint.mkdir()
        fake_mlflow.module.active_run.return_value = SimpleNamespace(info=SimpleNamespace(run_id="run-3"))
        backend = tracking.MLflowTracking("http://mlflow.local:5000")

        backend.register_checkpoint(name="m", local_path=checkpoint)

        assert fake_mlflow.client.logged == [
            ("dir", "run-3", str(checkpoint), "checkpoints/registered/pretrained_model")
        ]

    def test_same_named_checkpoints_get_their_own_directories(
        self, fake_mlflow: SimpleNamespace, tmp_path: Path
    ) -> None:
        fake_mlflow.module.active_run.return_value = SimpleNamespace(info=SimpleNamespace(run_id="run-4"))
        backend = tracking.MLflowTracking("http://mlflow.local:5000")
        steps = ("000010", "000020", "000030")
        for step in steps:
            checkpoint = tmp_path / step / "pretrained_model"
            checkpoint.mkdir(parents=True)
            (checkpoint / "model.safetensors").write_bytes(f"weights-{step}".encode())
            backend.register_checkpoint(name="act-pick", local_path=checkpoint)

        root = "s3://mlflow-artifacts/7/run-4/artifacts/"
        sources = [version["source"].removeprefix(root) for version in fake_mlflow.client.versions]
        assert sources == [
            "checkpoints/registered/pretrained_model",
            "checkpoints/registered/pretrained_model-2",
            "checkpoints/registered/pretrained_model-3",
        ]
        stored = [fake_mlflow.client.stored[f"{source}/model.safetensors"] for source in sources]
        assert stored == [f"weights-{step}".encode() for step in steps]

    def test_local_path_without_run_raises(self, fake_mlflow: SimpleNamespace, tmp_path: Path) -> None:
        backend = tracking.MLflowTracking("http://mlflow.local:5000")
        with pytest.raises(tracking.TrackingConfigError, match="needs an MLflow run"):
            backend.register_checkpoint(name="m", local_path=tmp_path)

    def test_requires_a_source(self, fake_mlflow: SimpleNamespace) -> None:
        with pytest.raises(ValueError, match="artifact_uri or local_path"):
            tracking.MLflowTracking("http://mlflow.local:5000").register_checkpoint(name="m")


class TestNoTracking:
    def test_registers_nothing(self, tmp_path: Path) -> None:
        backend = tracking.NoTracking()
        assert backend.register_checkpoint(name="m", local_path=tmp_path) is None
        assert backend.storage is None


class TestMlflowModuleFor:
    def test_none_returns_no_op_stand_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "mlflow", None)
        module = tracking.mlflow_module_for("none")
        assert isinstance(module, tracking.NoOpMlflow)
        with module.start_run(run_name="job") as run:
            assert run.info.run_id == ""
        assert module.log_metrics({"loss": 1.0}, step=1) is None
        assert module.set_tags({"k": "v"}) is None
        assert module.active_run() is None

    def test_tracked_backends_return_mlflow(self, fake_mlflow: SimpleNamespace) -> None:
        assert tracking.mlflow_module_for("mlflow") is fake_mlflow.module
        assert tracking.mlflow_module_for("azureml") is fake_mlflow.module

    def test_environment_selects_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACKING_BACKEND", "none")
        assert isinstance(tracking.mlflow_module_for(), tracking.NoOpMlflow)


class TestDownloadArtifacts:
    def test_mlflow_resolves_against_its_server(self, fake_mlflow: SimpleNamespace, tmp_path: Path) -> None:
        fake_mlflow.module.artifacts = SimpleNamespace(download_artifacts=MagicMock(return_value=str(tmp_path / "m")))
        backend = tracking.MLflowTracking("http://mlflow.local:5000")

        local = backend.download_artifacts("models:/cartpole/2", tmp_path)

        assert local == tmp_path / "m"
        fake_mlflow.module.set_tracking_uri.assert_called_once_with("http://mlflow.local:5000")
        fake_mlflow.module.artifacts.download_artifacts.assert_called_once_with(
            artifact_uri="models:/cartpole/2", dst_path=str(tmp_path)
        )

    def test_azureml_models_download_through_sdk(self, fake_mlflow: SimpleNamespace, tmp_path: Path) -> None:
        context = _azure_context()
        context.client.models.download = _sdk_layout_download({"best_agent.pt": "w"})
        backend = tracking.AzureMLTracking(context=context)

        assert backend.download_artifacts("models:/anymal/3", tmp_path) == tmp_path / "anymal"
        context.client.models.download.assert_called_once_with(name="anymal", version="3", download_path=str(tmp_path))

    def test_azureml_runs_download_through_mlflow(self, fake_mlflow: SimpleNamespace, tmp_path: Path) -> None:
        fake_mlflow.module.artifacts = SimpleNamespace(download_artifacts=MagicMock(return_value=str(tmp_path)))
        backend = tracking.AzureMLTracking(context=_azure_context())

        backend.download_artifacts("runs:/r1/checkpoints/model.pt", tmp_path)

        fake_mlflow.module.set_tracking_uri.assert_called_once_with("azureml://tracking")
        fake_mlflow.module.artifacts.download_artifacts.assert_called_once()

    def test_azureml_rejects_unversioned_model(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="models:/<name>/<version>"):
            tracking.AzureMLTracking(context=_azure_context()).download_artifacts("models:/anymal", tmp_path)

    def test_none_backend_cannot_download(self, tmp_path: Path) -> None:
        with pytest.raises(tracking.TrackingConfigError, match="needs a tracking server"):
            tracking.NoTracking().download_artifacts("runs:/r1/model.pt", tmp_path)
