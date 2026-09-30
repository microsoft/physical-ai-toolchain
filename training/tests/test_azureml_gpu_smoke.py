from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import load_training_module

smoke = load_training_module("azureml_gpu_smoke", "training/smoke/scripts/azureml_gpu_smoke.py")


class _FakeMetric:
    def __init__(self, key: str, value: float, timestamp: int, step: int) -> None:
        self.key = key
        self.value = value
        self.timestamp = timestamp
        self.step = step


class _FakeMlflowClient:
    def __init__(self, mlflow: _FakeMlflow) -> None:
        self._mlflow = mlflow

    def log_batch(self, run_id: str, metrics: list[_FakeMetric]) -> None:
        if self._mlflow.fail_log_batch:
            raise RuntimeError("tracking endpoint unavailable")
        self._mlflow.batches.append(len(metrics))
        for metric in metrics:
            self._mlflow.metrics[metric.key].append((metric.step, metric.value))

    def get_metric_history(self, run_id: str, key: str) -> list[SimpleNamespace]:
        values = self._mlflow.metrics[key]
        if self._mlflow.read_back_limit is not None:
            values = values[: self._mlflow.read_back_limit]
        return [SimpleNamespace(step=step, value=value) for step, value in values]


class _FakeMlflow:
    def __init__(
        self, *, read_back_limit: int | None = None, fail_log_batch: bool = False, corrupt_downloads: bool = False
    ) -> None:
        self.read_back_limit = read_back_limit
        self.fail_log_batch = fail_log_batch
        self.corrupt_downloads = corrupt_downloads
        self.metrics: dict[str, list[tuple[int, float]]] = defaultdict(list)
        self.batches: list[int] = []
        self.logged_artifacts: dict[str, bytes] = {}
        self.downloaded_uris: list[str] = []
        self.params: dict[str, Any] = {}
        self.dicts: dict[str, Any] = {}
        self.start_calls: list[dict[str, Any]] = []
        self.ended: str | None = None
        self._active: SimpleNamespace | None = None
        self.tracking = SimpleNamespace(MlflowClient=lambda: _FakeMlflowClient(self))
        self.entities = SimpleNamespace(Metric=_FakeMetric)
        self.artifacts = SimpleNamespace(download_artifacts=self._download_artifacts)

    def _download_artifacts(self, *, artifact_uri: str, dst_path: str) -> str:
        self.downloaded_uris.append(artifact_uri)
        artifact_path = artifact_uri.removeprefix("runs:/").split("/", 1)[1]
        content = self.logged_artifacts[artifact_path]
        target = Path(dst_path) / Path(artifact_path).name
        target.write_bytes(content + b"corrupt" if self.corrupt_downloads else content)
        return str(target)

    def active_run(self) -> SimpleNamespace | None:
        return self._active

    def start_run(self, **kwargs: Any) -> SimpleNamespace:
        self.start_calls.append(kwargs)
        self._active = SimpleNamespace(info=SimpleNamespace(run_id="run-123"))
        return self._active

    def log_params(self, params: dict[str, Any]) -> None:
        self.params.update(params)

    def set_tags(self, tags: dict[str, str]) -> None:
        pass

    def log_artifact(self, local_path: str, artifact_path: str | None = None) -> None:
        self.logged_artifacts[f"{artifact_path}/{Path(local_path).name}"] = Path(local_path).read_bytes()

    def log_dict(self, data: dict[str, Any], artifact_file: str) -> None:
        self.dicts[artifact_file] = data

    def end_run(self, status: str = "FINISHED") -> None:
        self.ended = status


class _FakeBlob:
    def __init__(self, blobs: dict[str, bytes], name: str) -> None:
        self._blobs = blobs
        self._name = name

    def get_blob_properties(self) -> SimpleNamespace:
        return SimpleNamespace(size=len(self._blobs[self._name]))

    def delete_blob(self) -> None:
        del self._blobs[self._name]


class _FakeStorage:
    container_name = "ckpts"

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.uploaded: list[str] = []
        self.blob_client = SimpleNamespace(get_blob_client=lambda container, blob: _FakeBlob(self.blobs, blob))

    def upload_checkpoint(self, *, local_path: str, model_name: str, step: int | None = None) -> str:
        name = f"checkpoints/{model_name}/20260930_000000_step_{step}.pt"
        self.blobs[name] = Path(local_path).read_bytes()
        self.uploaded.append(name)
        return name


class _FakeModels:
    def __init__(self) -> None:
        self.registered: list[Any] = []

    def create_or_update(self, model: Any) -> SimpleNamespace:
        self.registered.append(model)
        return SimpleNamespace(name=model.name, version="1")

    def get(self, name: str, version: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, version=version)


def _context(storage: _FakeStorage | None) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_name="ws-smoke",
        tracking_uri="azureml://tracking",
        storage=storage,
        client=SimpleNamespace(models=_FakeModels()),
    )


