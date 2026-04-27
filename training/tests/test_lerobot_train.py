"""Tests for training/il/scripts/lerobot/train.py."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from conftest import load_training_module

_MOD = load_training_module(
    "training_il_scripts_lerobot_train",
    "training/il/scripts/lerobot/train.py",
)


@pytest.fixture
def fake_mlflow(monkeypatch):
    mlflow = ModuleType("mlflow")
    run_ctx = SimpleNamespace(info=SimpleNamespace(run_id="run-abc"))

    class _RunCM:
        def __enter__(self):
            return run_ctx

        def __exit__(self, *a):
            return False

    mlflow.start_run = MagicMock(return_value=_RunCM())
    mlflow.log_params = MagicMock()
    mlflow.log_metrics = MagicMock()
    mlflow.log_metric = MagicMock()
    mlflow.log_param = MagicMock()
    mlflow.set_tag = MagicMock()
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    return mlflow


@pytest.fixture
def fake_checkpoints(monkeypatch):
    mod = ModuleType("training.il.scripts.lerobot.checkpoints")
    mod.upload_new_checkpoints = MagicMock()
    mod.register_final_checkpoint = MagicMock(return_value=0)
    monkeypatch.setitem(sys.modules, "training.il.scripts.lerobot.checkpoints", mod)
    return mod


@pytest.fixture
def fake_bootstrap(monkeypatch):
    mod = ModuleType("training.il.scripts.lerobot.bootstrap")
    mod.authenticate_huggingface = MagicMock(return_value="hf-user")
    mod.bootstrap_mlflow = MagicMock()
    monkeypatch.setitem(sys.modules, "training.il.scripts.lerobot.bootstrap", mod)
    return mod


class TestParseKValue:
    def test_with_k_suffix(self):
        assert _MOD._parse_k_value("2K") == 2000.0

    def test_without_suffix(self):
        assert _MOD._parse_k_value("100") == 100.0

    def test_decimal_with_k(self):
        assert _MOD._parse_k_value("1.5K") == 1500.0


class TestInitSystemCollector:
    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("SYSTEM_METRICS", "false")
        assert _MOD._init_system_collector() is None

    def test_uses_training_utils_when_available(self, monkeypatch):
        monkeypatch.setenv("SYSTEM_METRICS", "true")
        utils_pkg = ModuleType("training.utils")
        metrics_mod = ModuleType("training.utils.metrics")
        sentinel = MagicMock(name="collector-instance")
        metrics_mod.SystemMetricsCollector = MagicMock(return_value=sentinel)
        monkeypatch.setitem(sys.modules, "training.utils", utils_pkg)
        monkeypatch.setitem(sys.modules, "training.utils.metrics", metrics_mod)
        result = _MOD._init_system_collector()
        assert result is sentinel

    def test_falls_back_to_psutil(self, monkeypatch):
        monkeypatch.setenv("SYSTEM_METRICS", "true")
        # Force training.utils.metrics import to fail
        monkeypatch.setitem(sys.modules, "training.utils.metrics", None)
        psutil_mod = ModuleType("psutil")
        psutil_mod.cpu_percent = MagicMock(return_value=10.0)
        psutil_mod.virtual_memory = MagicMock(return_value=SimpleNamespace(used=1024 * 1024, percent=25.0))
        psutil_mod.disk_usage = MagicMock(return_value=SimpleNamespace(used=1024**3, percent=50.0))
        monkeypatch.setitem(sys.modules, "psutil", psutil_mod)
        # Pynvml import will fail naturally
        monkeypatch.setitem(sys.modules, "pynvml", None)
        collector = _MOD._init_system_collector()
        assert collector is not None
        m = collector.collect_metrics()
        assert "system/cpu_utilization_percentage" in m
        assert "system/memory_used_megabytes" in m
        assert "system/disk_used_gigabytes" in m

    def test_fallback_with_pynvml(self, monkeypatch):
        monkeypatch.setenv("SYSTEM_METRICS", "true")
        monkeypatch.setitem(sys.modules, "training.utils.metrics", None)
        psutil_mod = ModuleType("psutil")
        psutil_mod.cpu_percent = MagicMock(return_value=10.0)
        psutil_mod.virtual_memory = MagicMock(return_value=SimpleNamespace(used=1024 * 1024, percent=25.0))
        psutil_mod.disk_usage = MagicMock(return_value=SimpleNamespace(used=1024**3, percent=50.0))
        monkeypatch.setitem(sys.modules, "psutil", psutil_mod)
        pynvml_mod = ModuleType("pynvml")
        pynvml_mod.nvmlInit = MagicMock()
        pynvml_mod.nvmlDeviceGetCount = MagicMock(return_value=1)
        handle = object()
        pynvml_mod.nvmlDeviceGetHandleByIndex = MagicMock(return_value=handle)
        pynvml_mod.nvmlDeviceGetUtilizationRates = MagicMock(return_value=SimpleNamespace(gpu=42))
        pynvml_mod.nvmlDeviceGetMemoryInfo = MagicMock(return_value=SimpleNamespace(used=2 * 1024 * 1024, total=4 * 1024 * 1024))
        pynvml_mod.nvmlDeviceGetPowerUsage = MagicMock(return_value=125000)
        monkeypatch.setitem(sys.modules, "pynvml", pynvml_mod)
        collector = _MOD._init_system_collector()
        m = collector.collect_metrics()
        assert m["system/gpu_0_utilization_percentage"] == 42.0
        assert m["system/gpu_0_power_watts"] == 125.0

    def test_returns_none_when_psutil_missing(self, monkeypatch):
        monkeypatch.setenv("SYSTEM_METRICS", "true")
        monkeypatch.setitem(sys.modules, "training.utils.metrics", None)
        monkeypatch.setitem(sys.modules, "psutil", None)
        assert _MOD._init_system_collector() is None


class TestBuildTrainParams:
    def test_defaults(self, monkeypatch):
        for var in ("DATASET_REPO_ID", "POLICY_TYPE", "JOB_NAME", "POLICY_REPO_ID",
                    "TRAINING_STEPS", "BATCH_SIZE", "LEARNING_RATE", "LR_WARMUP_STEPS",
                    "SAVE_FREQ", "VAL_SPLIT", "SYSTEM_METRICS"):
            monkeypatch.delenv(var, raising=False)
        params = _MOD._build_train_params()
        assert params["policy_type"] == "act"
        assert params["training_steps"] == "100000"
        assert params["batch_size"] == "32"

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("POLICY_TYPE", "diffusion")
        monkeypatch.setenv("TRAINING_STEPS", "50000")
        monkeypatch.setenv("BATCH_SIZE", "64")
        params = _MOD._build_train_params()
        assert params["policy_type"] == "diffusion"
        assert params["training_steps"] == "50000"
        assert params["batch_size"] == "64"


class _FakePopen:
    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.stdout = iter(_FakePopen.lines)
        self.returncode = 0
        self.terminated = False

    def wait(self):
        return self.returncode

    def terminate(self):
        self.terminated = True


class TestRunTraining:
    def test_parses_log_lines_and_uploads(self, monkeypatch, fake_mlflow, fake_checkpoints, tmp_path):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("SYSTEM_METRICS", "false")
        monkeypatch.delenv("STORAGE_ACCOUNT", raising=False)

        _FakePopen.lines = [
            "step:200 smpl:2K ep:4 epch:0.31 loss:6.938 grdn:155.563 lr:1.0e-05 updt_s:0.324 data_s:0.011\n",
            "val_loss: 0.45\n",
            "noise line\n",
        ]
        monkeypatch.setattr(_MOD.subprocess, "Popen", _FakePopen)

        # Avoid actually installing real signal handlers
        monkeypatch.setattr(_MOD.signal, "signal", lambda *a, **k: None)

        rc = _MOD.run_training(["lerobot-train"], source="src")
        assert rc == 0
        fake_mlflow.log_metrics.assert_called()
        # Final upload always called
        assert fake_checkpoints.upload_new_checkpoints.called
        fake_mlflow.set_tag.assert_called_with("training_status", "completed")

    def test_failure_returns_nonzero(self, monkeypatch, fake_mlflow, fake_checkpoints, tmp_path):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("SYSTEM_METRICS", "false")

        class _FailPopen(_FakePopen):
            def __init__(self, cmd, **kwargs):
                super().__init__(cmd, **kwargs)
                self.returncode = 2

        _FakePopen.lines = []
        monkeypatch.setattr(_MOD.subprocess, "Popen", _FailPopen)
        monkeypatch.setattr(_MOD.signal, "signal", lambda *a, **k: None)

        rc = _MOD.run_training(["lerobot-train"])
        assert rc == 2
        fake_mlflow.set_tag.assert_called_with("training_status", "failed")

    def test_signal_handler_terminates(self, monkeypatch, fake_mlflow, fake_checkpoints, tmp_path):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("SYSTEM_METRICS", "false")
        captured = {}

        _FakePopen.lines = []
        proc_holder: list = []

        class _CapturePopen(_FakePopen):
            def __init__(self, cmd, **kwargs):
                super().__init__(cmd, **kwargs)
                proc_holder.append(self)

        def fake_signal(signum, handler):
            captured[signum] = handler

        monkeypatch.setattr(_MOD.subprocess, "Popen", _CapturePopen)
        monkeypatch.setattr(_MOD.signal, "signal", fake_signal)

        _MOD.run_training(["lerobot-train"])
        # Invoke the captured SIGTERM handler
        captured[_MOD.signal.SIGTERM](15, None)
        assert proc_holder[0].terminated is True

    def test_storage_account_adds_params(self, monkeypatch, fake_mlflow, fake_checkpoints, tmp_path):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("SYSTEM_METRICS", "false")
        monkeypatch.setenv("STORAGE_ACCOUNT", "myacct")
        monkeypatch.setenv("BLOB_PREFIX", "data/")
        _FakePopen.lines = []
        monkeypatch.setattr(_MOD.subprocess, "Popen", _FakePopen)
        monkeypatch.setattr(_MOD.signal, "signal", lambda *a, **k: None)

        _MOD.run_training(["lerobot-train"])
        params = fake_mlflow.log_params.call_args.args[0]
        assert params["storage_account"] == "myacct"
        assert params["blob_prefix"] == "data/"


class TestMain:
    def _setup(self, monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("SYSTEM_METRICS", "false")
        monkeypatch.delenv("REGISTER_CHECKPOINT", raising=False)
        _FakePopen.lines = []
        monkeypatch.setattr(_MOD.subprocess, "Popen", _FakePopen)
        monkeypatch.setattr(_MOD.signal, "signal", lambda *a, **k: None)

    def test_basic_invocation(self, monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap):
        self._setup(monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap)
        monkeypatch.setattr(_MOD.sys, "argv", ["train.py"])
        monkeypatch.setenv("DATASET_REPO_ID", "user/ds")
        monkeypatch.setenv("POLICY_TYPE", "act")
        monkeypatch.setenv("JOB_NAME", "job1")
        monkeypatch.setenv("TRAINING_STEPS", "1000")
        monkeypatch.setenv("BATCH_SIZE", "8")

        rc = _MOD.main()
        assert rc == 0
        fake_bootstrap.bootstrap_mlflow.assert_called_once()
        fake_checkpoints.register_final_checkpoint.assert_not_called()

    def test_register_checkpoint_branch(self, monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap):
        self._setup(monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap)
        monkeypatch.setattr(_MOD.sys, "argv", ["train.py"])
        monkeypatch.setenv("REGISTER_CHECKPOINT", "model-x")
        rc = _MOD.main()
        assert rc == 0
        fake_checkpoints.register_final_checkpoint.assert_called_once()

    def test_loads_mlflow_config_from_tmp(self, monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap):
        self._setup(monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap)
        monkeypatch.setattr(_MOD.sys, "argv", ["train.py"])

        cfg_path = tmp_path / "mlflow_config.env"
        cfg_path.write_text("FOO_KEY=bar\nINVALID_LINE\n")
        # Patch Path("/tmp/mlflow_config.env") usage by monkeypatching module's Path
        real_path_cls = _MOD.Path

        def fake_path(arg):
            if str(arg) == "/tmp/mlflow_config.env":
                return cfg_path
            return real_path_cls(arg)

        monkeypatch.setattr(_MOD, "Path", fake_path)
        _MOD.main()
        assert _MOD.os.environ.get("FOO_KEY") == "bar"

    def test_storage_account_changes_source(self, monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap):
        self._setup(monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap)
        monkeypatch.setattr(_MOD.sys, "argv", ["train.py"])
        monkeypatch.setenv("STORAGE_ACCOUNT", "acct")
        # Capture run_training source argument by patching it
        captured = {}

        def fake_run(cmd, source="x"):
            captured["source"] = source
            return 0

        monkeypatch.setattr(_MOD, "run_training", fake_run)
        rc = _MOD.main()
        assert rc == 0
        assert captured["source"] == "osmo-azure-data-training"

    def test_cli_args_skip_env_overrides(self, monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap):
        self._setup(monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap)
        monkeypatch.setattr(_MOD.sys, "argv", [
            "train.py",
            "--dataset.repo_id=cli/ds",
            "--policy.type=diffusion",
            "--output_dir=/x",
            "--job_name=cli-job",
            "--policy.device=cpu",
            "--wandb.enable=true",
            "--policy.repo_id=cli/repo",
            "--steps=10",
            "--batch_size=2",
            "--policy.optimizer_lr=1e-3",
            "--eval_freq=5",
            "--save_freq=5",
        ])
        monkeypatch.setenv("DATASET_REPO_ID", "env/ds")
        captured = {}

        def fake_run(cmd, source="x"):
            captured["cmd"] = cmd
            return 0

        monkeypatch.setattr(_MOD, "run_training", fake_run)
        _MOD.main()
        # Should NOT contain env-derived dataset
        assert not any("env/ds" in c for c in captured["cmd"])

    def test_auto_derives_policy_repo_id(self, monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap):
        self._setup(monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap)
        monkeypatch.setattr(_MOD.sys, "argv", ["train.py"])
        monkeypatch.delenv("POLICY_REPO_ID", raising=False)
        monkeypatch.setenv("JOB_NAME", "myjob")
        captured = {}

        def fake_run(cmd, source="x"):
            captured["cmd"] = cmd
            return 0

        monkeypatch.setattr(_MOD, "run_training", fake_run)
        _MOD.main()
        assert any("--policy.repo_id=hf-user/myjob" == c for c in captured["cmd"])
