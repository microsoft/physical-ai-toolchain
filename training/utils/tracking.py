"""Experiment tracking and checkpoint registry backends shared by training and evaluation.

``TRACKING_BACKEND`` selects where runs, metrics, and checkpoints go:

- ``azureml`` (default): the Azure ML workspace's MLflow endpoint for runs and the Azure ML model
  registry for checkpoints. Azure SDKs load only for this backend.
- ``mlflow``: the MLflow tracking server named by ``MLFLOW_TRACKING_URI``, used as given for runs and
  for the MLflow model registry.
- ``none``: no tracking and no registration; jobs write their outputs only.

Registration raises on failure, so each caller keeps its own policy for intermediate and final
checkpoints. Backends register from different sources: Azure ML uploads ``local_path`` when given, and
MLflow registers the run artifact directory at ``artifact_uri``, logging a single-file checkpoint into a
directory of its own first.
"""

from __future__ import annotations

import contextlib
import os
import posixpath
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from training.utils.context import AzureMLContext, AzureStorageContext

TRACKING_BACKEND_ENV = "TRACKING_BACKEND"
DEFAULT_TRACKING_BACKEND = "azureml"
TRACKING_BACKENDS = ("azureml", "mlflow", "none")

# Run directory that holds checkpoints logged for registration, one subdirectory per checkpoint.
_REGISTERED_ARTIFACT_DIR = "checkpoints/registered"

# Python modules each backend needs at run time, mapped to the package that provides them.
_BACKEND_REQUIREMENTS: dict[str, dict[str, str]] = {
    "azureml": {
        "azure.identity": "azure-identity>=1.13.0",
        "azure.ai.ml": "azure-ai-ml",
        "mlflow": "mlflow",
    },
    "mlflow": {"mlflow": "mlflow"},
    "none": {},
}


class TrackingConfigError(RuntimeError):
    """Raised when the selected tracking backend cannot be configured."""


@dataclass(frozen=True)
class RegisteredCheckpoint:
    """A checkpoint version in a model registry.

    Attributes:
        name: Registered model name.
        version: Registry version of this checkpoint.
        uri: URI that resolves to the checkpoint (``models:/`` or ``azureml:``).
    """

    name: str
    version: str
    uri: str


def resolve_tracking_backend_name(value: str | None = None) -> str:
    """Return the selected backend name from ``value`` or ``TRACKING_BACKEND``.

    Args:
        value: Explicit backend name; ``None`` or empty falls back to the environment, then ``azureml``.

    Returns:
        One of ``azureml``, ``mlflow``, or ``none``.

    Raises:
        TrackingConfigError: When the name is not a known backend.
    """
    raw = value if value else os.environ.get(TRACKING_BACKEND_ENV, "")
    name = raw.strip().lower() or DEFAULT_TRACKING_BACKEND
    if name not in TRACKING_BACKENDS:
        raise TrackingConfigError(f"Unknown tracking backend {name!r}; use one of: {', '.join(TRACKING_BACKENDS)}")
    return name


def backend_requirements(name: str) -> dict[str, str]:
    """Return the Python modules the named backend needs, mapped to their packages."""
    return dict(_BACKEND_REQUIREMENTS[resolve_tracking_backend_name(name)])


class TrackingBackend(ABC):
    """Where a job sends runs and registers checkpoints."""

    name: ClassVar[str]

    @property
    def enabled(self) -> bool:
        """Whether this backend records runs."""
        return True

    @property
    @abstractmethod
    def tracking_uri(self) -> str | None:
        """MLflow tracking URI for runs, or ``None`` when tracking is off."""

    @property
    def storage(self) -> AzureStorageContext | None:
        """Azure Blob Storage for raw checkpoint uploads; only the Azure ML backend provides it."""
        return None

    @property
    def experiment_name(self) -> str:
        """Experiment that ``configure_mlflow`` activates when the caller names none."""
        return ""

    def configure_mlflow(self, mlflow: Any, *, experiment_name: str | None = None) -> None:
        """Point MLflow tracking and registry at this backend and select the experiment.

        Args:
            mlflow: The ``mlflow`` module.
            experiment_name: Experiment to activate; defaults to the backend's experiment and is skipped
                when both are empty.
        """
        uri = self.tracking_uri
        if uri is None:
            return
        mlflow.set_tracking_uri(uri)
        mlflow.set_registry_uri(uri)
        experiment = experiment_name or self.experiment_name
        if experiment:
            mlflow.set_experiment(experiment)

    def run_tags(self) -> dict[str, str]:
        """Tags that identify this backend on every run."""
        return {}

    def download_artifacts(self, artifact_uri: str, dst_path: Path) -> Path:
        """Download a ``runs:/`` or ``models:/`` artifact from this backend into ``dst_path``.

        Args:
            artifact_uri: Run artifact or registered model URI.
            dst_path: Existing local directory to download into.

        Returns:
            Local path of the downloaded file or directory.
        """
        import mlflow

        self.configure_mlflow(mlflow)
        return Path(mlflow.artifacts.download_artifacts(artifact_uri=artifact_uri, dst_path=str(dst_path)))

    @abstractmethod
    def register_checkpoint(
        self,
        *,
        name: str,
        artifact_uri: str | None = None,
        local_path: Path | None = None,
        description: str = "",
        tags: dict[str, str] | None = None,
        properties: dict[str, str] | None = None,
    ) -> RegisteredCheckpoint | None:
        """Register a checkpoint as a new version of model ``name``.

        Args:
            name: Registered model name.
            artifact_uri: Run artifact URI of the checkpoint (``runs:/`` or a concrete artifact URI).
            local_path: Local checkpoint file or directory.
            description: Version description.
            tags: Version tags.
            properties: Version properties; MLflow stores them as tags.

        Returns:
            The registered version, or ``None`` when the backend does not register checkpoints.
        """


