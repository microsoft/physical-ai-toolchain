from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

from training.rl.scripts import runtime_provenance


class _Distribution:
    def __init__(self, root: Path, version: str) -> None:
        self._root = root
        self.version = version

    def locate_file(self, path: str) -> Path:
        return self._root / path


def test_installed_versions_rejects_inactive_duplicate_distribution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    active_root = tmp_path / "active"
    inactive_root = tmp_path / "inactive"
    monkeypatch.setattr(runtime_provenance.sysconfig, "get_path", lambda name: str(active_root))
    monkeypatch.setattr(
        importlib.metadata,
        "distributions",
        lambda *, name: [
            _Distribution(inactive_root / "first", "9.9.9"),
            _Distribution(inactive_root / "second", "8.8.8"),
        ],
    )

    actual, missing, unresolved = runtime_provenance._installed_versions({"example": "1.0.0"})

    assert actual == {}
    assert missing == []
    assert unresolved == ["example"]
