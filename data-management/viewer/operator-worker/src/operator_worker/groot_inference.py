"""Persistent GR00T N1.7 inference subprocess."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

_MAX_REQUEST_BYTES = 8_000_000


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def _decode_image(payload: str) -> Any:
    import cv2  # type: ignore[import-untyped]
    import numpy as np
    import torch

    encoded = np.frombuffer(base64.b64decode(payload, validate=True), dtype=np.uint8)
    bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("Policy image decode failed")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return torch.from_numpy(rgb).permute(2, 0, 1).float().div(255).unsqueeze(0)


def create_parser() -> argparse.ArgumentParser:
    """Create the inference subprocess argument parser."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    return parser


def main() -> int:
    """Load one policy and serve bounded JSON-line prediction requests."""
    import torch
    from lerobot.policies.factory import make_pre_post_processors  # type: ignore[import-untyped]
    from lerobot.policies.groot.modeling_groot import GrootPolicy  # type: ignore[import-untyped]

    args = create_parser().parse_args()
    policy = GrootPolicy.from_pretrained(args.checkpoint, strict=False)
    policy.to("cuda").eval()
    policy.config.device = "cuda"
    preprocessor, postprocessor = make_pre_post_processors(policy.config, pretrained_path=str(args.checkpoint))
    _emit({"type": "ready"})
    for line in sys.stdin:
        try:
            if len(line.encode("utf-8")) > _MAX_REQUEST_BYTES:
                raise ValueError("GR00T request exceeds the policy message limit")
            request = json.loads(line)
            if request.get("type") != "predict":
                raise ValueError("Unsupported GR00T request")
            batch = {
                "observation.state": torch.tensor([request["state"]], dtype=torch.float32),
                "observation.images.wrist": _decode_image(request["images"]["wrist"]),
                "observation.images.front": _decode_image(request["images"]["front"]),
                "task": [request["task"]],
            }
            with torch.no_grad():
                action_chunk = postprocessor(policy.predict_action_chunk(preprocessor(batch)))
            _emit({"type": "action_chunk", "actions": action_chunk.detach().cpu().tolist()[0]})
        except Exception as error:
            _emit({"type": "error", "message": str(error)[:1_000]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
