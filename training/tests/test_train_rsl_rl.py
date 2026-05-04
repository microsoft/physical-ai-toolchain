"""Tests for training/rl/scripts/rsl_rl/train.py."""

from __future__ import annotations

import importlib.metadata as _metadata
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

torch = pytest.importorskip("torch")

from conftest import load_training_module  # noqa: E402

# ---------------------------------------------------------------------------
# Pre-load stubs: register all heavy/missing modules in sys.modules BEFORE
# load_training_module imports the file.
# ---------------------------------------------------------------------------


class _StubAppLauncher:
    """Stand-in for isaaclab.app.AppLauncher used at module-import time."""

    @classmethod
    def add_app_launcher_args(cls, parser) -> None:
        parser.add_argument("--device", type=str, default=None)
        parser.add_argument("--enable_cameras", action="store_true", default=False)
        parser.add_argument("--headless", action="store_true", default=False)

    def __init__(self, args) -> None:
        self.app = SimpleNamespace()
        self.local_rank = 0


class _StubTensorDict:
    """Lightweight TensorDict replacement: stores data and exposes dict-like access."""

    def __init__(self, data, batch_size=None) -> None:
        self.data = data
        self.batch_size = batch_size

    def __eq__(self, other) -> bool:
        return isinstance(other, _StubTensorDict) and self.data == other.data


class _StubVecEnvWrapper:
    def __init__(self, env, clip_actions=None) -> None:
        self.env = env
        self.clip_actions = clip_actions
        self.num_envs = 4

    def get_observations(self):
        return torch.zeros(4)

    def step(self, actions):
        return torch.zeros(4), torch.zeros(4), torch.zeros(4), {}

    def reset(self):
        return torch.zeros(4), {}

    def close(self):
        return None


def _register_stub(name: str, module: ModuleType) -> None:
    sys.modules.setdefault(name, module)


def _build_stub_namespace(name: str, **attrs) -> ModuleType:
    mod = ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


# isaaclab namespace + submodules
_register_stub("isaaclab", _build_stub_namespace("isaaclab"))
_register_stub("isaaclab.app", _build_stub_namespace("isaaclab.app", AppLauncher=_StubAppLauncher))


class _DirectMARLEnv:
    pass


class _DirectMARLEnvCfg:
    pass


class _DirectRLEnvCfg:
    pass


class _ManagerBasedRLEnvCfg:
    pass


_register_stub(
    "isaaclab.envs",
    _build_stub_namespace(
        "isaaclab.envs",
        DirectMARLEnv=_DirectMARLEnv,
        DirectMARLEnvCfg=_DirectMARLEnvCfg,
        DirectRLEnvCfg=_DirectRLEnvCfg,
        ManagerBasedRLEnvCfg=_ManagerBasedRLEnvCfg,
        multi_agent_to_single_agent=lambda env: env,
    ),
)
_register_stub("isaaclab.utils", _build_stub_namespace("isaaclab.utils"))
_register_stub(
    "isaaclab.utils.dict",
    _build_stub_namespace("isaaclab.utils.dict", print_dict=lambda *a, **k: None),
)
_register_stub(
    "isaaclab.utils.io",
    _build_stub_namespace("isaaclab.utils.io", dump_yaml=lambda *a, **k: None),
)


class _RslRlOnPolicyRunnerCfg:
    pass


_register_stub("isaaclab_rl", _build_stub_namespace("isaaclab_rl"))
_register_stub(
    "isaaclab_rl.rsl_rl",
    _build_stub_namespace(
        "isaaclab_rl.rsl_rl",
        RslRlOnPolicyRunnerCfg=_RslRlOnPolicyRunnerCfg,
        RslRlVecEnvWrapper=_StubVecEnvWrapper,
    ),
)
_register_stub("isaaclab_tasks", _build_stub_namespace("isaaclab_tasks"))
_register_stub(
    "isaaclab_tasks.utils",
    _build_stub_namespace("isaaclab_tasks.utils", get_checkpoint_path=lambda *a, **k: "/fake/ckpt.pt"),
)
_register_stub(
    "isaaclab_tasks.utils.hydra",
    _build_stub_namespace(
        "isaaclab_tasks.utils.hydra",
        hydra_task_config=lambda task, agent: lambda fn: fn,
    ),
)


