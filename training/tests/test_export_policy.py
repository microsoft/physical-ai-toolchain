from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

import torch
from conftest import load_training_module

_MOD = load_training_module("training_packaging_export_policy", "training/packaging/scripts/export_policy.py")


def _checkpoint_state() -> dict[str, object]:
    return {
        "model_state_dict": {
            "actor.0.weight": torch.arange(15, dtype=torch.float32).reshape(5, 3),
            "actor.0.bias": torch.arange(5, dtype=torch.float32),
            "actor.2.weight": torch.arange(20, dtype=torch.float32).reshape(4, 5),
            "actor.2.bias": torch.arange(4, dtype=torch.float32),
            "actor.4.weight": torch.arange(8, dtype=torch.float32).reshape(2, 4),
            "actor.4.bias": torch.arange(2, dtype=torch.float32),
        }
    }


def _write_checkpoint(path: Path) -> None:
    torch.save(_checkpoint_state(), path)


def _skrl_checkpoint_state() -> dict[str, object]:
    # SKRL shared-model layout: a net_container MLP trunk feeding a policy_layer mean head.
    # Dimensions mirror _checkpoint_state (obs 3 -> 5 -> 4 -> action 2) so both layouts
    # normalize to the same PolicyArchitecture.
    return {
        "policy": {
            "net_container.0.weight": torch.arange(15, dtype=torch.float32).reshape(5, 3),
            "net_container.0.bias": torch.arange(5, dtype=torch.float32),
            "net_container.2.weight": torch.arange(20, dtype=torch.float32).reshape(4, 5),
            "net_container.2.bias": torch.arange(4, dtype=torch.float32),
            "policy_layer.weight": torch.arange(8, dtype=torch.float32).reshape(2, 4),
            "policy_layer.bias": torch.arange(2, dtype=torch.float32),
        }
    }


class TestPolicyArchitecture:
    def test_str_formats_layer_dimensions(self) -> None:
        arch = _MOD.PolicyArchitecture(obs_dim=3, action_dim=2, hidden_dims=[5, 4])

        description = str(arch)

        assert description == "MLP(3 -> 5 -> 4 -> 2)"


class TestBasePolicyExporter:
    def test_forward_applies_normalizer_before_actor(self) -> None:
        actor = torch.nn.Linear(3, 2, bias=False)
        normalizer = torch.nn.BatchNorm1d(3, affine=False)
        exporter = _MOD._BasePolicyExporter(actor, normalizer)
        observations = torch.ones(2, 3)

        actual = exporter(observations)
        expected = exporter.actor(exporter.normalizer(observations))

        assert torch.equal(actual, expected)


class TestBuildMlp:
    def test_build_mlp_adds_hidden_activations_only(self) -> None:
        model = _MOD.build_mlp(3, 2, [5, 4], activation="relu")

        assert [type(layer) for layer in model] == [
            torch.nn.Linear,
            torch.nn.ReLU,
            torch.nn.Linear,
            torch.nn.ReLU,
            torch.nn.Linear,
        ]
        assert model[0].in_features == 3
        assert model[0].out_features == 5
        assert model[2].in_features == 5
        assert model[2].out_features == 4
        assert model[4].in_features == 4
        assert model[4].out_features == 2

    def test_build_mlp_defaults_unknown_activation_to_elu(self) -> None:
        model = _MOD.build_mlp(3, 2, [5], activation="gelu")

        assert isinstance(model[1], torch.nn.ELU)


