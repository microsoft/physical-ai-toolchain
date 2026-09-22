from __future__ import annotations

import os
import time

# torch is imported inside the functions on purpose. The remoter imports this
# module on the client as well, in order to wrap the target function, and the
# client image intentionally ships without torch or CUDA.

_CLASSES = 10
_SEED = 1234


def _build_model(torch_nn):
    """Small convolutional classifier with deterministic, seed-derived weights."""
    return torch_nn.Sequential(
        torch_nn.Conv2d(3, 16, kernel_size=3, padding=1),
        torch_nn.ReLU(),
        torch_nn.MaxPool2d(2),
        torch_nn.Conv2d(16, 32, kernel_size=3, padding=1),
        torch_nn.ReLU(),
        torch_nn.AdaptiveAvgPool2d(1),
        torch_nn.Flatten(),
        torch_nn.Linear(32, _CLASSES),
    )


def gpu_inference(batch: int = 8, image_size: int = 32) -> dict:
    """Run a convolutional forward pass on the GPU and prove where it executed.

    Returns plain Python types only; the MessagePack codec rejects tensors.
    The CPU reference pass runs the identical seeded model so a caller can
    confirm the GPU produced numerically correct results rather than noise.
    """
    import torch
    from torch import nn

    result: dict[str, object] = {
        "executed_by": os.environ.get("HOSTNAME", "unknown"),
        "torch_version": str(torch.__version__),
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_device_count": int(torch.cuda.device_count()),
    }

    if not result["cuda_available"]:
        result["device_type"] = "cpu"
        result["error"] = "CUDA is not available in the server container"
        return result

    device = torch.device("cuda:0")
    result["device_name"] = str(torch.cuda.get_device_name(device))
    major, minor = torch.cuda.get_device_capability(device)
    result["compute_capability"] = f"{major}.{minor}"
    result["cuda_runtime_version"] = str(torch.version.cuda) if torch.version.cuda else None
    result["total_memory_mib"] = int(torch.cuda.get_device_properties(device).total_memory / (1024 * 1024))

    torch.manual_seed(_SEED)
    model = _build_model(nn).eval()
    torch.manual_seed(_SEED)
    inputs = torch.randn(batch, 3, image_size, image_size)

    with torch.no_grad():
        cpu_logits = model(inputs)

        gpu_model = model.to(device)
        gpu_inputs = inputs.to(device)
        torch.cuda.synchronize(device)

        started = time.perf_counter()
        gpu_logits = gpu_model(gpu_inputs)
        torch.cuda.synchronize(device)
        result["forward_ms"] = round((time.perf_counter() - started) * 1000.0, 3)

        # A matmul large enough to be unambiguously GPU work.
        left = torch.randn(1024, 1024, device=device)
        right = torch.randn(1024, 1024, device=device)
        torch.cuda.synchronize(device)
        started = time.perf_counter()
        product = left @ right
        torch.cuda.synchronize(device)
        result["matmul_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        result["matmul_checksum"] = round(float(product.sum().item()), 4)

        result["logits_device"] = str(gpu_logits.device)
        result["device_type"] = str(gpu_logits.device.type)
        result["top1"] = int(gpu_logits[0].argmax().item())
        result["max_abs_diff_vs_cpu"] = float((gpu_logits.cpu() - cpu_logits).abs().max().item())
        result["peak_memory_mib"] = round(torch.cuda.max_memory_allocated(device) / (1024 * 1024), 3)

    return result