class _OnPolicyRunner:
    def __init__(self, env, agent_cfg_dict, log_dir=None, device=None) -> None:
        self.env = env
        self.cfg = agent_cfg_dict
        self.log_dir = log_dir
        self.device = device
        self.current_learning_iteration = 0
        self.alg = SimpleNamespace(learning_rate=0.001, policy=SimpleNamespace(action_std=torch.tensor([0.1])))

    def add_git_repo_to_log(self, path) -> None:
        pass

    def load(self, path) -> None:
        pass

    def log(self, locs, *args, **kwargs):
        return None

    def save(self, path, *args, **kwargs):
        return None

    def learn(self, num_learning_iterations, init_at_random_ep_len=True) -> None:
        pass


class _DistillationRunner(_OnPolicyRunner):
    pass


_register_stub("rsl_rl", _build_stub_namespace("rsl_rl"))
_register_stub(
    "rsl_rl.runners",
    _build_stub_namespace("rsl_rl.runners", OnPolicyRunner=_OnPolicyRunner, DistillationRunner=_DistillationRunner),
)
_register_stub("tensordict", _build_stub_namespace("tensordict", TensorDict=_StubTensorDict))


# gymnasium
class _RecordVideo:
    def __init__(self, env, **kwargs) -> None:
        self.env = env
        self.kwargs = kwargs


_gym = _build_stub_namespace("gymnasium", make=lambda *a, **k: SimpleNamespace(unwrapped=SimpleNamespace()))
_gym.wrappers = SimpleNamespace(RecordVideo=_RecordVideo)
_register_stub("gymnasium", _gym)

# omni
_omni = _build_stub_namespace("omni")
_omni.log = SimpleNamespace(warn=lambda *a, **k: None)
_register_stub("omni", _omni)


# training.utils + training.utils.metrics
class _AzureConfigError(Exception):
    pass


def _bootstrap_azure_ml(experiment_name=None, **_):
    return None


_register_stub(
    "training.utils",
    _build_stub_namespace(
        "training.utils",
        AzureConfigError=_AzureConfigError,
        bootstrap_azure_ml=_bootstrap_azure_ml,
    ),
)


class _SystemMetricsCollector:
    def __init__(self, collect_gpu=True, collect_disk=True) -> None:
        self.collect_gpu = collect_gpu
        self.collect_disk = collect_disk
        self._gpu_available = False
        self._gpu_handles = []

    def collect_metrics(self) -> dict:
        return {"system_cpu": 0.0}


_register_stub(
    "training.utils.metrics",
    _build_stub_namespace("training.utils.metrics", SystemMetricsCollector=_SystemMetricsCollector),
)

# Patch importlib.metadata.version for rsl-rl-lib check
_orig_version = _metadata.version


def _patched_version(name: str) -> str:
    if name == "rsl-rl-lib":
        return "999.0.0"
    return _orig_version(name)


_metadata.version = _patched_version

# Control sys.argv at module load (parser.parse_known_args consumes from sys.argv)
_saved_argv = sys.argv
sys.argv = ["test"]
try:
    _MOD = load_training_module("training_rl_scripts_rsl_rl_train", "training/rl/scripts/rsl_rl/train.py")
finally:
    sys.argv = _saved_argv


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestModuleLoad:
    def test_module_loaded(self):
        assert _MOD.__name__ == "training_rl_scripts_rsl_rl_train"
        assert hasattr(_MOD, "main")
        assert hasattr(_MOD, "RslRl3xCompatWrapper")


