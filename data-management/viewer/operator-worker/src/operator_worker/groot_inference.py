"""Persistent GR00T N1.7 inference process for the operator worker."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
from lerobot.policies.factory import (  # type: ignore[import-untyped]
    make_pre_post_processors,
)
from lerobot.policies.groot.modeling_groot import (  # type: ignore[import-untyped]
    GrootPolicy,
)


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def _decode_image(payload: str) -> torch.Tensor:
    encoded = np.frombuffer(base64.b64decode(payload), dtype=np.uint8)
    bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("Policy image decode failed")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return torch.from_numpy(rgb).permute(2, 0, 1).float().div(255).unsqueeze(0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    args = parser.parse_args()
    policy = GrootPolicy.from_pretrained(args.checkpoint, strict=False)
    policy.to("cuda").eval()
    policy.config.device = "cuda"
    preprocessor, postprocessor = make_pre_post_processors(policy.config, pretrained_path=str(args.checkpoint))
    _emit({"type": "ready"})
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("type") != "predict":
                raise ValueError("Unsupported GR00T request")
            batch = {
                "observation.state": torch.tensor([request["state"]], dtype=torch.float32),
                "observation.images.wrist": _decode_image(request["images"]["wrist"]),
                "observation.images.front": _decode_image(request["images"]["front"]),
                "task": [request["task"]],
            }
            processed = preprocessor(batch)
            with torch.no_grad():
                relative_chunk = policy.predict_action_chunk(processed)
                action_chunk = postprocessor(relative_chunk)
            _emit(
                {
                    "type": "action_chunk",
                    "actions": action_chunk.detach().cpu().tolist()[0],
                }
            )
        except Exception as error:
            _emit({"type": "error", "message": str(error)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
