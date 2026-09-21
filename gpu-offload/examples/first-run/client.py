"""Control-container entry point for the first-run offload check.

Nothing here imports the remoter SDK. The image places its sitecustomize hook on
PYTHONPATH, so the interpreter starts the offload runtime before this module is
loaded and the functions named in remote.yaml are already rewritten into remote
calls by the time they are imported below.
"""

from __future__ import annotations

import json
import os
import time

# cspell:ignore nvidiactl


def client_gpu_devices() -> list[str]:
    """GPU device nodes visible to the client container.

    The client never requests a GPU, so this must stay empty. It is the negative
    control for the offload check: identical code reports a CUDA device only
    because it executed on the remote server stage.
    """
    return [path for path in ("/dev/nvidiactl", "/dev/nvidia0", "/dev/dxg") if os.path.exists(path)]


def main() -> None:
    # Imported after the interpreter has started the offload runtime, so predict
    # is already the remoted version. gpu_model stays behind the GPU_CHECK gate:
    # on CPU platforms it is absent from remote.yaml, and calling it locally would
    # fail on the torch the client image deliberately does not ship.
    from demo_model import predict

    gpu_inference = None
    if os.environ.get("GPU_CHECK", "false").lower() == "true":
        from gpu_model import gpu_inference

    while True:
        print(json.dumps(predict([1, 2, 3, 4])), flush=True)

        if gpu_inference is not None:
            report = {
                "gpu_check": gpu_inference(),
                "client_host": os.environ.get("HOSTNAME", "unknown"),
                "client_gpu_devices": client_gpu_devices(),
            }
            print(json.dumps(report), flush=True)

        time.sleep(10)


if __name__ == "__main__":
    main()