class TestRslRl3xCompatWrapper:
    def _make_wrapper(self):
        env = SimpleNamespace(
            num_envs=2,
            extras="x",
            get_observations=lambda: torch.zeros(2),
            step=lambda actions: (torch.zeros(2), torch.zeros(2), torch.zeros(2), {}),
            reset=lambda: (torch.zeros(2), {}),
        )
        return _MOD.RslRl3xCompatWrapper(env), env

    def test_init_proxies_attrs(self):
        wrapper, env = self._make_wrapper()
        # __init__ copies non-callable attrs onto wrapper
        assert wrapper._env is env

    def test_getattr_falls_through(self):
        wrapper, env = self._make_wrapper()
        # extras was set during init via setattr; missing attrs go through __getattr__
        env.dynamic_attr = "value"
        assert wrapper.dynamic_attr == "value"

    def test_ensure_tensordict_already_tensordict(self):
        wrapper, _ = self._make_wrapper()
        td = _MOD.TensorDict({"policy": torch.zeros(2)}, batch_size=[2])
        assert wrapper._ensure_tensordict(td) is td

    def test_ensure_tensordict_dict(self):
        wrapper, _ = self._make_wrapper()
        result = wrapper._ensure_tensordict({"policy": torch.zeros(2)})
        assert isinstance(result, _MOD.TensorDict)

    def test_ensure_tensordict_tensor(self):
        wrapper, _ = self._make_wrapper()
        result = wrapper._ensure_tensordict(torch.zeros(2))
        assert isinstance(result, _MOD.TensorDict)
        assert "policy" in result.data

    def test_ensure_tensordict_tuple_with_dict(self):
        wrapper, _ = self._make_wrapper()
        result = wrapper._ensure_tensordict(({"policy": torch.zeros(2)}, {}))
        assert isinstance(result, _MOD.TensorDict)

    def test_ensure_tensordict_tuple_with_tensor(self):
        wrapper, _ = self._make_wrapper()
        result = wrapper._ensure_tensordict((torch.zeros(2), {}))
        assert isinstance(result, _MOD.TensorDict)

    def test_ensure_tensordict_unsupported(self):
        wrapper, _ = self._make_wrapper()
        with pytest.raises(TypeError):
            wrapper._ensure_tensordict(42)

    def test_get_observations(self):
        wrapper, _ = self._make_wrapper()
        result = wrapper.get_observations()
        assert isinstance(result, _MOD.TensorDict)

    def test_step(self):
        wrapper, _ = self._make_wrapper()
        obs, _rew, _dones, extras = wrapper.step(torch.zeros(2))
        assert isinstance(obs, _MOD.TensorDict)
        assert extras == {}

    def test_reset_tuple(self):
        wrapper, _ = self._make_wrapper()
        obs, extras = wrapper.reset()
        assert isinstance(obs, _MOD.TensorDict)
        assert extras == {}

    def test_reset_non_tuple(self):
        env = SimpleNamespace(
            num_envs=2,
            get_observations=lambda: torch.zeros(2),
            step=lambda a: (torch.zeros(2),) * 4,
            reset=lambda: torch.zeros(2),
        )
        wrapper = _MOD.RslRl3xCompatWrapper(env)
        obs, extras = wrapper.reset()
        assert isinstance(obs, _MOD.TensorDict)
        assert extras == {}


class TestIsPrimaryRank:
    def test_not_distributed(self):
        args = SimpleNamespace(distributed=False)
        launcher = SimpleNamespace(local_rank=5)
        assert _MOD._is_primary_rank(args, launcher) is True

    def test_distributed_primary(self):
        args = SimpleNamespace(distributed=True)
        launcher = SimpleNamespace(local_rank=0)
        assert _MOD._is_primary_rank(args, launcher) is True

    def test_distributed_secondary(self):
        args = SimpleNamespace(distributed=True)
        launcher = SimpleNamespace(local_rank=1)
        assert _MOD._is_primary_rank(args, launcher) is False


class TestResolveEnvCount:
    def test_scene_with_num_envs(self):
        cfg = SimpleNamespace(scene=SimpleNamespace(num_envs=64))
        assert _MOD._resolve_env_count(cfg) == 64

    def test_scene_none_fallback_attr(self):
        cfg = SimpleNamespace(scene=None, num_envs=8)
        assert _MOD._resolve_env_count(cfg) == 8

    def test_neither_returns_none(self):
        cfg = SimpleNamespace(scene=None)
        assert _MOD._resolve_env_count(cfg) is None