class TestInferArchitectureFromCheckpoint:
    def test_infers_actor_dimensions_from_checkpoint(self, tmp_path: Path) -> None:
        checkpoint_path = tmp_path / "policy.pt"
        _write_checkpoint(checkpoint_path)

        arch = _MOD.infer_architecture_from_checkpoint(str(checkpoint_path))

        assert arch == _MOD.PolicyArchitecture(obs_dim=3, action_dim=2, hidden_dims=[5, 4], activation="elu")

    def test_loads_checkpoint_with_non_model_keys(self, tmp_path: Path) -> None:
        # Real RSL-RL checkpoints carry optimizer_state_dict/iter/infos beside
        # model_state_dict; weights_only=True must still load these safe-typed extras.
        checkpoint_path = tmp_path / "realistic.pt"
        state = _checkpoint_state()
        state["optimizer_state_dict"] = {"state": {}, "param_groups": [{"lr": 1e-3, "params": [0]}]}
        state["iter"] = 100
        state["infos"] = None
        torch.save(state, checkpoint_path)

        arch = _MOD.infer_architecture_from_checkpoint(str(checkpoint_path))

        assert arch == _MOD.PolicyArchitecture(obs_dim=3, action_dim=2, hidden_dims=[5, 4], activation="elu")

    def test_rejects_checkpoint_without_model_state_dict(self, tmp_path: Path) -> None:
        checkpoint_path = tmp_path / "missing-state.pt"
        torch.save({"epoch": 3}, checkpoint_path)

        with pytest.raises(ValueError, match="model_state_dict"):
            _MOD.infer_architecture_from_checkpoint(str(checkpoint_path))

    def test_rejects_checkpoint_without_actor_weights(self, tmp_path: Path) -> None:
        checkpoint_path = tmp_path / "missing-actor.pt"
        torch.save({"model_state_dict": {"critic.0.weight": torch.zeros(5, 3)}}, checkpoint_path)

        with pytest.raises(ValueError, match="No actor weights"):
            _MOD.infer_architecture_from_checkpoint(str(checkpoint_path))


class TestLoadActorFromCheckpoint:
    def test_loads_actor_with_inferred_architecture(self, tmp_path: Path) -> None:
        checkpoint_path = tmp_path / "policy.pt"
        _write_checkpoint(checkpoint_path)

        actor, normalizer, arch = _MOD.load_actor_from_checkpoint(str(checkpoint_path))

        assert normalizer is None
        assert arch == _MOD.PolicyArchitecture(obs_dim=3, action_dim=2, hidden_dims=[5, 4], activation="elu")
        assert isinstance(actor, torch.nn.Sequential)
        assert torch.equal(actor[0].weight, _checkpoint_state()["model_state_dict"]["actor.0.weight"])
        assert not actor.training

    def test_applies_architecture_overrides(self, tmp_path: Path) -> None:
        checkpoint_path = tmp_path / "policy.pt"
        state = {
            "model_state_dict": {
                "actor.0.weight": torch.ones(6, 4),
                "actor.0.bias": torch.ones(6),
                "actor.2.weight": torch.ones(3, 6),
                "actor.2.bias": torch.ones(3),
            }
        }
        torch.save(state, checkpoint_path)

        actor, _, arch = _MOD.load_actor_from_checkpoint(
            str(checkpoint_path),
            obs_dim=4,
            action_dim=3,
            hidden_dims=[6],
            activation="tanh",
        )

        assert arch == _MOD.PolicyArchitecture(obs_dim=4, action_dim=3, hidden_dims=[6], activation="tanh")
        assert isinstance(actor[1], torch.nn.Tanh)


