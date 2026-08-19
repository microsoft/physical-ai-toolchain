"""Checkpoint-routing tests for RSL-RL evaluation entry points."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
import torch

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _module(name: str, **attributes: object) -> ModuleType:
    module = ModuleType(name)
    for attribute, value in attributes.items():
        setattr(module, attribute, value)
    return module


class _AppLauncher:
    @staticmethod
    def add_app_launcher_args(parser: object) -> None:
        pass

    def __init__(self, args: object) -> None:
        del args
        self.app = MagicMock()


class _DirectMARLEnv:
    pass


class _Cfg:
    pass


def _touch_marker(path: str) -> None:
    Path(path).touch()


class _Malicious:
    def __init__(self, marker: Path) -> None:
        self._marker = marker

    def __reduce__(self) -> tuple[object, tuple[str]]:
        return (_touch_marker, (str(self._marker),))


def _load_module(monkeypatch: pytest.MonkeyPatch, name: str, relative_path: str, argv: list[str]) -> ModuleType:
    cli_args = _module(
        "training.rl.cli_args",
        add_rsl_rl_args=lambda parser: None,
        update_rsl_rl_cfg=lambda cfg, args: cfg,
    )
    hydra = _module("isaaclab_tasks.utils.hydra", hydra_task_config=lambda task, agent: lambda function: function)
    runners = _module("rsl_rl.runners", DistillationRunner=MagicMock(), OnPolicyRunner=MagicMock())

    modules = {
        "gymnasium": _module("gymnasium", make=MagicMock(), wrappers=SimpleNamespace(RecordVideo=MagicMock())),
        "isaaclab": _module("isaaclab"),
        "isaaclab.app": _module("isaaclab.app", AppLauncher=_AppLauncher),
        "isaaclab.envs": _module(
            "isaaclab.envs",
            DirectMARLEnv=_DirectMARLEnv,
            DirectMARLEnvCfg=_Cfg,
            DirectRLEnvCfg=_Cfg,
            ManagerBasedRLEnvCfg=_Cfg,
            multi_agent_to_single_agent=lambda env: env,
        ),
        "isaaclab.utils": _module("isaaclab.utils"),
        "isaaclab.utils.assets": _module("isaaclab.utils.assets", retrieve_file_path=lambda path: path),
        "isaaclab.utils.dict": _module("isaaclab.utils.dict", print_dict=lambda *args, **kwargs: None),
        "isaaclab.utils.pretrained_checkpoint": _module(
            "isaaclab.utils.pretrained_checkpoint",
            get_published_pretrained_checkpoint=lambda *args: None,
        ),
        "isaaclab_rl": _module("isaaclab_rl"),
        "isaaclab_rl.rsl_rl": _module(
            "isaaclab_rl.rsl_rl",
            RslRlBaseRunnerCfg=_Cfg,
            RslRlVecEnvWrapper=lambda env, clip_actions: env,
            export_policy_as_jit=MagicMock(),
            export_policy_as_onnx=MagicMock(),
        ),
        "isaaclab_tasks": _module("isaaclab_tasks"),
        "isaaclab_tasks.utils": _module(
            "isaaclab_tasks.utils",
            get_checkpoint_path=lambda *args: "/fake/model.pt",
        ),
        "isaaclab_tasks.utils.hydra": hydra,
        "rsl_rl": _module("rsl_rl"),
        "rsl_rl.runners": runners,
        "training.rl.cli_args": cli_args,
        "training.rl.simulation_shutdown": _module(
            "training.rl.simulation_shutdown",
            prepare_for_shutdown=MagicMock(),
        ),
        "training.packaging": _module("training.packaging"),
        "training.packaging.scripts": _module("training.packaging.scripts"),
        "training.packaging.scripts.export_policy": _module(
            "training.packaging.scripts.export_policy",
            write_sha256_sidecar=MagicMock(),
        ),
    }
    for module_name, module in modules.items():
        monkeypatch.setitem(sys.modules, module_name, module)
    monkeypatch.setattr(sys.modules["training.rl"], "cli_args", cli_args)
    monkeypatch.setattr(sys, "argv", argv)

    path = _REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class TestRslRlCheckpointRouting:
    def test_checkpoint_monitor_routes_load_through_safe_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _load_module(
            monkeypatch,
            "test_monitor_checkpoints_module",
            "evaluation/sil/monitor_checkpoints.py",
            ["monitor_checkpoints.py", "--checkpoint_dir", "/tmp/checkpoints"],
        )
        runner = MagicMock()
        runner.get_inference_policy.return_value = "policy"
        module.OnPolicyRunner = MagicMock(return_value=runner)
        safe_loader = MagicMock()
        module.safe_load_framework_checkpoint = safe_loader
        monitor = object.__new__(module.CheckpointMonitor)
        monitor.agent_cfg = SimpleNamespace(
            class_name="OnPolicyRunner",
            device="cpu",
            to_dict=lambda: {},
        )
        monitor.env = SimpleNamespace(unwrapped=SimpleNamespace(device="cpu"))

        policy = monitor._load_policy("/fake/model.pt")

        safe_loader.assert_called_once_with("/fake/model.pt", loader=runner.load)
        runner.load.assert_not_called()
        assert policy == "policy"

    def test_checkpoint_monitor_propagates_rejected_checkpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _load_module(
            monkeypatch,
            "test_monitor_checkpoints_rejection_module",
            "evaluation/sil/monitor_checkpoints.py",
            ["monitor_checkpoints.py", "--checkpoint_dir", "/tmp/checkpoints"],
        )
        runner = MagicMock()
        module.OnPolicyRunner = MagicMock(return_value=runner)
        module.safe_load_framework_checkpoint = MagicMock(side_effect=ValueError("add_safe_globals"))
        monitor = object.__new__(module.CheckpointMonitor)
        monitor.agent_cfg = SimpleNamespace(
            class_name="OnPolicyRunner",
            device="cpu",
            to_dict=lambda: {},
        )
        monitor.env = SimpleNamespace(unwrapped=SimpleNamespace(device="cpu"))

        with pytest.raises(ValueError, match="add_safe_globals"):
            monitor._load_policy("/fake/model.pt")

        runner.get_inference_policy.assert_not_called()

    def test_checkpoint_monitor_rejects_malicious_checkpoint(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        module = _load_module(
            monkeypatch,
            "test_monitor_checkpoints_malicious_module",
            "evaluation/sil/monitor_checkpoints.py",
            ["monitor_checkpoints.py", "--checkpoint_dir", str(tmp_path)],
        )
        path = tmp_path / "model.pt"
        marker = tmp_path / "executed"
        torch.save(_Malicious(marker), path)
        runner = MagicMock()
        runner.load.side_effect = lambda checkpoint_path: torch.load(checkpoint_path, weights_only=False)
        module.OnPolicyRunner = MagicMock(return_value=runner)
        monitor = object.__new__(module.CheckpointMonitor)
        monitor.agent_cfg = SimpleNamespace(
            class_name="OnPolicyRunner",
            device="cpu",
            to_dict=lambda: {},
        )
        monitor.env = SimpleNamespace(unwrapped=SimpleNamespace(device="cpu"))

        with pytest.raises(ValueError, match="could not be loaded under weights_only=True"):
            monitor._load_policy(str(path))

        assert not marker.exists()
        runner.get_inference_policy.assert_not_called()

    def test_play_routes_load_through_safe_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = _load_module(
            monkeypatch,
            "test_play_module",
            "evaluation/sil/play.py",
            ["play.py"],
        )
        module.args_cli.task = "Walk-v0"
        module.args_cli.num_envs = None
        module.args_cli.device = None
        module.args_cli.use_pretrained_checkpoint = False
        module.args_cli.checkpoint = None
        module.args_cli.video = False
        env = SimpleNamespace(unwrapped=SimpleNamespace(device="cpu"))
        module.gym.make = MagicMock(return_value=env)
        runner = MagicMock()
        module.OnPolicyRunner = MagicMock(return_value=runner)

        class StopAfterCheckpointRouting(RuntimeError):
            pass

        safe_loader = MagicMock()
        module.safe_load_framework_checkpoint = safe_loader
        runner.get_inference_policy.side_effect = StopAfterCheckpointRouting
        env_cfg = SimpleNamespace(
            scene=SimpleNamespace(num_envs=1),
            sim=SimpleNamespace(device="cpu"),
            seed=0,
        )
        agent_cfg = SimpleNamespace(
            class_name="OnPolicyRunner",
            clip_actions=True,
            device="cpu",
            experiment_name="exp",
            load_checkpoint="model.pt",
            load_run="run",
            seed=0,
            to_dict=lambda: {},
        )

        with pytest.raises(StopAfterCheckpointRouting):
            module.main(env_cfg, agent_cfg)

        safe_loader.assert_called_once_with("/fake/model.pt", loader=runner.load)
        runner.load.assert_not_called()