class TestStartMlflowRun:
    def test_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "mlflow", None)
        ctx = SimpleNamespace(tracking_uri="x")
        mod, active = _MOD._start_mlflow_run(context=ctx, experiment_name="e", run_name="r", tags={}, params={})
        assert mod is None
        assert active is False

    def test_happy_path(self, monkeypatch):
        fake = ModuleType("mlflow")
        fake.set_tracking_uri = MagicMock()
        fake.set_experiment = MagicMock()
        fake.start_run = MagicMock()
        fake.set_tags = MagicMock()
        fake.log_params = MagicMock()
        monkeypatch.setitem(sys.modules, "mlflow", fake)

        ctx = SimpleNamespace(tracking_uri="uri")
        params = {"a": 1, "b": "s", "c": [1, 2], "d": None}
        mod, active = _MOD._start_mlflow_run(
            context=ctx, experiment_name="exp", run_name="run", tags={"k": "v"}, params=params
        )
        assert mod is fake
        assert active is True
        fake.set_tracking_uri.assert_called_once_with("uri")
        fake.set_experiment.assert_called_once_with("exp")
        fake.start_run.assert_called_once_with(run_name="run")
        fake.set_tags.assert_called_once_with({"k": "v"})
        # list "c" should be filtered out
        logged = fake.log_params.call_args[0][0]
        assert "c" not in logged
        assert logged == {"a": 1, "b": "s", "d": None}

    def test_exception(self, monkeypatch):
        fake = ModuleType("mlflow")
        fake.set_tracking_uri = MagicMock(side_effect=RuntimeError("boom"))
        monkeypatch.setitem(sys.modules, "mlflow", fake)
        ctx = SimpleNamespace(tracking_uri="uri")
        mod, active = _MOD._start_mlflow_run(context=ctx, experiment_name="e", run_name="r", tags={}, params={})
        assert mod is None
        assert active is False


class TestLogConfigArtifacts:
    def test_none_mlflow(self):
        _MOD._log_config_artifacts(None, "/nonexistent")

    def test_no_params_dir(self, tmp_path):
        mlflow = MagicMock()
        _MOD._log_config_artifacts(mlflow, str(tmp_path))
        mlflow.log_artifact.assert_not_called()

    def test_happy_path(self, tmp_path):
        params = tmp_path / "params"
        params.mkdir()
        (params / "env.yaml").write_text("x")
        (params / "agent.yaml").write_text("y")
        mlflow = MagicMock()
        _MOD._log_config_artifacts(mlflow, str(tmp_path))
        assert mlflow.log_artifact.call_count == 2

    def test_log_artifact_raises(self, tmp_path):
        params = tmp_path / "params"
        params.mkdir()
        (params / "env.yaml").write_text("x")
        mlflow = MagicMock()
        mlflow.log_artifact.side_effect = RuntimeError("fail")
        _MOD._log_config_artifacts(mlflow, str(tmp_path))


