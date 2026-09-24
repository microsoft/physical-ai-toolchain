"""Supervision for a persistent local GR00T inference process."""

from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any

_MAX_POLICY_MESSAGE_BYTES = 8_000_000


class GrootPolicyClient:
    """Bounded JSON-line client for one GR00T model subprocess."""

    name = "policy"

    def __init__(
        self,
        *,
        python: Path,
        checkpoint: Path,
        cuda_visible_devices: str | None,
        timeout_s: float = 120.0,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("Policy timeout must be greater than zero")
        self.python = python
        self.checkpoint = checkpoint
        self.cuda_visible_devices = cuda_visible_devices
        self.timeout_s = timeout_s
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=2)
        self._stderr: deque[str] = deque(maxlen=100)

    def acquire(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        if self.cuda_visible_devices:
            environment["CUDA_VISIBLE_DEVICES"] = self.cuda_visible_devices
        script = Path(__file__).with_name("groot_inference.py")
        self._process = subprocess.Popen(
            [str(self.python), str(script), "--checkpoint", str(self.checkpoint)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=environment,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        ready = self._next_response()
        if ready.get("type") != "ready":
            raise RuntimeError(str(ready.get("message", "GR00T runtime failed to start")))

    def predict(self, observation: dict[str, Any], task: str) -> list[list[float]]:
        """Request one action chunk from the persistent inference process."""
        import cv2  # type: ignore[import-untyped]

        images: dict[str, str] = {}
        for name, frame in observation["images"].items():
            encoded, jpeg = cv2.imencode(
                ".jpg",
                cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 85],
            )
            if not encoded:
                raise RuntimeError(f"Failed to encode {name} policy frame")
            images[name] = base64.b64encode(jpeg.tobytes()).decode("ascii")
        self._send({"type": "predict", "state": observation["state"], "images": images, "task": task})
        response = self._next_response()
        if response.get("type") != "action_chunk":
            raise RuntimeError(str(response.get("message", "GR00T inference failed")))
        actions = response.get("actions")
        if not isinstance(actions, list):
            raise RuntimeError("GR00T inference returned an invalid action chunk")
        return actions

    def release(self) -> None:
        self.close()

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        self._process = None

    def _send(self, payload: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("GR00T runtime is not active")
        message = json.dumps(payload, separators=(",", ":"))
        if len(message.encode("utf-8")) > _MAX_POLICY_MESSAGE_BYTES:
            raise RuntimeError("GR00T request exceeds the policy message limit")
        self._process.stdin.write(message + "\n")
        self._process.stdin.flush()

    def _next_response(self) -> dict[str, Any]:
        try:
            return self._responses.get(timeout=self.timeout_s)
        except queue.Empty as error:
            detail = "".join(self._stderr)[-1_000:]
            raise RuntimeError(f"GR00T runtime timed out: {detail}") from error

    def _read_stdout(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        for line in self._process.stdout:
            if len(line.encode("utf-8")) > _MAX_POLICY_MESSAGE_BYTES:
                self._stderr.append("GR00T response exceeded the policy message limit")
                continue
            try:
                self._responses.put(json.loads(line), timeout=self.timeout_s)
            except (json.JSONDecodeError, queue.Full):
                self._stderr.append(line[-1_000:])

    def _read_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        for line in self._process.stderr:
            self._stderr.append(line[-1_000:])