def _config(tmp_path: Path, **overrides: Any) -> Any:
    values = {
        "steps": 60,
        "checkpoint_interval": 20,
        "batch_size": 64,
        "matrix_size": 64,
        "matmul_iterations": 2,
        "output_dir": tmp_path / "checkpoints",
        "experiment_name": "gpu-smoke",
        "model_name": "gpu-smoke-test",
        "register_model": True,
        "azure": True,
        "allow_cpu": True,
        "seed": 0,
    }
    values.update(overrides)
    return smoke.SmokeConfig(**values)


def _statuses(summary: dict[str, Any]) -> dict[str, str]:
    return {check["name"]: check["status"] for check in summary["checks"]}


@pytest.fixture(autouse=True)
def _clear_azureml_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MLFLOW_RUN_ID", raising=False)


def test_runner_skips_checks_that_depend_on_a_failure() -> None:
    runner = smoke.SmokeRunner()

    def fail() -> None:
        raise RuntimeError("boom")

    runner.run("first", lambda: {"ok": True})
    runner.run("second", fail)
    runner.run("third", lambda: None, requires=["second"])

    assert [result.status for result in runner.results] == [smoke.PASSED, smoke.FAILED, smoke.SKIPPED]
    assert runner.results[1].error == "RuntimeError: boom"
    assert runner.results[2].details == {"blocked_by": ["second"]}
    assert not runner.succeeded


def test_runner_treats_skip_check_as_success() -> None:
    runner = smoke.SmokeRunner()

    def skip() -> None:
        raise smoke.SkipCheck("not configured")

    runner.run("optional", skip)

    assert runner.results[0].status == smoke.SKIPPED
    assert runner.results[0].details == {"reason": "not configured"}
    assert runner.succeeded


def test_cpu_run_without_azure_trains_and_writes_checkpoints(tmp_path: Path) -> None:
    config = _config(tmp_path, azure=False)

    summary = smoke.GpuSmokeTest(config).execute()

    assert summary["status"] == smoke.PASSED
    assert _statuses(summary) == {
        "device": smoke.PASSED,
        "matmul": smoke.PASSED,
        "training": smoke.PASSED,
        "checkpoints": smoke.PASSED,
    }
    files = sorted(path.name for path in config.output_dir.glob("checkpoint-step-*.pt"))
    assert files == ["checkpoint-step-000020.pt", "checkpoint-step-000040.pt", "checkpoint-step-000060.pt"]
    written = json.loads((config.output_dir / smoke.SUMMARY_FILENAME).read_text(encoding="utf-8"))
    assert written["status"] == smoke.PASSED


def test_missing_gpu_fails_and_skips_gpu_work(tmp_path: Path) -> None:
    summary = smoke.GpuSmokeTest(_config(tmp_path, azure=False, allow_cpu=False)).execute()

    statuses = _statuses(summary)
    assert summary["status"] == smoke.FAILED
    assert statuses["device"] == smoke.FAILED
    assert statuses["matmul"] == smoke.SKIPPED
    assert statuses["training"] == smoke.SKIPPED
    assert statuses["checkpoints"] == smoke.SKIPPED


def test_full_run_checks_every_azure_service(tmp_path: Path) -> None:
    mlflow = _FakeMlflow()
    storage = _FakeStorage()
    context = _context(storage)
    experiments: list[str] = []

    def bootstrap(experiment_name: str) -> SimpleNamespace:
        experiments.append(experiment_name)
        return context

    summary = smoke.GpuSmokeTest(_config(tmp_path), mlflow_module=mlflow, bootstrap=bootstrap).execute()

    assert summary["status"] == smoke.PASSED, summary
    assert set(_statuses(summary).values()) == {smoke.PASSED}
    assert experiments == ["gpu-smoke"]
    assert len(mlflow.metrics[smoke.METRIC_LOSS]) == 60
    assert list(mlflow.logged_artifacts) == ["smoke/checkpoint-step-000060.pt"]
    assert mlflow.downloaded_uris == ["runs:/run-123/smoke/checkpoint-step-000060.pt"]
    assert mlflow.batches == [41, 40, 40]
    assert mlflow.metrics[smoke.METRIC_TFLOPS]
    assert f"smoke/{smoke.SUMMARY_FILENAME}" in mlflow.dicts
    assert storage.uploaded and not storage.blobs
    assert context.client.models.registered[0].name == "gpu-smoke-test"
    assert mlflow.start_calls == [{"run_name": "gpu-smoke"}]
    assert mlflow.ended == "FINISHED"


def test_azureml_managed_run_is_resumed_and_left_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLFLOW_RUN_ID", "azureml-run")
    mlflow = _FakeMlflow()

    summary = smoke.GpuSmokeTest(
        _config(tmp_path), mlflow_module=mlflow, bootstrap=lambda _: _context(_FakeStorage())
    ).execute()

    assert summary["status"] == smoke.PASSED
    assert mlflow.start_calls == [{}]
    assert mlflow.ended is None


