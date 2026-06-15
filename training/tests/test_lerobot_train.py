"""Tests for training/il/scripts/lerobot/train.py."""

from __future__ import annotations

import json
import sys
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
    mlflow.set_tags = MagicMock()
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


class TestSyncCheckpointOutput:
    """``_sync_checkpoint_output`` mirrors ``OUTPUT_DIR/checkpoints/`` into the
    AzureML ``checkpoints`` named output (env var ``AZURE_ML_OUTPUT_CHECKPOINTS``)."""

    def test_no_target_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AZURE_ML_OUTPUT_CHECKPOINTS", raising=False)
        (tmp_path / "checkpoints").mkdir()
        _MOD._sync_checkpoint_output(tmp_path)

    def test_missing_source_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AZURE_ML_OUTPUT_CHECKPOINTS", str(tmp_path / "out"))
        _MOD._sync_checkpoint_output(tmp_path)
        assert not (tmp_path / "out").exists()

    def test_copies_to_destination(self, tmp_path, monkeypatch):
        src = tmp_path / "checkpoints"
        src.mkdir()
        (src / "ckpt.safetensors").write_text("data")
        dest = tmp_path / "out"
        monkeypatch.setenv("AZURE_ML_OUTPUT_CHECKPOINTS", str(dest))

        _MOD._sync_checkpoint_output(tmp_path)

        assert (dest / "ckpt.safetensors").read_text() == "data"

    def test_replaces_existing_destination(self, tmp_path, monkeypatch):
        src = tmp_path / "checkpoints"
        src.mkdir()
        (src / "new.safetensors").write_text("new")
        dest = tmp_path / "out"
        dest.mkdir()
        (dest / "stale.safetensors").write_text("stale")
        monkeypatch.setenv("AZURE_ML_OUTPUT_CHECKPOINTS", str(dest))

        _MOD._sync_checkpoint_output(tmp_path)

        assert (dest / "new.safetensors").exists()
        assert not (dest / "stale.safetensors").exists()

    def test_swallows_copy_errors(self, tmp_path, monkeypatch):
        (tmp_path / "checkpoints").mkdir()
        monkeypatch.setenv("AZURE_ML_OUTPUT_CHECKPOINTS", str(tmp_path / "out"))
        monkeypatch.setattr(_MOD.shutil, "copytree", MagicMock(side_effect=OSError("denied")))
        # Should not raise.
        _MOD._sync_checkpoint_output(tmp_path)


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
        pynvml_mod.nvmlDeviceGetMemoryInfo = MagicMock(
            return_value=SimpleNamespace(used=2 * 1024 * 1024, total=4 * 1024 * 1024)
        )
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
        for var in (
            "DATASET_REPO_ID",
            "POLICY_TYPE",
            "JOB_NAME",
            "POLICY_REPO_ID",
            "TRAINING_STEPS",
            "BATCH_SIZE",
            "LEARNING_RATE",
            "LR_WARMUP_STEPS",
            "SAVE_FREQ",
            "VAL_SPLIT",
            "SYSTEM_METRICS",
            "MIXED_PRECISION",
        ):
            monkeypatch.delenv(var, raising=False)
        params = _MOD._build_train_params(num_gpus=1)
        assert params["policy_type"] == "act"
        assert params["training_steps"] == "100000"
        assert params["batch_size"] == "32"
        assert params["num_gpus"] == "1"
        assert params["mixed_precision"] == "no"
        assert params["distributed"] == "false"
        assert params["effective_batch_size"] == "32"

    def test_multi_gpu_scales_effective_batch_size(self, monkeypatch):
        monkeypatch.setenv("POLICY_TYPE", "diffusion")
        monkeypatch.setenv("TRAINING_STEPS", "50000")
        monkeypatch.setenv("BATCH_SIZE", "64")
        monkeypatch.setenv("MIXED_PRECISION", "bf16")
        params = _MOD._build_train_params(num_gpus=4)
        assert params["policy_type"] == "diffusion"
        assert params["training_steps"] == "50000"
        assert params["batch_size"] == "64"
        assert params["num_gpus"] == "4"
        assert params["mixed_precision"] == "bf16"
        assert params["distributed"] == "true"
        assert params["effective_batch_size"] == "256"


