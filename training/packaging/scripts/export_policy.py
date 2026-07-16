"""Standalone policy export script for RSL-RL and SKRL checkpoints.

Exports trained policy checkpoints to JIT (TorchScript) and ONNX formats without
requiring the full Isaac Lab simulator environment.

Auto-detects observation and action dimensions from checkpoint weights.
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass

import torch
import torch.nn as nn

from training.utils.hashing import write_sha256_sidecar
from training.utils.integrity import safe_load_checkpoint


@dataclass
class PolicyArchitecture:
    """Inferred policy network architecture from checkpoint."""

    obs_dim: int
    action_dim: int
    hidden_dims: list[int]
    activation: str = "elu"

    def __str__(self) -> str:
        layers = [self.obs_dim, *self.hidden_dims, self.action_dim]
        return f"MLP({' -> '.join(map(str, layers))})"


class _BasePolicyExporter(nn.Module):
    """Base class wrapping actor network with normalizer for export."""

    def __init__(self, actor: nn.Module, normalizer: nn.Module | None = None):
        super().__init__()
        self.actor = copy.deepcopy(actor)
        self.normalizer = copy.deepcopy(normalizer) if normalizer else nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.actor(self.normalizer(x))


class _TorchPolicyExporter(_BasePolicyExporter):
    """Exports actor network to JIT (TorchScript) format."""

    def export(self, path: str, filename: str = "policy.pt") -> str:
        os.makedirs(path, exist_ok=True)
        filepath = os.path.join(path, filename)
        self.to("cpu")
        self.eval()
        scripted = torch.jit.script(self)
        scripted.save(filepath)
        return filepath


class _OnnxPolicyExporter(_BasePolicyExporter):
    """Exports actor network to ONNX format."""

    def export(self, path: str, obs_dim: int, filename: str = "policy.onnx") -> str:
        os.makedirs(path, exist_ok=True)
        filepath = os.path.join(path, filename)
        self.to("cpu")
        self.eval()

        dummy_input = torch.zeros(1, obs_dim)
        torch.onnx.export(
            self,
            dummy_input,
            filepath,
            export_params=True,
            opset_version=18,
            input_names=["obs"],
            output_names=["actions"],
            dynamic_axes={"obs": {0: "batch_size"}, "actions": {0: "batch_size"}},
        )
        return filepath


def build_mlp(
    input_dim: int,
    output_dim: int,
    hidden_dims: list[int],
    activation: str = "elu",
) -> nn.Sequential:
    """Build MLP network matching RSL-RL ActorCritic architecture."""
    activation_fn = {"elu": nn.ELU, "relu": nn.ReLU, "tanh": nn.Tanh}.get(activation.lower(), nn.ELU)

    layers = []
    dims = [input_dim, *hidden_dims, output_dim]

    for i in range(len(dims) - 1):
        layers.append(nn.Linear(dims[i], dims[i + 1]))
        if i < len(dims) - 2:
            layers.append(activation_fn())

    return nn.Sequential(*layers)


def _extract_actor_state(checkpoint: Mapping[str, object]) -> tuple[dict[str, torch.Tensor], PolicyArchitecture]:
    """Return actor weights re-keyed for ``build_mlp`` plus the inferred architecture.

    Supports two trained-checkpoint layouts:

    - RSL-RL ActorCritic: a top-level ``model_state_dict`` whose ``actor.<i>.{weight,bias}``
      keys form an ``nn.Sequential`` MLP.
    - SKRL shared model (Isaac Lab): a top-level ``policy`` module with a ``net_container``
      trunk and a ``policy_layer`` mean head.

    Both are normalized to ``build_mlp`` Sequential indices (``0.weight``, ``2.weight``, ...).

    Raises:
        ValueError: If the checkpoint matches neither layout or lacks actor weights.
    """
    if "model_state_dict" in checkpoint:
        state_dict = checkpoint["model_state_dict"]
        actor_weights = sorted(
            [(k, v) for k, v in state_dict.items() if k.startswith("actor.") and "weight" in k],
            key=lambda kv: int(kv[0].split(".")[1]),
        )
        if not actor_weights:
            raise ValueError("No actor weights found in RSL-RL checkpoint")
        actor_state = {k[len("actor.") :]: v for k, v in state_dict.items() if k.startswith("actor.")}
        obs_dim = actor_weights[0][1].shape[1]
        action_dim = actor_weights[-1][1].shape[0]
        hidden_dims = [w.shape[0] for _, w in actor_weights[:-1]]
        return actor_state, PolicyArchitecture(obs_dim, action_dim, hidden_dims)

    policy = checkpoint.get("policy")
    if isinstance(policy, Mapping) and any(k.startswith("net_container.") for k in policy):
        trunk_layers = sorted(
            int(k.split(".")[1]) for k in policy if k.startswith("net_container.") and k.endswith(".weight")
        )
        if not trunk_layers or "policy_layer.weight" not in policy:
            raise ValueError("SKRL policy missing net_container trunk or policy_layer head")

        actor_state: dict[str, torch.Tensor] = {}
        hidden_dims = []
        for position, layer in enumerate(trunk_layers):
            weight = policy[f"net_container.{layer}.weight"]
            actor_state[f"{2 * position}.weight"] = weight
            actor_state[f"{2 * position}.bias"] = policy[f"net_container.{layer}.bias"]
            hidden_dims.append(weight.shape[0])
        head_index = 2 * len(trunk_layers)
        actor_state[f"{head_index}.weight"] = policy["policy_layer.weight"]
        actor_state[f"{head_index}.bias"] = policy["policy_layer.bias"]

        obs_dim = policy[f"net_container.{trunk_layers[0]}.weight"].shape[1]
        action_dim = policy["policy_layer.weight"].shape[0]
        return actor_state, PolicyArchitecture(obs_dim, action_dim, hidden_dims)

    raise ValueError("Unsupported checkpoint: expected RSL-RL 'model_state_dict' or SKRL 'policy' module")


def infer_architecture_from_checkpoint(checkpoint_path: str) -> PolicyArchitecture:
    """Infer policy architecture from an RSL-RL or SKRL checkpoint.

    Automatically detects observation dimension, action dimension, and hidden layer
    sizes by inspecting the actor network weight shapes in the checkpoint.

    Args:
        checkpoint_path: Path to the model checkpoint (.pt file).

    Returns:
        PolicyArchitecture with inferred dimensions.

    Raises:
        ValueError: If checkpoint structure is invalid or actor weights not found.
    """
    checkpoint = safe_load_checkpoint(checkpoint_path)
    _actor_state, arch = _extract_actor_state(checkpoint)
    return arch


def load_actor_from_checkpoint(
    checkpoint_path: str,
    obs_dim: int | None = None,
    action_dim: int | None = None,
    hidden_dims: list[int] | None = None,
    activation: str = "elu",
) -> tuple[nn.Module, nn.Module | None, PolicyArchitecture]:
    """Load actor network from an RSL-RL or SKRL checkpoint.

    If obs_dim, action_dim, or hidden_dims are not provided, they are automatically
    inferred from the checkpoint weights.

    Args:
        checkpoint_path: Path to the model checkpoint (.pt file).
        obs_dim: Observation dimension (auto-detected if None).
        action_dim: Action dimension (auto-detected if None).
        hidden_dims: Hidden layer dimensions (auto-detected if None).
        activation: Activation function name.

    Returns:
        Tuple of (actor_network, normalizer_or_none, architecture).
    """
    checkpoint = safe_load_checkpoint(checkpoint_path)
    actor_state, arch = _extract_actor_state(checkpoint)

    # Override with user-provided values if specified
    if obs_dim is not None:
        arch.obs_dim = obs_dim
    if action_dim is not None:
        arch.action_dim = action_dim
    if hidden_dims is not None:
        arch.hidden_dims = hidden_dims
    arch.activation = activation

    print(f"Policy architecture: {arch}")

    actor = build_mlp(arch.obs_dim, arch.action_dim, arch.hidden_dims, arch.activation)
    actor.load_state_dict(actor_state)
    actor.eval()

    return actor, None, arch


def export_policy(
    checkpoint_path: str,
    output_dir: str,
    obs_dim: int | None = None,
    action_dim: int | None = None,
    hidden_dims: list[int] | None = None,
    activation: str = "elu",
    export_jit: bool = True,
    export_onnx: bool = True,
) -> dict[str, str]:
    """Export an RSL-RL or SKRL checkpoint to JIT and/or ONNX formats.

    Automatically detects observation and action dimensions from the checkpoint
    if not explicitly provided.

    Args:
        checkpoint_path: Path to the model checkpoint.
        output_dir: Directory to save exported models.
        obs_dim: Observation dimension (auto-detected if None).
        action_dim: Action dimension (auto-detected if None).
        hidden_dims: Hidden layer dimensions (auto-detected if None).
        activation: Activation function.
        export_jit: Whether to export JIT model.
        export_onnx: Whether to export ONNX model.

    Returns:
        Dictionary mapping format to export path.
    """
    print(f"Loading checkpoint: {checkpoint_path}")
    actor, normalizer, arch = load_actor_from_checkpoint(checkpoint_path, obs_dim, action_dim, hidden_dims, activation)

    exported = {}

    if export_jit:
        print("Exporting JIT model...")
        exporter = _TorchPolicyExporter(actor, normalizer)
        jit_path = exporter.export(output_dir, "policy.pt")
        write_sha256_sidecar(jit_path)
        exported["jit"] = jit_path
        print(f"  -> {jit_path}")

    if export_onnx:
        print("Exporting ONNX model...")
        try:
            exporter = _OnnxPolicyExporter(actor, normalizer)
            onnx_path = exporter.export(output_dir, arch.obs_dim, "policy.onnx")
            write_sha256_sidecar(onnx_path)
            exported["onnx"] = onnx_path
            print(f"  -> {onnx_path}")
        except Exception as exc:  # ONNX export is best-effort; JIT is the primary format
            print(f"  ONNX export unavailable, skipping: {exc}")

    if (export_jit or export_onnx) and not exported:
        raise RuntimeError("No policy formats were exported (all requested exports failed)")

    return exported


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export an RSL-RL or SKRL checkpoint to JIT/ONNX (auto-detects dimensions)"
    )
    parser.add_argument("--checkpoint", required=True, help="Path to checkpoint file")
    parser.add_argument("--output-dir", help="Output directory (default: checkpoint_dir/exported)")
    parser.add_argument(
        "--obs-dim",
        type=int,
        default=None,
        help="Observation dimension (auto-detected from checkpoint if not provided)",
    )
    parser.add_argument(
        "--action-dim",
        type=int,
        default=None,
        help="Action dimension (auto-detected from checkpoint if not provided)",
    )
    parser.add_argument(
        "--hidden-dims",
        type=int,
        nargs="+",
        default=None,
        help="Hidden layer dimensions (auto-detected from checkpoint if not provided)",
    )
    parser.add_argument("--activation", default="elu", help="Activation function")
    parser.add_argument("--no-jit", action="store_true", help="Skip JIT export")
    parser.add_argument("--no-onnx", action="store_true", help="Skip ONNX export")
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Only inspect checkpoint architecture, don't export",
    )
    args = parser.parse_args()

    # Inspect mode - just show architecture
    if args.inspect:
        arch = infer_architecture_from_checkpoint(args.checkpoint)
        print(f"\nCheckpoint: {args.checkpoint}")
        print(f"Architecture: {arch}")
        print(f"  Observation dim: {arch.obs_dim}")
        print(f"  Action dim: {arch.action_dim}")
        print(f"  Hidden dims: {arch.hidden_dims}")
        sys.exit()

    output_dir = args.output_dir or os.path.join(os.path.dirname(args.checkpoint), "exported")

    exported = export_policy(
        checkpoint_path=args.checkpoint,
        output_dir=output_dir,
        obs_dim=args.obs_dim,
        action_dim=args.action_dim,
        hidden_dims=args.hidden_dims,
        activation=args.activation,
        export_jit=not args.no_jit,
        export_onnx=not args.no_onnx,
    )

    print(f"\nExported {len(exported)} model(s) to: {output_dir}")