def test_metric_read_back_shortfall_fails_only_that_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "METRIC_READ_BACK_TIMEOUT_S", 0.0)
    mlflow = _FakeMlflow(read_back_limit=10)

    summary = smoke.GpuSmokeTest(
        _config(tmp_path), mlflow_module=mlflow, bootstrap=lambda _: _context(_FakeStorage())
    ).execute()

    statuses = _statuses(summary)
    assert summary["status"] == smoke.FAILED
    assert statuses["mlflow_metrics"] == smoke.FAILED
    assert statuses["checkpoints"] == smoke.PASSED
    assert statuses["model_registry"] == smoke.PASSED
    assert mlflow.ended == "FAILED"


def test_failed_metric_batches_fail_the_metrics_check(tmp_path: Path) -> None:
    summary = smoke.GpuSmokeTest(
        _config(tmp_path), mlflow_module=_FakeMlflow(fail_log_batch=True), bootstrap=lambda _: _context(_FakeStorage())
    ).execute()

    statuses = _statuses(summary)
    training = next(check for check in summary["checks"] if check["name"] == "training")
    assert statuses["training"] == smoke.PASSED
    assert training["details"]["metric_log_failures"] == 121
    assert statuses["mlflow_metrics"] == smoke.FAILED
    assert summary["status"] == smoke.FAILED


def test_corrupted_artifact_download_fails_the_artifact_check(tmp_path: Path) -> None:
    summary = smoke.GpuSmokeTest(
        _config(tmp_path),
        mlflow_module=_FakeMlflow(corrupt_downloads=True),
        bootstrap=lambda _: _context(_FakeStorage()),
    ).execute()

    statuses = _statuses(summary)
    assert statuses["mlflow_artifacts"] == smoke.FAILED
    assert statuses["storage"] == smoke.PASSED
    assert summary["status"] == smoke.FAILED


def test_storage_and_registry_skip_when_not_configured(tmp_path: Path) -> None:
    summary = smoke.GpuSmokeTest(
        _config(tmp_path, register_model=False),
        mlflow_module=_FakeMlflow(),
        bootstrap=lambda _: _context(None),
    ).execute()

    statuses = _statuses(summary)
    assert summary["status"] == smoke.PASSED
    assert statuses["storage"] == smoke.SKIPPED
    assert statuses["model_registry"] == smoke.SKIPPED


def test_workspace_failure_skips_azure_checks_but_trains(tmp_path: Path) -> None:
    def bootstrap(experiment_name: str) -> SimpleNamespace:
        raise RuntimeError("no workspace access")

    summary = smoke.GpuSmokeTest(_config(tmp_path), mlflow_module=_FakeMlflow(), bootstrap=bootstrap).execute()

    statuses = _statuses(summary)
    assert statuses["azure_workspace"] == smoke.FAILED
    assert statuses["training"] == smoke.PASSED
    assert statuses["checkpoints"] == smoke.PASSED
    for name in ("mlflow_run", "mlflow_metrics", "mlflow_artifacts", "storage", "model_registry"):
        assert statuses[name] == smoke.SKIPPED


def test_expected_checkpoint_steps_include_the_final_step(tmp_path: Path) -> None:
    test = smoke.GpuSmokeTest(_config(tmp_path, steps=45, checkpoint_interval=20))

    assert test._expected_checkpoint_steps() == [20, 40, 45]


def test_argument_parsing_defaults_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "job-experiment")
    monkeypatch.setenv("AZURE_ML_OUTPUT_CHECKPOINTS", "/mnt/outputs/checkpoints")

    args = smoke._parse_args(["--register-model", "false"])

    assert args.experiment_name == "job-experiment"
    assert args.output_dir == "/mnt/outputs/checkpoints"
    assert args.register_model is False
    with pytest.raises(SystemExit):
        smoke._parse_args(["--steps", "0"])
    with pytest.raises(SystemExit):
        smoke._parse_args(["--register-model", "maybe"])


def test_main_returns_nonzero_when_a_check_fails(tmp_path: Path) -> None:
    exit_code = smoke.main(["--skip-azure", "--output-dir", str(tmp_path), "--steps", "20"])

    assert exit_code == 1
    summary = json.loads((tmp_path / smoke.SUMMARY_FILENAME).read_text(encoding="utf-8"))
    assert _statuses(summary)["device"] == smoke.FAILED


def test_prepare_registry_uri_uses_a_local_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MLFLOW_REGISTRY_URI", raising=False)
    monkeypatch.setenv("MLFLOW_LOCAL_REGISTRY_DIR", str(tmp_path / "registry"))

    smoke._prepare_mlflow_registry_uri()

    assert (tmp_path / "registry").is_dir()
    assert smoke.os.environ["MLFLOW_REGISTRY_URI"] == f"file://{tmp_path / 'registry'}"
