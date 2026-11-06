"""Patch Cosmos autoregressive FPS, then delegate to examples.multiview."""

from __future__ import annotations

import os
import runpy


def patch_autoregressive_fps() -> None:
    from cosmos_transfer2._src.transfer2_multiview.inference.inference import ControlVideo2WorldInference

    original = ControlVideo2WorldInference._create_chunk_batch
    if getattr(original, "_carla_cosmos_fps_patch", False):
        return

    def _create_chunk_batch_with_source_fps(self, original_batch, *args, **kwargs):
        chunk_batch = original(self, original_batch, *args, **kwargs)
        fps = original_batch.get("fps")
        if fps is not None:
            chunk_batch["fps"] = fps.clone() if hasattr(fps, "clone") else fps
        return chunk_batch

    _create_chunk_batch_with_source_fps._carla_cosmos_fps_patch = True
    ControlVideo2WorldInference._create_chunk_batch = _create_chunk_batch_with_source_fps


def main() -> None:
    if os.environ.get("CARLA_COSMOS_NO_FPS_PATCH", "0") != "1":
        patch_autoregressive_fps()
    runpy.run_module("examples.multiview", run_name="__main__")


if __name__ == "__main__":
    main()