def _downloaded_model_root(model_dir: Path, artifact_uri: str) -> Path:
    """Return the directory, or file, that holds an Azure ML model downloaded into ``model_dir``.

    The SDK writes a model under ``<download_path>/<name>``, and a directory model one level deeper under the
    registered directory's own name, so single-directory levels are followed down to the model's files.
    """
    if not model_dir.exists() or (model_dir.is_dir() and not any(model_dir.iterdir())):
        raise FileNotFoundError(f"The Azure ML download wrote no files for {artifact_uri} under {model_dir}")
    root = model_dir
    while root.is_dir():
        children = list(root.iterdir())
        if len(children) != 1 or not children[0].is_dir():
            break
        root = children[0]
    return root


def _default_azure_bootstrap(*, experiment_name: str) -> AzureMLContext:
    from training.utils.context import bootstrap_azure_ml

    return bootstrap_azure_ml(experiment_name=experiment_name)


class AzureMLTracking(TrackingBackend):
    """Azure ML workspace tracking and model registry.

    The workspace connection is created on first use, so constructing the backend never contacts Azure
    or imports the Azure SDKs. The workspace bootstrap activates the experiment itself.
    """

    name = "azureml"

    def __init__(
        self,
        *,
        experiment_name: str = "",
        bootstrap: Callable[..., AzureMLContext] | None = None,
        context: AzureMLContext | None = None,
    ) -> None:
        self._experiment_name = experiment_name
        self._bootstrap = bootstrap or _default_azure_bootstrap
        self._context = context

    @property
    def context(self) -> AzureMLContext:
        """The Azure ML workspace context, bootstrapped on first access."""
        if self._context is None:
            self._context = self._bootstrap(experiment_name=self._experiment_name)
        return self._context

    @property
    def tracking_uri(self) -> str:
        return self.context.tracking_uri

    @property
    def workspace_name(self) -> str:
        """Name of the connected Azure ML workspace."""
        return self.context.workspace_name

    @property
    def storage(self) -> AzureStorageContext | None:
        return getattr(self.context, "storage", None)

    def run_tags(self) -> dict[str, str]:
        return {"azureml_workspace": self.context.workspace_name}

    def download_artifacts(self, artifact_uri: str, dst_path: Path) -> Path:
        # Registered checkpoints are custom models, which MLflow's models:/ resolver rejects because they are not
        # MLflow-format models, so they download through the Azure ML SDK.
        if not artifact_uri.startswith("models:/"):
            return super().download_artifacts(artifact_uri, dst_path)
        name, _, version = artifact_uri[len("models:/") :].partition("/")
        if not name or not version:
            raise ValueError(f"Expected models:/<name>/<version>, got {artifact_uri}")
        self.context.client.models.download(name=name, version=version, download_path=str(dst_path))
        return _downloaded_model_root(Path(dst_path) / name, artifact_uri)

    def register_checkpoint(
        self,
        *,
        name: str,
        artifact_uri: str | None = None,
        local_path: Path | None = None,
        description: str = "",
        tags: dict[str, str] | None = None,
        properties: dict[str, str] | None = None,
    ) -> RegisteredCheckpoint:
        from azure.ai.ml.entities import Model

        path = str(local_path) if local_path is not None else artifact_uri
        if not path:
            raise ValueError("Azure ML registration needs local_path or artifact_uri")
        model = Model(
            name=name,
            path=path,
            type="custom_model",
            description=description,
            tags=tags or {},
            properties=properties or {},
        )
        registered = self.context.client.models.create_or_update(model)
        version = str(getattr(registered, "version", "") or "")
        return RegisteredCheckpoint(name=name, version=version, uri=f"azureml:{name}:{version}")