class TestSkrlCheckpointSupport:
    def test_infers_skrl_actor_dimensions_from_checkpoint(self, tmp_path: Path) -> None:
        checkpoint_path = tmp_path / "skrl-policy.pt"
        torch.save(_skrl_checkpoint_state(), checkpoint_path)

        arch = _MOD.infer_architecture_from_checkpoint(str(checkpoint_path))

        assert arch == _MOD.PolicyArchitecture(obs_dim=3, action_dim=2, hidden_dims=[5, 4], activation="elu")

    def test_loads_skrl_actor_rekeying_trunk_and_head(self, tmp_path: Path) -> None:
        checkpoint_path = tmp_path / "skrl-policy.pt"
        torch.save(_skrl_checkpoint_state(), checkpoint_path)

        actor, normalizer, arch = _MOD.load_actor_from_checkpoint(str(checkpoint_path))

        policy = _skrl_checkpoint_state()["policy"]
        assert normalizer is None
        assert arch == _MOD.PolicyArchitecture(obs_dim=3, action_dim=2, hidden_dims=[5, 4], activation="elu")
        assert isinstance(actor, torch.nn.Sequential)
        # net_container trunk -> Sequential indices 0, 2; policy_layer head -> index 4.
        assert torch.equal(actor[0].weight, policy["net_container.0.weight"])
        assert torch.equal(actor[2].weight, policy["net_container.2.weight"])
        assert torch.equal(actor[4].weight, policy["policy_layer.weight"])
        assert not actor.training

    def test_rejects_skrl_policy_without_head(self, tmp_path: Path) -> None:
        checkpoint_path = tmp_path / "headless-skrl.pt"
        torch.save(
            {"policy": {"net_container.0.weight": torch.zeros(5, 3), "net_container.0.bias": torch.zeros(5)}},
            checkpoint_path,
        )

        with pytest.raises(ValueError, match="policy_layer head"):
            _MOD.infer_architecture_from_checkpoint(str(checkpoint_path))

    def test_rejects_checkpoint_matching_no_known_layout(self, tmp_path: Path) -> None:
        checkpoint_path = tmp_path / "unknown.pt"
        torch.save({"policy": {"value_layer.weight": torch.zeros(1, 3)}}, checkpoint_path)

        with pytest.raises(ValueError, match="Unsupported checkpoint"):
            _MOD.infer_architecture_from_checkpoint(str(checkpoint_path))


class TestPolicyExporters:
    def test_torch_export_saves_scripted_policy(self, mocker: MockerFixture, tmp_path: Path) -> None:
        actor = torch.nn.Linear(3, 2)
        exporter = _MOD._TorchPolicyExporter(actor)
        scripted = mocker.Mock()
        script = mocker.patch.object(_MOD.torch.jit, "script", return_value=scripted)

        filepath = exporter.export(str(tmp_path), "policy.pt")

        assert filepath == str(tmp_path / "policy.pt")
        script.assert_called_once_with(exporter)
        scripted.save.assert_called_once_with(str(tmp_path / "policy.pt"))
        assert (tmp_path).exists()

    def test_onnx_export_uses_dynamic_batch_axes(self, mocker: MockerFixture, tmp_path: Path) -> None:
        actor = torch.nn.Linear(3, 2)
        exporter = _MOD._OnnxPolicyExporter(actor)
        onnx_export = mocker.patch.object(_MOD.torch.onnx, "export")

        filepath = exporter.export(str(tmp_path), obs_dim=3, filename="policy.onnx")

        assert filepath == str(tmp_path / "policy.onnx")
        onnx_export.assert_called_once()
        args, kwargs = onnx_export.call_args
        assert args[0] is exporter
        assert args[2] == str(tmp_path / "policy.onnx")
        assert args[1].shape == (1, 3)
        assert kwargs["opset_version"] == 18
        assert kwargs["dynamic_axes"] == {
            "obs": {0: "batch_size"},
            "actions": {0: "batch_size"},
        }


