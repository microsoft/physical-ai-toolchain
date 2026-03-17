"""Standalone policy export script for RSL-RL checkpoints.

Exports RSL-RL trained policy checkpoints to JIT (TorchScript) and ONNX formats
without requiring the full IsaacLab simulator environment.

Auto-detects observation and action dimensions from checkpoint weights.
"""

import argparse
import copy
import os
import sys
from dataclasses import dataclass

import torch
import torch.nn as nn


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


def infer_architecture_from_checkpoint(checkpoint_path: str) -> PolicyArchitecture:
    """Infer policy architecture from RSL-RL checkpoint weights.

    Automatically detects observation dimension, action dimension, and hidden layer
    sizes by inspecting the actor network weight shapes in the checkpoint.

    Args:
        checkpoint_path: Path to the model checkpoint (.pt file).

    Returns:
        PolicyArchitecture with inferred dimensions.

    Raises:
        ValueError: If checkpoint structure is invalid or actor weights not found.
    """
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)

    if "model_state_dict" not in checkpoint:
        raise ValueError("Checkpoint missing 'model_state_dict' key")

    state_dict = checkpoint["model_state_dict"]

    # Find all actor layer weights (excluding biases)
    actor_weights = sorted(
        [(k, v) for k, v in state_dict.items() if k.startswith("actor.") and "weight" in k],
        key=lambda x: int(x[0].split(".")[1]),  # Sort by layer index
    )

    if not actor_weights:
        raise ValueError("No actor weights found in checkpoint")

    # First layer input dim = obs_dim
    _first_layer_key, first_layer_weight = actor_weights[0]
    obs_dim = first_layer_weight.shape[1]

    # Last layer output dim = action_dim
    _last_layer_key, last_layer_weight = actor_weights[-1]
    action_dim = last_layer_weight.shape[0]

    # Hidden dims = output dims of all layers except the last
    hidden_dims = [w.shape[0] for _, w in actor_weights[:-1]]

    return PolicyArchitecture(
        obs_dim=obs_dim,
        action_dim=action_dim,
        hidden_dims=hidden_dims,
        activation="elu",  # RSL-RL default
    )


def load_actor_from_checkpoint(
    checkpoint_path: str,
    obs_dim: int | None = None,
    action_dim: int | None = None,
    hidden_dims: list[int] | None = None,
    activation: str = "elu",
) -> tuple[nn.Module, nn.Module | None, PolicyArchitecture]:
    """Load actor network from RSL-RL checkpoint.

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
    # Auto-detect architecture if not fully specified
    arch = infer_architecture_from_checkpoint(checkpoint_path)

    # Override with user-provided values if specified
    if obs_dim is not None:
        arch.obs_dim = obs_dim
    if action_dim is not None:
        arch.action_dim = action_dim
    if hidden_dims is not None:
        arch.hidden_dims = hidden_dims
    arch.activation = activation

    print(f"Policy architecture: {arch}")

    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    state_dict = checkpoint["model_state_dict"]

    # Build actor network
    actor = build_mlp(arch.obs_dim, arch.action_dim, arch.hidden_dims, arch.activation)

    # Load actor weights
    actor_state = {}
    for key in state_dict:
        if key.startswith("actor."):
            new_key = key.replace("actor.", "")
            actor_state[new_key] = state_dict[key]

    actor.load_state_dict(actor_state)
    actor.eval()

    # Check for normalizer
    normalizer = None
    normalizer_keys = [k for k in state_dict if "normalizer" in k.lower()]
    if normalizer_keys:
        print(f"Found normalizer keys: {normalizer_keys}")

    return actor, normalizer, arch


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
    """Export RSL-RL checkpoint to JIT and/or ONNX formats.

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
        exported["jit"] = jit_path
        print(f"  -> {jit_path}")

    if export_onnx:
        print("Exporting ONNX model...")
        exporter = _OnnxPolicyExporter(actor, normalizer)
        onnx_path = exporter.export(output_dir, arch.obs_dim, "policy.onnx")
        exported["onnx"] = onnx_path
        print(f"  -> {onnx_path}")

    return exported


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export RSL-RL checkpoint to JIT/ONNX (auto-detects dimensions)")
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