class TestSyncLogsToStorage:
    def test_storage_none(self):
        _MOD._sync_logs_to_storage(None, log_dir="/x", experiment_name="e")

    def test_root_missing(self, tmp_path):
        storage = MagicMock()
        _MOD._sync_logs_to_storage(storage, log_dir=str(tmp_path / "missing"), experiment_name="e")
        storage.upload_file.assert_not_called()

    def test_batch_path(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        storage = SimpleNamespace(
            upload_files_batch=MagicMock(return_value=["f.txt"]),
        )
        _MOD._sync_logs_to_storage(storage, log_dir=str(tmp_path), experiment_name="e")
        storage.upload_files_batch.assert_called_once()

    def test_sequential_path(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        storage = MagicMock(spec=["upload_file"])
        _MOD._sync_logs_to_storage(storage, log_dir=str(tmp_path), experiment_name="e")
        storage.upload_file.assert_called_once()

    def test_sequential_raises(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        storage = MagicMock(spec=["upload_file"])
        storage.upload_file.side_effect = RuntimeError("boom")
        _MOD._sync_logs_to_storage(storage, log_dir=str(tmp_path), experiment_name="e")

    def test_root_no_files(self, tmp_path):
        # Empty dir - root exists but no files
        storage = MagicMock()
        _MOD._sync_logs_to_storage(storage, log_dir=str(tmp_path), experiment_name="e")
        storage.upload_file.assert_not_called()


class TestRegisterFinalModel:
    def test_no_context(self):
        assert _MOD._register_final_model(context=None, model_path="/m", model_name="n", tags={}) is False

    def test_azure_import_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "azure.ai.ml.entities", None)
        ctx = SimpleNamespace(client=SimpleNamespace())
        result = _MOD._register_final_model(context=ctx, model_path="/m", model_name="n", tags={})
        assert result is False

    def test_happy_path(self, monkeypatch):
        fake_entities = ModuleType("azure.ai.ml.entities")
        fake_entities.Model = MagicMock(return_value="model_obj")
        monkeypatch.setitem(sys.modules, "azure.ai.ml.entities", fake_entities)
        ctx = SimpleNamespace(client=SimpleNamespace(models=SimpleNamespace(create_or_update=MagicMock())))
        result = _MOD._register_final_model(
            context=ctx, model_path="/m", model_name="n", tags={"t": "v"}, properties={"p": "1"}
        )
        assert result is True
        ctx.client.models.create_or_update.assert_called_once_with("model_obj")

    def test_create_raises(self, monkeypatch):
        fake_entities = ModuleType("azure.ai.ml.entities")
        fake_entities.Model = MagicMock(return_value="m")
        monkeypatch.setitem(sys.modules, "azure.ai.ml.entities", fake_entities)
        ctx = SimpleNamespace(
            client=SimpleNamespace(models=SimpleNamespace(create_or_update=MagicMock(side_effect=RuntimeError("x"))))
        )
        assert _MOD._register_final_model(context=ctx, model_path="/m", model_name="n", tags={}) is False


class TestCreateEnhancedLog:
    def _runner(self):
        return SimpleNamespace(
            alg=SimpleNamespace(
                learning_rate=0.001,
                policy=SimpleNamespace(action_std=torch.tensor([0.1, 0.2])),
            ),
            device="cpu",
        )

    def test_no_mlflow(self):
        original = MagicMock(return_value="orig")
        runner = self._runner()
        enhanced = _MOD._create_enhanced_log(original, None, False, runner, collect_system_metrics=False)
        assert enhanced({"it": 1}) == "orig"
        original.assert_called_once()

    def test_full_metrics(self):
        original = MagicMock(return_value=None)
        runner = self._runner()
        mlflow = MagicMock()
        enhanced = _MOD._create_enhanced_log(original, mlflow, True, runner, collect_system_metrics=False)

        locs = {
            "it": 5,
            "rewbuffer": [1.0, 2.0],
            "lenbuffer": [10, 20],
            "erewbuffer": [0.5],
            "irewbuffer": [0.3],
            "loss_dict": {"value": 0.1, "policy": 0.2},
            "ep_infos": [
                {
                    "logs_rew_walk": torch.tensor(1.0),
                    "logs_cur_speed": torch.tensor(0.5),
                    "metric/score": torch.tensor(0.8),
                    "plain_metric": torch.tensor(0.9),
                    "scalar_value": 0.7,
                }
            ],
        }
        enhanced(locs)
        mlflow.log_metrics.assert_called_once()
        batch = mlflow.log_metrics.call_args[0][0]
        assert "mean_reward" in batch
        assert "mean_episode_length" in batch
        assert "mean_extrinsic_reward" in batch
        assert "mean_intrinsic_reward" in batch
        assert "loss_value" in batch
        assert "learning_rate" in batch
        assert "mean_noise_std" in batch
        assert "reward_terms/walk" in batch
        assert "curriculum/speed" in batch
        assert "metric/score" in batch
        assert "episode_plain_metric" in batch
        assert "episode_scalar_value" in batch

    def test_collector_init_raises(self, monkeypatch):
        original = MagicMock(return_value=None)
        runner = self._runner()
        monkeypatch.setattr(_MOD, "SystemMetricsCollector", MagicMock(side_effect=RuntimeError("init fail")))
        enhanced = _MOD._create_enhanced_log(original, None, False, runner, collect_system_metrics=True)
        enhanced({"it": 0})

    def test_collector_with_gpu_handles(self, monkeypatch):
        original = MagicMock(return_value=None)
        runner = self._runner()

        class _Collector:
            def __init__(self, collect_gpu, collect_disk):
                self._gpu_available = True
                self._gpu_handles = [1, 2]

            def collect_metrics(self):
                return {"gpu_util": 0.5}

        monkeypatch.setattr(_MOD, "SystemMetricsCollector", _Collector)
        mlflow = MagicMock()
        enhanced = _MOD._create_enhanced_log(original, mlflow, True, runner, collect_system_metrics=True)
        enhanced({"it": 0, "rewbuffer": [1.0], "lenbuffer": [1]})
        batch = mlflow.log_metrics.call_args[0][0]
        assert "gpu_util" in batch

    def test_collect_metrics_raises(self, monkeypatch):
        original = MagicMock(return_value=None)
        runner = self._runner()

        class _Collector:
            def __init__(self, collect_gpu, collect_disk):
                self._gpu_available = False
                self._gpu_handles = []

            def collect_metrics(self):
                raise RuntimeError("collect fail")

        monkeypatch.setattr(_MOD, "SystemMetricsCollector", _Collector)
        mlflow = MagicMock()
        enhanced = _MOD._create_enhanced_log(original, mlflow, True, runner, collect_system_metrics=True)
        enhanced({"it": 0, "rewbuffer": [1.0], "lenbuffer": [1]})

    def test_log_metrics_raises(self):
        original = MagicMock(return_value=None)
        runner = self._runner()
        mlflow = MagicMock()
        mlflow.log_metrics.side_effect = RuntimeError("log fail")
        enhanced = _MOD._create_enhanced_log(original, mlflow, True, runner, collect_system_metrics=False)
        enhanced({"it": 0, "rewbuffer": [1.0], "lenbuffer": [1]})


class TestCreateEnhancedSave:
    def test_no_mlflow_no_storage(self):
        original = MagicMock(return_value="orig")
        runner = SimpleNamespace(current_learning_iteration=0)
        enhanced = _MOD._create_enhanced_save(original, None, False, None, "/log", "model", runner)
        assert enhanced("/log/ckpt.pt") == "orig"

    def test_mlflow_and_storage(self, monkeypatch, tmp_path):
        ckpt = tmp_path / "ckpt.pt"
        ckpt.write_text("data")
        original = MagicMock(return_value=None)
        runner = SimpleNamespace(current_learning_iteration=10)
        mlflow = MagicMock()
        storage = SimpleNamespace(upload_checkpoint=MagicMock(return_value="blob/path"))
        enhanced = _MOD._create_enhanced_save(original, mlflow, True, storage, str(tmp_path), "model", runner)
        enhanced(str(ckpt))
        mlflow.log_artifact.assert_called_once()
        storage.upload_checkpoint.assert_called_once()
        mlflow.set_tags.assert_called_once()

    def test_relative_path_join(self, tmp_path):
        ckpt = tmp_path / "ckpt.pt"
        ckpt.write_text("data")
        original = MagicMock(return_value=None)
        runner = SimpleNamespace(current_learning_iteration=0)
        mlflow = MagicMock()
        enhanced = _MOD._create_enhanced_save(original, mlflow, True, None, str(tmp_path), "model", runner)
        enhanced("ckpt.pt")
        mlflow.log_artifact.assert_called_once()

    def test_save_raises(self, tmp_path):
        ckpt = tmp_path / "ckpt.pt"
        ckpt.write_text("data")
        original = MagicMock(return_value=None)
        runner = SimpleNamespace(current_learning_iteration=0)
        storage = SimpleNamespace(upload_checkpoint=MagicMock(side_effect=RuntimeError("boom")))
        enhanced = _MOD._create_enhanced_save(original, None, False, storage, str(tmp_path), "model", runner)
        # exception caught internally
        enhanced(str(ckpt))


class TestMain:
    """Smoke tests for main() — exercise the no-azure happy path."""

    @pytest.fixture(autouse=True)
    def _stub_shutdown(self, monkeypatch):
        # simulation_shutdown uses os.fork (POSIX-only); stub on Windows.
        monkeypatch.setattr(_MOD, "prepare_for_shutdown", lambda *a, **k: None, raising=False)

    def _make_cfgs(self):
        env_cfg = _MOD.ManagerBasedRLEnvCfg()
        env_cfg.scene = SimpleNamespace(num_envs=4)
        env_cfg.sim = SimpleNamespace(device="cpu")
        env_cfg.seed = 0
        env_cfg.export_io_descriptors = False

        algorithm = SimpleNamespace(class_name="PPO")
        agent_cfg = SimpleNamespace(
            experiment_name="exp",
            run_name="",
            resume=False,
            algorithm=algorithm,
            max_iterations=1,
            seed=0,
            clip_actions=True,
            device="cpu",
            logger=None,
            to_dict=lambda: {"class_name": "OnPolicyRunner"},
        )
        return env_cfg, agent_cfg

    def test_main_no_azure_manager_based(self, monkeypatch, tmp_path):
        env_cfg, agent_cfg = self._make_cfgs()

        # Force args_cli into a known state
        monkeypatch.setattr(_MOD.args_cli, "task", "Walk", raising=False)
        monkeypatch.setattr(_MOD.args_cli, "num_envs", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "max_iterations", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "device", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "distributed", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "disable_azure", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "azure_primary_rank_only", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "video", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "export_io_descriptors", False, raising=False)

        env = SimpleNamespace(unwrapped=SimpleNamespace(), close=MagicMock())
        monkeypatch.setattr(_MOD.gym, "make", lambda *a, **k: env)

        runner = _OnPolicyRunner(env, {}, log_dir=str(tmp_path), device="cpu")
        monkeypatch.setattr(_MOD, "OnPolicyRunner", lambda *a, **k: runner)

        _MOD.main(env_cfg, agent_cfg)

    def test_main_with_azure_and_video(self, monkeypatch, tmp_path):
        env_cfg, agent_cfg = self._make_cfgs()
        agent_cfg.run_name = "v1"

        monkeypatch.setattr(_MOD.args_cli, "task", "Walk", raising=False)
        monkeypatch.setattr(_MOD.args_cli, "num_envs", 8, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "max_iterations", 1, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "device", "cpu", raising=False)
        monkeypatch.setattr(_MOD.args_cli, "distributed", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "disable_azure", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "azure_primary_rank_only", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "video", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "video_interval", 100, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "video_length", 50, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "export_io_descriptors", True, raising=False)

        ctx = SimpleNamespace(
            workspace_name="ws",
            storage=SimpleNamespace(
                container_name="c",
                upload_checkpoint=MagicMock(return_value="blob"),
                upload_files_batch=MagicMock(return_value=["f"]),
            ),
            tracking_uri="uri",
            client=SimpleNamespace(models=SimpleNamespace(create_or_update=MagicMock())),
        )
        monkeypatch.setattr(_MOD, "bootstrap_azure_ml", lambda experiment_name=None: ctx)

        fake_mlflow = ModuleType("mlflow")
        fake_mlflow.set_tracking_uri = MagicMock()
        fake_mlflow.set_experiment = MagicMock()
        fake_mlflow.start_run = MagicMock()
        fake_mlflow.set_tags = MagicMock()
        fake_mlflow.set_tag = MagicMock()
        fake_mlflow.log_params = MagicMock()
        fake_mlflow.log_metrics = MagicMock()
        fake_mlflow.log_artifact = MagicMock()
        fake_mlflow.end_run = MagicMock()
        monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

        env = SimpleNamespace(unwrapped=SimpleNamespace(), close=MagicMock())
        monkeypatch.setattr(_MOD.gym, "make", lambda *a, **k: env)

        runner = _OnPolicyRunner(env, {}, log_dir=str(tmp_path), device="cpu")
        monkeypatch.setattr(_MOD, "OnPolicyRunner", lambda *a, **k: runner)

        _MOD.main(env_cfg, agent_cfg)
        fake_mlflow.end_run.assert_called_once()

    def test_main_distillation_resume(self, monkeypatch, tmp_path):
        env_cfg, agent_cfg = self._make_cfgs()
        agent_cfg.algorithm = SimpleNamespace(class_name="Distillation")
        agent_cfg.resume = True
        agent_cfg.load_run = "prev"
        agent_cfg.load_checkpoint = "ckpt"
        agent_cfg.to_dict = lambda: {"class_name": "DistillationRunner", "obs_groups": None}

        monkeypatch.setattr(_MOD.args_cli, "task", "Walk", raising=False)
        monkeypatch.setattr(_MOD.args_cli, "num_envs", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "max_iterations", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "device", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "distributed", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "disable_azure", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "azure_primary_rank_only", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "video", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "export_io_descriptors", False, raising=False)

        env = SimpleNamespace(unwrapped=SimpleNamespace(), close=MagicMock())
        monkeypatch.setattr(_MOD.gym, "make", lambda *a, **k: env)

        runner = _DistillationRunner(env, {}, log_dir=str(tmp_path), device="cpu")
        monkeypatch.setattr(_MOD, "DistillationRunner", lambda *a, **k: runner)

        _MOD.main(env_cfg, agent_cfg)

    def test_main_distributed_assigns_local_rank(self, monkeypatch, tmp_path):
        env_cfg, agent_cfg = self._make_cfgs()

        monkeypatch.setattr(_MOD.args_cli, "task", "Walk", raising=False)
        monkeypatch.setattr(_MOD.args_cli, "num_envs", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "max_iterations", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "device", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "distributed", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "disable_azure", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "azure_primary_rank_only", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "video", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "export_io_descriptors", False, raising=False)
        monkeypatch.setattr(_MOD.app_launcher, "local_rank", 0, raising=False)

        env = SimpleNamespace(unwrapped=SimpleNamespace(), close=MagicMock())
        monkeypatch.setattr(_MOD.gym, "make", lambda *a, **k: env)

        runner = _OnPolicyRunner(env, {}, log_dir=str(tmp_path), device="cpu")
        monkeypatch.setattr(_MOD, "OnPolicyRunner", lambda *a, **k: runner)

        _MOD.main(env_cfg, agent_cfg)

    def test_main_unsupported_runner_raises(self, monkeypatch, tmp_path):
        env_cfg, agent_cfg = self._make_cfgs()
        agent_cfg.to_dict = lambda: {"class_name": "BogusRunner"}

        monkeypatch.setattr(_MOD.args_cli, "task", "Walk", raising=False)
        monkeypatch.setattr(_MOD.args_cli, "num_envs", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "max_iterations", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "device", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "distributed", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "disable_azure", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "azure_primary_rank_only", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "video", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "export_io_descriptors", False, raising=False)

        env = SimpleNamespace(unwrapped=SimpleNamespace(), close=MagicMock())
        monkeypatch.setattr(_MOD.gym, "make", lambda *a, **k: env)

        with pytest.raises(ValueError, match="Unsupported runner"):
            _MOD.main(env_cfg, agent_cfg)

    def test_main_marl_env_converted(self, monkeypatch, tmp_path):
        env_cfg, agent_cfg = self._make_cfgs()
        # Use DirectRLEnvCfg so the manager-based path triggers omni.log.warn
        env_cfg = _MOD.DirectRLEnvCfg()
        env_cfg.scene = SimpleNamespace(num_envs=4)
        env_cfg.sim = SimpleNamespace(device="cpu")
        env_cfg.seed = 0

        monkeypatch.setattr(_MOD.args_cli, "task", "Walk", raising=False)
        monkeypatch.setattr(_MOD.args_cli, "num_envs", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "max_iterations", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "device", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "distributed", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "disable_azure", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "azure_primary_rank_only", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "video", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "export_io_descriptors", False, raising=False)

        marl = _MOD.DirectMARLEnv()
        env = SimpleNamespace(unwrapped=marl, close=MagicMock())
        monkeypatch.setattr(_MOD.gym, "make", lambda *a, **k: env)
        # Convert to single agent returns a new env
        single = SimpleNamespace(unwrapped=SimpleNamespace(), close=MagicMock())
        monkeypatch.setattr(_MOD, "multi_agent_to_single_agent", lambda e: single)

        runner = _OnPolicyRunner(single, {}, log_dir=str(tmp_path), device="cpu")
        monkeypatch.setattr(_MOD, "OnPolicyRunner", lambda *a, **k: runner)

        _MOD.main(env_cfg, agent_cfg)

    def test_main_learn_raises_propagates(self, monkeypatch, tmp_path):
        env_cfg, agent_cfg = self._make_cfgs()

        monkeypatch.setattr(_MOD.args_cli, "task", "Walk", raising=False)
        monkeypatch.setattr(_MOD.args_cli, "num_envs", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "max_iterations", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "device", None, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "distributed", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "disable_azure", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "azure_primary_rank_only", True, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "video", False, raising=False)
        monkeypatch.setattr(_MOD.args_cli, "export_io_descriptors", False, raising=False)

        env = SimpleNamespace(unwrapped=SimpleNamespace(), close=MagicMock())
        monkeypatch.setattr(_MOD.gym, "make", lambda *a, **k: env)

        runner = _OnPolicyRunner(env, {}, log_dir=str(tmp_path), device="cpu")
        runner.learn = MagicMock(side_effect=RuntimeError("training boom"))
        monkeypatch.setattr(_MOD, "OnPolicyRunner", lambda *a, **k: runner)

        with pytest.raises(RuntimeError, match="training boom"):
            _MOD.main(env_cfg, agent_cfg)