class TestMultiGpuHelpers:
    def test_detect_num_gpus_no_torch(self, monkeypatch):
        # Force ImportError when train.py imports torch
        monkeypatch.setitem(sys.modules, "torch", None)
        assert _MOD._detect_num_gpus() == 1

    def test_detect_num_gpus_single(self, monkeypatch):
        torch_stub = ModuleType("torch")
        torch_stub.cuda = SimpleNamespace(device_count=lambda: 1)
        monkeypatch.setitem(sys.modules, "torch", torch_stub)
        assert _MOD._detect_num_gpus() == 1

    def test_detect_num_gpus_multi(self, monkeypatch):
        torch_stub = ModuleType("torch")
        torch_stub.cuda = SimpleNamespace(device_count=lambda: 4)
        monkeypatch.setitem(sys.modules, "torch", torch_stub)
        assert _MOD._detect_num_gpus() == 4

    def test_detect_num_gpus_floors_at_one(self, monkeypatch):
        # Defensive: never return 0; downstream callers branch on > 1.
        torch_stub = ModuleType("torch")
        torch_stub.cuda = SimpleNamespace(device_count=lambda: 0)
        monkeypatch.setitem(sys.modules, "torch", torch_stub)
        assert _MOD._detect_num_gpus() == 1

    def test_read_mixed_precision_default(self, monkeypatch):
        monkeypatch.delenv("MIXED_PRECISION", raising=False)
        assert _MOD._read_mixed_precision() == "no"

    def test_read_mixed_precision_invalid(self, monkeypatch):
        monkeypatch.setenv("MIXED_PRECISION", "int8")
        with pytest.raises(RuntimeError, match="MIXED_PRECISION must be one of"):
            _MOD._read_mixed_precision()

    def test_strip_use_amp_space_separated_value(self):
        # draccus accepts "--flag value" (argparse action='store', no nargs);
        # both tokens must be dropped or 'value' becomes an orphan positional.
        cleaned = _MOD._strip_use_amp(["--policy.use_amp", "true", "--keep=me"])
        assert cleaned == ["--keep=me"]

    def test_strip_use_amp_equals_form(self):
        cleaned = _MOD._strip_use_amp(["--policy.use_amp=true", "--keep=me"])
        assert cleaned == ["--keep=me"]

    def test_strip_use_amp_noop(self):
        cleaned = _MOD._strip_use_amp(["--keep=me", "--steps=10"])
        assert cleaned == ["--keep=me", "--steps=10"]

    def test_wrap_with_accelerate_rejects_wrong_head(self, monkeypatch):
        monkeypatch.setattr(_MOD, "_resolve_lerobot_train", lambda: "/v/bin/lerobot-train")
        with pytest.raises(RuntimeError, match="Expected cmd to start with 'lerobot-train'"):
            _MOD._wrap_with_accelerate(["python", "-m", "x"], num_gpus=2, mixed_precision="bf16")

    def test_wrap_with_accelerate_prepends_flags(self, monkeypatch):
        monkeypatch.setattr(_MOD, "_resolve_lerobot_train", lambda: "/v/bin/lerobot-train")
        wrapped = _MOD._wrap_with_accelerate(["lerobot-train", "--steps=10"], num_gpus=4, mixed_precision="bf16")
        assert wrapped[:5] == [
            "accelerate",
            "launch",
            "--multi_gpu",
            "--num_processes=4",
            "--mixed_precision=bf16",
        ]
        assert wrapped[5] == "/v/bin/lerobot-train"
        assert wrapped[6] == "--steps=10"


class _FakePopen:
    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.stdout = iter(_FakePopen.lines)
        self.returncode = 0
        self.terminated = False
        self.pid = 12345

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True