class MLflowTracking(TrackingBackend):
    """A self-hosted MLflow tracking server and its model registry."""

    name = "mlflow"

    def __init__(self, tracking_uri: str | None = None, *, experiment_name: str = "") -> None:
        uri = (tracking_uri or os.environ.get("MLFLOW_TRACKING_URI", "")).strip()
        if not uri:
            raise TrackingConfigError("TRACKING_BACKEND=mlflow requires MLFLOW_TRACKING_URI")
        self._tracking_uri = uri
        self._experiment_name = experiment_name

    @property
    def tracking_uri(self) -> str:
        return self._tracking_uri

    @property
    def experiment_name(self) -> str:
        return self._experiment_name

    def run_tags(self) -> dict[str, str]:
        return {"tracking_backend": self.name}

    def _client(self) -> Any:
        from mlflow.tracking import MlflowClient

        return MlflowClient(tracking_uri=self._tracking_uri, registry_uri=self._tracking_uri)

    @staticmethod
    def _split_run_uri(artifact_uri: str) -> tuple[str, str]:
        """Split ``runs:/<run_id>/<path>`` into the run ID and the run-relative artifact path."""
        run_id, _, relative = artifact_uri[len("runs:/") :].partition("/")
        if not run_id:
            raise ValueError(f"Malformed run artifact URI: {artifact_uri}")
        return run_id, relative.strip("/")

    @staticmethod
    def _artifact_location(client: Any, run_id: str, relative: str) -> str:
        """Return the concrete artifact-store location of a run-relative path."""
        root = client.get_run(run_id).info.artifact_uri.rstrip("/")
        return f"{root}/{relative}" if relative else root

    @staticmethod
    def _is_directory(client: Any, run_id: str, relative: str) -> bool:
        """Report whether a run artifact is a directory; raise when the run has no such artifact."""
        if not relative:
            return True
        parent = posixpath.dirname(relative)
        for entry in client.list_artifacts(run_id, parent or None):
            if entry.path == relative:
                return bool(entry.is_dir)
        raise ValueError(f"Run {run_id} has no artifact at {relative}")

    @staticmethod
    def _current_run_id() -> str:
        import mlflow

        run = mlflow.active_run() or mlflow.last_active_run()
        if run is None:
            raise TrackingConfigError("Registering a local checkpoint in MLflow needs an MLflow run")
        return run.info.run_id

    @staticmethod
    def _unused_registered_dir(client: Any, run_id: str, base: str) -> str:
        """Return ``checkpoints/registered/<base>``, numbered from ``-2`` when the run already holds that name."""
        taken = {posixpath.basename(entry.path) for entry in client.list_artifacts(run_id, _REGISTERED_ARTIFACT_DIR)}
        name = base
        suffix = 2
        while name in taken:
            name = f"{base}-{suffix}"
            suffix += 1
        return f"{_REGISTERED_ARTIFACT_DIR}/{name}"

    @classmethod
    def _log_checkpoint_directory(cls, client: Any, run_id: str, local_path: Path) -> str:
        """Log a checkpoint into a run directory of its own and return the run-relative directory.

        Each registration gets an unused directory, so a later checkpoint with the same name, such as another
        LeRobot ``pretrained_model``, never overwrites the files an earlier version points at.
        """
        is_dir = local_path.is_dir()
        artifact_dir = cls._unused_registered_dir(client, run_id, local_path.name if is_dir else local_path.stem)
        if is_dir:
            client.log_artifacts(run_id, str(local_path), artifact_dir)
        else:
            client.log_artifact(run_id, str(local_path), artifact_dir)
        return artifact_dir

    def _version_source(self, client: Any, artifact_uri: str | None, local_path: Path | None) -> tuple[str, str | None]:
        """Return the artifact directory a model version points at, and the run that owns it.

        MLflow resolves a version source as a directory, so ``models:/`` downloads of a single-file source come
        back empty. A file checkpoint is logged into a directory of its own first, from ``local_path`` when given
        and otherwise from the run's copy.
        """
        if artifact_uri and not artifact_uri.startswith("runs:/"):
            return artifact_uri, None

        if artifact_uri:
            run_id, relative = self._split_run_uri(artifact_uri)
            if self._is_directory(client, run_id, relative):
                return self._artifact_location(client, run_id, relative), run_id
            if local_path is None:
                with tempfile.TemporaryDirectory(prefix="mlflow-register-") as download_dir:
                    downloaded = Path(client.download_artifacts(run_id, relative, download_dir))
                    artifact_dir = self._log_checkpoint_directory(client, run_id, downloaded)
                return self._artifact_location(client, run_id, artifact_dir), run_id
        elif local_path is None:
            raise ValueError("MLflow registration needs artifact_uri or local_path")
        else:
            run_id = self._current_run_id()

        artifact_dir = self._log_checkpoint_directory(client, run_id, Path(local_path))
        return self._artifact_location(client, run_id, artifact_dir), run_id

    def register_checkpoint(
        self,
        *,
        name: str,
        artifact_uri: str | None = None,
        local_path: Path | None = None,
        description: str = "",
        tags: dict[str, str] | None = None,
        properties: dict[str, str] | None = None,
    ) -> RegisteredCheckpoint:
        from mlflow.exceptions import MlflowException

        client = self._client()
        source, run_id = self._version_source(client, artifact_uri, None if local_path is None else Path(local_path))

        try:
            client.create_registered_model(name)
        except MlflowException as exc:
            if getattr(exc, "error_code", "") != "RESOURCE_ALREADY_EXISTS":
                raise
        version_tags = {**(properties or {}), **(tags or {})}
        version = client.create_model_version(
            name,
            source=source,
            run_id=run_id,
            tags={key: str(value) for key, value in version_tags.items()},
            description=description or None,
        )
        return RegisteredCheckpoint(name=name, version=str(version.version), uri=f"models:/{name}/{version.version}")


