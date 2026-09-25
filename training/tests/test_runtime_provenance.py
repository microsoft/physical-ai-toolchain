from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
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


def _write_distribution(root: Path, version: str) -> None:
    distribution = root / f"overlay_probe-{version}.dist-info"
    distribution.mkdir(parents=True)
    (distribution / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: overlay-probe\nVersion: {version}\n",
        encoding="utf-8",
    )


def test_installed_versions_prefers_explicit_overlay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = tmp_path / "base"
    overlay = tmp_path / "overlay"
    _write_distribution(base, "1.0.0")
    _write_distribution(overlay, "2.0.0")
    monkeypatch.syspath_prepend(str(base))
    monkeypatch.syspath_prepend(str(overlay))
    monkeypatch.setattr(runtime_provenance.sysconfig, "get_path", lambda name: str(base))

    actual, missing, unresolved = runtime_provenance._installed_versions(
        {"overlay-probe": "2.0.0"}, install_root=overlay
    )

    assert actual == {"overlay-probe": "2.0.0"}
    assert missing == []
    assert unresolved == []


def test_explicit_overlay_does_not_fall_back_to_base(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base = tmp_path / "base"
    _write_distribution(base, "2.0.0")
    monkeypatch.syspath_prepend(str(base))
    monkeypatch.setattr(runtime_provenance.sysconfig, "get_path", lambda name: str(base))

    actual, missing, unresolved = runtime_provenance._installed_versions(
        {"overlay-probe": "2.0.0"}, install_root=tmp_path / "overlay"
    )

    assert actual == {}
    assert missing == []
    assert unresolved == ["overlay-probe"]


def test_provenance_cli_reports_overlay_versions(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    _write_distribution(overlay, "2.0.0")
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("overlay-probe==2.0.0\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            runtime_provenance.__file__,
            str(tmp_path),
            str(requirements),
            "--install-root",
            str(overlay),
        ],
        env={**os.environ, "PYTHONPATH": str(overlay)},
        capture_output=True,
        text=True,
        check=True,
    )
    provenance = json.loads(result.stdout.removeprefix("RUNTIME_PROVENANCE="))

    assert provenance["actual"] == provenance["expected"] == {"overlay-probe": "2.0.0"}
    assert provenance["missing"] == []
    assert provenance["unresolved"] == []
