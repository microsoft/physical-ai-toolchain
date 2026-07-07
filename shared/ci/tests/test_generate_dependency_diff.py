# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Unit tests for the SBOM dependency-diff generator."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "generate-dependency-diff.py"


def _load_generator() -> ModuleType:
    """Load the hyphenated generator script as an importable module."""
    spec = importlib.util.spec_from_file_location("generate_dependency_diff", _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GENERATOR = _load_generator()


def _write_sbom(path: Path, packages: dict[str, str]) -> Path:
    """Write a minimal SPDX SBOM containing the given package name/version pairs."""
    document = {
        "spdxVersion": "SPDX-2.3",
        "packages": [{"name": name, "versionInfo": version} for name, version in packages.items()],
    }
    path.write_text(json.dumps(document))
    return path


class TestLoadPackages:
    def test_reads_name_and_version(self, tmp_path: Path) -> None:
        sbom = _write_sbom(tmp_path / "sbom.json", {"numpy": "2.5.0", "torch": "2.12.1"})
        assert _GENERATOR.load_packages(sbom) == {"numpy": "2.5.0", "torch": "2.12.1"}

    def test_missing_fields_use_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "sbom.json"
        path.write_text(json.dumps({"packages": [{}]}))
        assert _GENERATOR.load_packages(path) == {"UNKNOWN": "unknown"}

    def test_absent_packages_key(self, tmp_path: Path) -> None:
        path = tmp_path / "sbom.json"
        path.write_text(json.dumps({"spdxVersion": "SPDX-2.3"}))
        assert _GENERATOR.load_packages(path) == {}


class TestWriteDependencyDiff:
    def test_added_dependency(self, tmp_path: Path) -> None:
        current = _write_sbom(tmp_path / "current.json", {"numpy": "2.5.0"})
        previous = _write_sbom(tmp_path / "previous.json", {})
        output = tmp_path / "diff.md"

        counts = _GENERATOR.write_dependency_diff(current, previous, output)

        assert counts == (1, 0, 0)
        content = output.read_text()
        assert "## Added" in content
        assert "| Package | Version |" in content
        assert "| numpy | 2.5.0 |" in content
        assert "## Removed" not in content
        assert "## Changed" not in content
        assert "No dependency changes detected." not in content

    def test_removed_dependency(self, tmp_path: Path) -> None:
        current = _write_sbom(tmp_path / "current.json", {})
        previous = _write_sbom(tmp_path / "previous.json", {"torch": "2.12.1"})
        output = tmp_path / "diff.md"

        counts = _GENERATOR.write_dependency_diff(current, previous, output)

        assert counts == (0, 1, 0)
        content = output.read_text()
        assert "## Removed" in content
        assert "| Package | Version |" in content
        assert "| torch | 2.12.1 |" in content
        assert "## Added" not in content
        assert "## Changed" not in content

    def test_changed_dependency(self, tmp_path: Path) -> None:
        current = _write_sbom(tmp_path / "current.json", {"torch": "2.12.1"})
        previous = _write_sbom(tmp_path / "previous.json", {"torch": "2.11.0"})
        output = tmp_path / "diff.md"

        counts = _GENERATOR.write_dependency_diff(current, previous, output)

        assert counts == (0, 0, 1)
        content = output.read_text()
        assert "## Changed" in content
        assert "| Package | Previous | Current |" in content
        assert "| torch | 2.11.0 | 2.12.1 |" in content
        assert "## Added" not in content
        assert "## Removed" not in content

    def test_no_changes_detected(self, tmp_path: Path) -> None:
        packages = {"numpy": "2.5.0", "torch": "2.12.1"}
        current = _write_sbom(tmp_path / "current.json", packages)
        previous = _write_sbom(tmp_path / "previous.json", packages)
        output = tmp_path / "diff.md"

        counts = _GENERATOR.write_dependency_diff(current, previous, output)

        assert counts == (0, 0, 0)
        content = output.read_text()
        assert content.startswith("# Dependency Diff")
        assert "No dependency changes detected." in content
        assert "## Added" not in content
        assert "## Removed" not in content
        assert "## Changed" not in content

    def test_all_branches_combined(self, tmp_path: Path) -> None:
        current = _write_sbom(
            tmp_path / "current.json",
            {"kept": "1.0.0", "added-pkg": "9.9.9", "changed-pkg": "3.1.0"},
        )
        previous = _write_sbom(
            tmp_path / "previous.json",
            {"kept": "1.0.0", "removed-pkg": "2.0.0", "changed-pkg": "3.0.0"},
        )
        output = tmp_path / "diff.md"

        counts = _GENERATOR.write_dependency_diff(current, previous, output)

        assert counts == (1, 1, 1)
        content = output.read_text()
        assert "| added-pkg | 9.9.9 |" in content
        assert "| removed-pkg | 2.0.0 |" in content
        assert "| changed-pkg | 3.0.0 | 3.1.0 |" in content
        # Unchanged packages produce no row in any section.
        assert "| kept |" not in content
        assert "No dependency changes detected." not in content
        # Sections appear in Added, Removed, Changed order.
        assert content.index("## Added") < content.index("## Removed") < content.index("## Changed")


class TestMain:
    def test_main_writes_diff_and_reports_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        current = _write_sbom(tmp_path / "current.json", {"numpy": "2.5.0"})
        previous = _write_sbom(tmp_path / "previous.json", {})
        output = tmp_path / "diff.md"
        monkeypatch.setattr(sys, "argv", ["generate-dependency-diff.py", str(current), str(previous), str(output)])

        exit_code = _GENERATOR.main()

        assert exit_code == 0
        assert "1 added, 0 removed, 0 changed" in capsys.readouterr().out
        assert "| numpy | 2.5.0 |" in output.read_text()