class NoTracking(TrackingBackend):
    """No tracking server and no registry."""

    name = "none"

    @property
    def enabled(self) -> bool:
        return False

    @property
    def tracking_uri(self) -> None:
        return None

    def download_artifacts(self, artifact_uri: str, dst_path: Path) -> Path:
        raise TrackingConfigError(
            f"Downloading {artifact_uri} needs a tracking server; set TRACKING_BACKEND to azureml or mlflow"
        )

    def register_checkpoint(
        self,
        *,
        name: str,
        artifact_uri: str | None = None,
        local_path: Path | None = None,
        description: str = "",
        tags: dict[str, str] | None = None,
        properties: dict[str, str] | None = None,
    ) -> None:
        return None


def create_tracking_backend(
    name: str | None = None,
    *,
    experiment_name: str = "",
    azure_bootstrap: Callable[..., AzureMLContext] | None = None,
) -> TrackingBackend:
    """Create the selected tracking backend.

    Args:
        name: Backend name; ``None`` reads ``TRACKING_BACKEND`` and defaults to ``azureml``.
        experiment_name: Experiment the backend activates for its runs.
        azure_bootstrap: Replaces the Azure ML workspace bootstrap.

    Returns:
        The configured backend.

    Raises:
        TrackingConfigError: When the name is unknown or the backend's configuration is incomplete.
    """
    resolved = resolve_tracking_backend_name(name)
    if resolved == "azureml":
        return AzureMLTracking(experiment_name=experiment_name, bootstrap=azure_bootstrap)
    if resolved == "mlflow":
        return MLflowTracking(experiment_name=experiment_name)
    return NoTracking()


class NoOpMlflow:
    """Stands in for the ``mlflow`` module when tracking is off, so training loops need no branches."""

    _RUN = SimpleNamespace(info=SimpleNamespace(run_id="", artifact_uri="", experiment_id=""))

    @contextlib.contextmanager
    def start_run(self, *args: Any, **kwargs: Any) -> Iterator[SimpleNamespace]:
        yield self._RUN

    def active_run(self) -> None:
        return None

    def last_active_run(self) -> None:
        return None

    def end_run(self, *args: Any, **kwargs: Any) -> None:
        return None

    def log_param(self, *args: Any, **kwargs: Any) -> None:
        return None

    def log_params(self, *args: Any, **kwargs: Any) -> None:
        return None

    def log_metric(self, *args: Any, **kwargs: Any) -> None:
        return None

    def log_metrics(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set_tag(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set_tags(self, *args: Any, **kwargs: Any) -> None:
        return None

    def log_artifact(self, *args: Any, **kwargs: Any) -> None:
        return None

    def log_artifacts(self, *args: Any, **kwargs: Any) -> None:
        return None


def mlflow_module_for(backend_name: str | None = None) -> Any:
    """Return the ``mlflow`` module for the named backend, or a no-op stand-in when tracking is off.

    Args:
        backend_name: Backend name; ``None`` or empty reads ``TRACKING_BACKEND``.
    """
    if resolve_tracking_backend_name(backend_name) == "none":
        return NoOpMlflow()
    import mlflow

    return mlflow
