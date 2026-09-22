from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class _UtilsCompat:
    """Subset of upstream k8sutils helpers used by Xavier remoter runtime."""

    def cleansocketpath(self, socket_path: str) -> None:
        try:
            if os.path.exists(socket_path):
                os.remove(socket_path)
        except FileNotFoundError:
            return

    def socketpath(self, base_path: str, namespace: str, pod_name: str, pod_uid: str) -> str:
        safe_name = f"{namespace}-{pod_name}-{pod_uid}".replace("/", "-")
        return os.path.join(base_path, f"{safe_name}.sock")


utils = _UtilsCompat()
