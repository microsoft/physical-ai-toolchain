# Model Packaging

Policy export and packaging for deployment to inference runtimes.

## Status

Active — supports ONNX and TensorRT export paths.

## Components

| Component | Description |
|-----------|-------------|
| `export_policy.py` | Converts trained policy checkpoints to deployment-ready formats |
| ONNX export | Framework-agnostic model serialization |
| TensorRT optimization | NVIDIA GPU-optimized inference acceleration |

## Export Flow

1. Training produces a checkpoint (PyTorch `.pt` file)
2. `export_policy.py` loads the checkpoint and traces the model
3. Model is exported to ONNX format
4. TensorRT converts ONNX to an optimized engine (optional)

## Location

Export tooling is in `training/packaging/scripts/`.
