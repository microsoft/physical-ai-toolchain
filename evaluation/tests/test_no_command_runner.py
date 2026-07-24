"""Regression tests for the deterministic no-command HiL runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hil import no_command_runner
from hil.no_command_runner import (
    NoCommandTransportError,
    NoCommandUr10eAdapter,
    _load_observations,
    _validate_output_artifacts,
    run,
)

_CONFIG_PATH = Path(__file__).parents[1] / "hil" / "config" / "ur10e-no-command.json"
_EXPECTED_OUTPUT_FILES = {
    "manifest.json",
    "observations.jsonl",
    "proposed-actions.jsonl",
    "safety-events.jsonl",
    "summary.json",
}
_EXPECTED_MANIFEST_FILES = _EXPECTED_OUTPUT_FILES - {"manifest.json"}


class TestNoCommandUr10eAdapter:
    def test_apply_action_rejects_with_no_command_transport(self) -> None:
        config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        observations = _load_observations(
            _CONFIG_PATH.parent / config["observations"]["fixture"],
            config["execution"]["max_steps"],
        )
        adapter = NoCommandUr10eAdapter(observations)

        with pytest.raises(NoCommandTransportError) as error:
            adapter.apply_action((0.0, 0.0, 0.0, 0.0, 0.0, 0.0))

        assert str(error.value) == "NO_COMMAND_TRANSPORT"


class TestNoCommandRunner:
    def test_run_records_rejected_actions_and_integrity_manifest(self, tmp_path: Path) -> None:
        result = run(_CONFIG_PATH, tmp_path)

        assert result["applied_actions"] == 0
        assert result["negative_command_probe"] == "passed"
        assert result["rejection_code"] == "NO_COMMAND_TRANSPORT"
        assert {path.name for path in tmp_path.iterdir()} == _EXPECTED_OUTPUT_FILES

        action_records = [
            json.loads(line) for line in (tmp_path / "proposed-actions.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(action_records) == result["proposed_actions"]
        assert all(record["applied"] is False for record in action_records)
        assert all(record["command_probe"] == "rejected" for record in action_records)
        assert all(record["rejection_code"] == "NO_COMMAND_TRANSPORT" for record in action_records)

        manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert {entry["path"] for entry in manifest["files"]} == _EXPECTED_MANIFEST_FILES
        _validate_output_artifacts(tmp_path)

    @pytest.mark.parametrize("artifact_name", sorted(_EXPECTED_OUTPUT_FILES))
    def test_run_rejects_credential_shaped_output_before_return(
        self, tmp_path: Path, artifact_name: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_write_json = no_command_runner._write_json
        original_write_jsonl = no_command_runner._write_jsonl

        def write_json(path: Path, value: object) -> None:
            original_write_json(path, value)
            if path.name == artifact_name:
                path.write_text(path.read_text(encoding="utf-8") + "token=credential-shaped-sentinel\n")

        def write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
            original_write_jsonl(path, values)
            if path.name == artifact_name:
                path.write_text(path.read_text(encoding="utf-8") + "token=credential-shaped-sentinel\n")

        monkeypatch.setattr(no_command_runner, "_write_json", write_json)
        monkeypatch.setattr(no_command_runner, "_write_jsonl", write_jsonl)

        with pytest.raises(ValueError, match="credential-shaped content"):
            run(_CONFIG_PATH, tmp_path)
