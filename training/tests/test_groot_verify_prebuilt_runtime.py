from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import load_training_module

_MOD = load_training_module("groot_verify_prebuilt_runtime", "training/vla/scripts/groot/verify_prebuilt_runtime.py")

_REF = "a" * 40
_VERSIONS = {"torch": "2.7.1+cu128", "numpy": "1.26.4"}


def _version_of(name: str) -> str:
    return _VERSIONS[name]


def test_matching_metadata_passes() -> None:
    _MOD.verify_metadata({"isaac_groot_ref": _REF, "runtime_versions": dict(_VERSIONS)}, _REF, _version_of)


def test_ref_mismatch_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="does not match requested ref"):
        _MOD.verify_metadata({"isaac_groot_ref": "b" * 40, "runtime_versions": dict(_VERSIONS)}, _REF, _version_of)


def test_changed_runtime_version_is_rejected() -> None:
    metadata = {"isaac_groot_ref": _REF, "runtime_versions": {**_VERSIONS, "torch": "2.6.0"}}

    with pytest.raises(RuntimeError, match="prebuilt runtime versions changed"):
        _MOD.verify_metadata(metadata, _REF, _version_of)


def test_missing_runtime_versions_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="no runtime_versions"):
        _MOD.verify_metadata({"isaac_groot_ref": _REF}, _REF, _version_of)


def test_main_reports_unreadable_metadata(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    metadata = tmp_path / "image.json"
    metadata.write_text("[]", encoding="utf-8")

    assert _MOD.main(["--metadata", str(metadata), "--groot-ref", _REF]) == 1
    assert "is not a JSON object" in capsys.readouterr().err


def test_main_reports_missing_package(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    metadata = tmp_path / "image.json"
    metadata.write_text(
        json.dumps({"isaac_groot_ref": _REF, "runtime_versions": {"package-that-is-not-installed": "1.0"}}),
        encoding="utf-8",
    )

    assert _MOD.main(["--metadata", str(metadata), "--groot-ref", _REF]) == 1
    assert "package-that-is-not-installed" in capsys.readouterr().err