class TestExportPolicy:
    def test_exports_requested_formats(self, mocker: MockerFixture, tmp_path: Path) -> None:
        actor = torch.nn.Linear(3, 2)
        arch = _MOD.PolicyArchitecture(obs_dim=3, action_dim=2, hidden_dims=[])
        load_actor = mocker.patch.object(_MOD, "load_actor_from_checkpoint", return_value=(actor, None, arch))
        jit_export = mocker.patch.object(_MOD._TorchPolicyExporter, "export", return_value="/exports/policy.pt")
        onnx_export = mocker.patch.object(_MOD._OnnxPolicyExporter, "export", return_value="/exports/policy.onnx")
        sidecar = mocker.patch.object(_MOD, "write_sha256_sidecar")

        exported = _MOD.export_policy(
            checkpoint_path="checkpoint.pt",
            output_dir=str(tmp_path),
            obs_dim=3,
            action_dim=2,
            hidden_dims=[],
            activation="relu",
        )

        assert exported == {"jit": "/exports/policy.pt", "onnx": "/exports/policy.onnx"}
        load_actor.assert_called_once_with("checkpoint.pt", 3, 2, [], "relu")
        jit_export.assert_called_once_with(str(tmp_path), "policy.pt")
        onnx_export.assert_called_once_with(str(tmp_path), 3, "policy.onnx")
        assert sidecar.call_args_list == [mocker.call("/exports/policy.pt"), mocker.call("/exports/policy.onnx")]

    def test_onnx_failure_still_exports_jit(self, mocker: MockerFixture, tmp_path: Path) -> None:
        actor = torch.nn.Linear(3, 2)
        arch = _MOD.PolicyArchitecture(obs_dim=3, action_dim=2, hidden_dims=[])
        mocker.patch.object(_MOD, "load_actor_from_checkpoint", return_value=(actor, None, arch))
        sidecar = mocker.patch.object(_MOD, "write_sha256_sidecar")
        mocker.patch.object(_MOD._TorchPolicyExporter, "export", return_value=str(tmp_path / "policy.pt"))
        mocker.patch.object(_MOD._OnnxPolicyExporter, "export", side_effect=RuntimeError("no onnx"))

        exported = _MOD.export_policy(checkpoint_path="checkpoint.pt", output_dir=str(tmp_path))

        assert exported == {"jit": str(tmp_path / "policy.pt")}
        assert "onnx" not in exported
        sidecar.assert_called_once_with(str(tmp_path / "policy.pt"))

    def test_raises_when_all_requested_exports_fail(self, mocker: MockerFixture, tmp_path: Path) -> None:
        actor = torch.nn.Linear(3, 2)
        arch = _MOD.PolicyArchitecture(obs_dim=3, action_dim=2, hidden_dims=[])
        mocker.patch.object(_MOD, "load_actor_from_checkpoint", return_value=(actor, None, arch))
        mocker.patch.object(_MOD, "write_sha256_sidecar")
        mocker.patch.object(_MOD._OnnxPolicyExporter, "export", side_effect=RuntimeError("no onnx"))

        with pytest.raises(RuntimeError, match="No policy formats were exported"):
            _MOD.export_policy(checkpoint_path="checkpoint.pt", output_dir=str(tmp_path), export_jit=False)

    def test_skips_disabled_export_formats(self, mocker: MockerFixture, tmp_path: Path) -> None:
        actor = torch.nn.Linear(3, 2)
        arch = _MOD.PolicyArchitecture(obs_dim=3, action_dim=2, hidden_dims=[])
        mocker.patch.object(_MOD, "load_actor_from_checkpoint", return_value=(actor, None, arch))
        jit_export = mocker.patch.object(_MOD._TorchPolicyExporter, "export")
        onnx_export = mocker.patch.object(_MOD._OnnxPolicyExporter, "export")

        exported = _MOD.export_policy(
            checkpoint_path="checkpoint.pt",
            output_dir=str(tmp_path),
            export_jit=False,
            export_onnx=False,
        )

        assert exported == {}
        jit_export.assert_not_called()
        onnx_export.assert_not_called()


class TestSha256Sidecar:
    def test_writes_sha256sum_format_matching_content(self, tmp_path: Path) -> None:
        import hashlib

        model_path = tmp_path / "policy.pt"
        model_path.write_bytes(b"scripted-model-bytes")

        sidecar = _MOD.write_sha256_sidecar(str(model_path))

        expected = hashlib.sha256(b"scripted-model-bytes").hexdigest()
        assert sidecar == str(model_path) + ".sha256"
        assert Path(sidecar).read_text(encoding="utf-8") == f"{expected}  policy.pt\n"

    def test_load_rejects_weights_only_incompatible_payload(self, tmp_path: Path) -> None:
        # weights_only=True must refuse a pickle carrying an arbitrary object.
        # argparse.Namespace is picklable but not in torch's safe-globals allowlist,
        # so the load is rejected and surfaced as an actionable error that chains the cause.
        import argparse
        import pickle

        checkpoint_path = tmp_path / "evil.pt"
        torch.save({"model_state_dict": {}, "payload": argparse.Namespace(cmd="rm -rf /")}, checkpoint_path)

        with pytest.raises(ValueError, match="add_safe_globals") as excinfo:
            _MOD.infer_architecture_from_checkpoint(str(checkpoint_path))
        assert isinstance(excinfo.value.__cause__, pickle.UnpicklingError)