class TestRunTraining:
    def test_parses_log_lines_and_uploads(self, monkeypatch, fake_mlflow, fake_checkpoints, tmp_path):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("SYSTEM_METRICS", "false")

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

        # Capture os.killpg calls (handler now reaps the whole subprocess group
        # to clean up accelerate-spawned worker ranks; see train.py).
        killpg_calls: list[tuple[int, int]] = []

        def fake_killpg(pid, sig):
            killpg_calls.append((pid, sig))

        monkeypatch.setattr(_MOD.subprocess, "Popen", _CapturePopen)
        monkeypatch.setattr(_MOD.signal, "signal", fake_signal)
        monkeypatch.setattr(_MOD.os, "killpg", fake_killpg)

        _MOD.run_training(["lerobot-train"])
        # Invoke the captured SIGTERM handler
        captured[_MOD.signal.SIGTERM](15, None)
        assert killpg_calls and killpg_calls[0] == (proc_holder[0].pid, _MOD.signal.SIGTERM)

    def test_blob_urls_set_lineage_tags(self, monkeypatch, fake_mlflow, fake_checkpoints, tmp_path):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        monkeypatch.setenv("SYSTEM_METRICS", "false")
        monkeypatch.setenv("DATASET_REPO_ID", "dataset")
        monkeypatch.setenv(
            "BLOB_URLS",
            json.dumps(
                [
                    "https://acct.blob.core.windows.net/datasets/pusht",
                    "https://acct.blob.core.windows.net/datasets/pusht-val",
                ]
            ),
        )

        _FakePopen.lines = []
        monkeypatch.setattr(_MOD.subprocess, "Popen", _FakePopen)
        monkeypatch.setattr(_MOD.signal, "signal", lambda *a, **k: None)

        assert _MOD.run_training(["lerobot-train"], source="osmo-azure-blob-training") == 0
        tags = fake_mlflow.set_tags.call_args.args[0]
        assert tags["dataset.source"] == "azure-blob"


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
        monkeypatch.delenv("AZUREML_RUN_ID", raising=False)
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
        captured: dict[str, str] = {}

        def fake_run(cmd, source="x", num_gpus=1):
            captured["source"] = source
            return 0

        monkeypatch.setattr(_MOD, "run_training", fake_run)
        rc = _MOD.main()
        assert rc == 0
        assert _MOD.os.environ.get("FOO_KEY") == "bar"
        assert captured["source"] == "osmo-lerobot-hf"

    def test_blob_urls_use_blob_source_tag(self, monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap):
        self._setup(monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap)
        monkeypatch.delenv("AZUREML_RUN_ID", raising=False)
        monkeypatch.setattr(_MOD.sys, "argv", ["train.py"])
        monkeypatch.setenv("BLOB_URLS", json.dumps(["https://acct.blob.core.windows.net/datasets/pusht"]))

        captured: dict[str, str] = {}

        def fake_run(cmd, source="x", num_gpus=1):
            captured["source"] = source
            return 0

        monkeypatch.setattr(_MOD, "run_training", fake_run)
        rc = _MOD.main()
        assert rc == 0
        assert captured["source"] == "osmo-lerobot-blob"

    def test_azureml_run_id_uses_azureml_source_tag(
        self, monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap
    ):
        self._setup(monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap)
        monkeypatch.setattr(_MOD.sys, "argv", ["train.py"])
        monkeypatch.setenv("AZUREML_RUN_ID", "OfflineRun_12345")

        captured: dict[str, str] = {}

        def fake_run(cmd, source="x", num_gpus=1):
            captured["source"] = source
            return 0

        monkeypatch.setattr(_MOD, "run_training", fake_run)
        rc = _MOD.main()
        assert rc == 0
        assert captured["source"] == "azureml-lerobot-hf"

    def test_azureml_run_id_with_blob_urls_uses_azureml_blob_source_tag(
        self, monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap
    ):
        self._setup(monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap)
        monkeypatch.setattr(_MOD.sys, "argv", ["train.py"])
        monkeypatch.setenv("AZUREML_RUN_ID", "OfflineRun_12345")
        monkeypatch.setenv("BLOB_URLS", json.dumps(["https://acct.blob.core.windows.net/datasets/pusht"]))

        captured: dict[str, str] = {}

        def fake_run(cmd, source="x", num_gpus=1):
            captured["source"] = source
            return 0

        monkeypatch.setattr(_MOD, "run_training", fake_run)
        rc = _MOD.main()
        assert rc == 0
        assert captured["source"] == "azureml-lerobot-blob"

    def test_cli_args_skip_env_overrides(self, monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap):
        self._setup(monkeypatch, tmp_path, fake_mlflow, fake_checkpoints, fake_bootstrap)
        monkeypatch.setattr(
            _MOD.sys,
            "argv",
            [
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
            ],
        )
        monkeypatch.setenv("DATASET_REPO_ID", "env/ds")
        captured = {}

        def fake_run(cmd, source="x", num_gpus=1):
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

        def fake_run(cmd, source="x", num_gpus=1):
            captured["cmd"] = cmd
            return 0

        monkeypatch.setattr(_MOD, "run_training", fake_run)
        _MOD.main()
        assert any(c == "--policy.repo_id=hf-user/myjob" for c in captured["cmd"])
