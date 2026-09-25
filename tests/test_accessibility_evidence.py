"""Behavior tests for the accessibility evidence gate."""

# cspell:ignore syspath

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from scripts.accessibility import evidence_gate as gate
from scripts.accessibility.evidence_gate import (
    AssetJourneyLedger,
    EvidenceBundle,
    Framework,
    RequirementEvidenceLedger,
    _docusaurus_catalogs,
    _docusaurus_exact_results,
    _load_yaml,
    canonical_model_digest,
    format_human_summary,
    main,
    prepare_docusaurus_contrast_review,
    prepare_docusaurus_reviewer_handoff,
    prepare_docusaurus_source_manifest,
    prepare_docusaurus_validation_input,
    summarize_evidence,
    validate_github_surfaces,
    validate_presentation_taxonomy,
    verify_bundle_integrity,
)

_DIGEST = "a" * 64
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_ASSET_LEDGER_PATH = _REPOSITORY_ROOT / ".github" / "accessibility" / "asset-journeys.json"
_REQUIREMENT_LEDGER_PATH = _REPOSITORY_ROOT / ".github" / "accessibility" / "requirement-evidence.json"
_ACCESSIBILITY_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "accessibility-evidence.yml"
_DOCUSAURUS_PLAYWRIGHT_CONFIG_PATH = _REPOSITORY_ROOT / "docs" / "docusaurus" / "playwright.config.ts"
_DOCUSAURUS_SCREEN_READER_CATALOG_PATH = _REPOSITORY_ROOT / "docs" / "docusaurus" / "a11y-screen-reader-cases.json"
_DOCUSAURUS_SCREEN_READER_BINDING_PATH = _REPOSITORY_ROOT / "docs" / "docusaurus" / "a11y-screen-reader.bindings.json"
_DOCUSAURUS_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "docusaurus-tests.yml"
_VIEWER_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "dataviewer-frontend-tests.yml"
_PR_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "pr-validation.yml"
_MAIN_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "main.yml"


@pytest.fixture()
def valid_bundle() -> dict[str, Any]:
    """Return the smallest complete evidence bundle."""
    return {
        "asset_ledger": {
            "schema_version": "1.0.0",
            "assets": [
                {
                    "asset_id": "ASSET-VIEWER",
                    "name": "Dataset Viewer",
                    "lifecycle": "ACTIVE",
                    "owner": "Viewer team",
                    "ownership": "PROJECT",
                    "platform": "WEB",
                    "activation": "Viewer entry point is reachable",
                    "invalidation": "Viewer composition changes",
                    "retest": "Run mapped Viewer journeys",
                }
            ],
            "fixtures": [
                {
                    "fixture_id": "FIXTURE-VIEWER",
                    "version": "1.0.0",
                    "description": "Synthetic Viewer catalog",
                    "environment_class": "LOCAL_CI",
                    "synthetic": True,
                }
            ],
            "journeys": [
                {
                    "journey_id": "V02",
                    "asset_id": "ASSET-VIEWER",
                    "lifecycle": "ACTIVE",
                    "owner": "Viewer team",
                    "role": "Annotator",
                    "platform": "WEB",
                    "state": "Dataset picker open",
                    "task": "Choose a dataset",
                    "expected_outcome": "Selection is announced and focus returns",
                    "fixture_id": "FIXTURE-VIEWER",
                    "predecessor_ids": [],
                    "successor_ids": [],
                    "handoff": None,
                    "activation": "Synthetic catalog is loaded",
                    "invalidation": "Dataset picker behavior changes",
                    "retest": "Run the dataset selection task",
                    "required_methods": ["PLAYWRIGHT_KEYBOARD"],
                }
            ],
            "exclusions": [
                {
                    "exclusion_id": "EXCLUSION-DOCUSAURUS",
                    "scope": "Docusaurus and GitHub Pages",
                    "reason": "Explicit project boundary",
                }
            ],
        },
        "requirement_ledger": {
            "schema_version": "1.0.0",
            "requirements": [
                {
                    "requirement_id": "WCAG-2.1.1-V02",
                    "framework": "WCAG_2_2",
                    "criterion": "2.1.1",
                    "proposition": "Dataset selection is keyboard operable",
                    "journey_ids": ["V02"],
                    "applicability": "APPLICABLE",
                    "applicability_rationale": "The picker is interactive",
                    "evidence_status": "NOT_ASSESSED",
                    "evidence_packs": ["E2_DETERMINISTIC_WEB"],
                    "methods": [
                        {
                            "method": "PLAYWRIGHT_KEYBOARD",
                            "disposition": "DECIDES",
                            "proposition": "Complete the exact keyboard task",
                        }
                    ],
                    "owner": "Viewer team",
                    "freshness_rule": "Invalidate when picker behavior changes",
                    "evidence_refs": [],
                    "evidence_digests": [],
                }
            ],
        },
        "run_manifest": {
            "schema_version": "1.0.0",
            "run_id": "RUN-VALID",
            "created_at": "2026-09-11T12:00:00Z",
            "bindings": {
                "source": {"repository_commit": _DIGEST, "dirty": False, "dirty_tree_digest": None},
                "build_digest": _DIGEST,
                "config_digest": _DIGEST,
                "fixture_digest": _DIGEST,
                "lockfile_digest": _DIGEST,
                "tool_digest": _DIGEST,
                "harness_package_digest": _DIGEST,
                "criterion_map_digest": _DIGEST,
            },
            "scope": {
                "asset_ids": ["ASSET-VIEWER"],
                "journey_ids": ["V02"],
                "states": ["Dataset picker open"],
                "fixture_ids": ["FIXTURE-VIEWER"],
                "roles": ["Annotator"],
                "probes": ["project-playwright"],
            },
            "environment_class": "LOCAL_CI",
            "application_name": "Dataset Viewer",
            "application_version": "0.8.0",
            "operating_system_name": "Linux",
            "operating_system_version": "test-image",
            "browser_name": "Chromium",
            "browser_version": "140",
            "assistive_technology_name": None,
            "assistive_technology_version": None,
            "provider_name": None,
            "provider_version": None,
            "locale": "en-US",
            "viewport": "1280x720",
            "input_modes": ["keyboard"],
        },
        "bundle_manifest": {
            "schema_version": "1.0.0",
            "bundle_id": "BUNDLE-VALID",
            "run_id": "RUN-VALID",
            "run_manifest_digest": _DIGEST,
            "artifacts": [
                {
                    "artifact_id": "ARTIFACT-TRACE",
                    "path": "artifacts/accessibility/RUN-VALID/trace.json",
                    "media_type": "application/json",
                    "size_bytes": 128,
                    "sha256": _DIGEST,
                }
            ],
            "quarantine_state": "CLEAR",
            "quarantine_reasons": [],
            "review_state": "AUTOMATION_ACCEPTED",
            "non_attestation_notice": True,
        },
        "evaluated_at": "2026-09-11T12:10:00Z",
        "expected_cells": [
            {
                "cell_id": "CELL-V02",
                "requirement_id": "WCAG-2.1.1-V02",
                "journey_id": "V02",
                "method": "PLAYWRIGHT_KEYBOARD",
                "disposition": "DECIDES",
                "probe": "project-playwright",
                "expected_assertion": "Keyboard selection completes and focus returns",
                "required": True,
            }
        ],
        "state_proofs": [
            {
                "proof_id": "PROOF-V02",
                "run_id": "RUN-VALID",
                "journey_id": "V02",
                "fixture_id": "FIXTURE-VIEWER",
                "role": "Annotator",
                "route": "/",
                "status": "PROVED",
                "expected": "Dataset picker is open",
                "observed": "Dataset picker is open and focused",
                "setup_steps": [
                    {"sequence": 1, "action": "NAVIGATE", "target": "/", "timeout_ms": 5000},
                    {
                        "sequence": 2,
                        "action": "ASSERT",
                        "target": "role=dialog[name='Choose dataset']",
                        "timeout_ms": 5000,
                    },
                ],
                "route_matched": True,
                "fixture_matched": True,
                "action_errors": [],
                "artifact_ids": ["ARTIFACT-TRACE"],
            }
        ],
        "results": [
            {
                "result_id": "RESULT-V02",
                "run_id": "RUN-VALID",
                "framework": "WCAG_2_2",
                "requirement_id": "WCAG-2.1.1-V02",
                "journey_id": "V02",
                "method": "PLAYWRIGHT_KEYBOARD",
                "disposition": "DECIDES",
                "status": "PASS",
                "applicability": "APPLICABLE",
                "state_proof_id": "PROOF-V02",
                "expected_result": "Keyboard selection completes",
                "observed_result": "Keyboard selection completed",
                "artifact_ids": ["ARTIFACT-TRACE"],
                "defect_id": None,
                "limitation": None,
                "reviewer_id": "automation",
                "review_state": "AUTOMATION_ACCEPTED",
                "observed_at": "2026-09-11T12:05:00Z",
                "valid_until": "2026-10-11T12:05:00Z",
                "invalidated": False,
                "invalidation_reasons": [],
                "quarantined": False,
                "quarantine_reasons": [],
                "supersedes_result_id": None,
            }
        ],
        "conflicts": [],
    }


class TestEvidenceBundleValidation:
    def test_given_conflicting_current_outcomes_when_validated_then_rejects_bundle(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        conflicting = deepcopy(valid_bundle["results"][0])
        conflicting["result_id"] = "RESULT-V02-FAIL"
        conflicting["status"] = "FAIL"
        valid_bundle["results"].append(conflicting)

        # Act and assert
        with pytest.raises(ValidationError, match="Conflicting current outcomes"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_cross_framework_result_when_validated_then_rejects_reuse(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["results"][0]["framework"] = "SECTION_504"

        # Act and assert
        with pytest.raises(ValidationError, match="reuses an outcome across frameworks"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_duplicate_ids_when_validated_then_rejects_inventory(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["asset_ledger"]["assets"].append(deepcopy(valid_bundle["asset_ledger"]["assets"][0]))

        # Act and assert
        with pytest.raises(ValidationError, match="Duplicate stable IDs"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_empty_inventory_when_validated_then_rejects_bundle(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["asset_ledger"]["journeys"] = []

        # Act and assert
        with pytest.raises(ValidationError, match="at least 1 item"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_informing_pass_when_validated_then_rejects_transition(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["requirement_ledger"]["requirements"][0]["methods"][0]["disposition"] = "INFORMS"
        valid_bundle["results"][0]["disposition"] = "INFORMS"

        # Act and assert
        with pytest.raises(ValidationError, match="Informing methods cannot produce PASS"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_missing_binding_when_validated_then_rejects_run(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        del valid_bundle["run_manifest"]["bindings"]["fixture_digest"]

        # Act and assert
        with pytest.raises(ValidationError, match="fixture_digest"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_empty_successful_run_when_validated_then_rejects_missing_results(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["results"] = []

        # Act and assert
        with pytest.raises(ValidationError, match="at least 1 item"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_expected_cell_without_result_when_validated_then_fails_closed(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["requirement_ledger"]["requirements"][0]["methods"].append(
            {
                "method": "HVE_PROBE",
                "disposition": "INFORMS",
                "proposition": "Inform possible keyboard traversal risk",
            }
        )
        valid_bundle["run_manifest"]["scope"]["probes"].append("probe-keyboard-traversal")
        valid_bundle["expected_cells"].append(
            {
                "cell_id": "CELL-V02-HVE",
                "requirement_id": "WCAG-2.1.1-V02",
                "journey_id": "V02",
                "method": "HVE_PROBE",
                "disposition": "INFORMS",
                "probe": "probe-keyboard-traversal",
                "expected_assertion": "Traversal evidence is captured",
                "required": True,
            }
        )

        # Act and assert
        with pytest.raises(ValidationError, match="requires a current result"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_probe_scope_without_expected_cell_when_validated_then_fails_closed(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["run_manifest"]["scope"]["probes"].append("qualified-human")

        # Act and assert
        with pytest.raises(ValidationError, match="probes are missing expected cells"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_unknown_probe_when_validated_then_rejects_scope(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["expected_cells"][0]["probe"] = "probe-unknown"

        # Act and assert
        with pytest.raises(ValidationError, match="probe-axe"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_hve_probe_with_project_method_when_validated_then_rejects_pairing(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["expected_cells"][0]["probe"] = "probe-keyboard-traversal"

        # Act and assert
        with pytest.raises(ValidationError, match="HVE probes require the HVE_PROBE method"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_unknown_state_action_when_validated_then_rejects_setup(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["state_proofs"][0]["setup_steps"][0]["action"] = "visit"

        # Act and assert
        with pytest.raises(ValidationError, match="Input should be 'NAVIGATE'"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_setup_without_navigation_when_validated_then_rejects_order(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["state_proofs"][0]["setup_steps"][0]["action"] = "CLICK"

        # Act and assert
        with pytest.raises(ValidationError, match="must begin with NAVIGATE"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_empty_expected_assertion_when_validated_then_rejects_cell(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["expected_cells"][0]["expected_assertion"] = ""

        # Act and assert
        with pytest.raises(ValidationError, match="at least 1 character"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_failed_state_without_quarantine_when_validated_then_rejects_result(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["state_proofs"][0]["status"] = "FAILED"
        valid_bundle["state_proofs"][0]["route_matched"] = False
        valid_bundle["state_proofs"][0]["action_errors"] = ["Expected route was not reached"]
        valid_bundle["results"][0]["status"] = "CANT_TELL"

        # Act and assert
        with pytest.raises(ValidationError, match="must be quarantined after state setup failure"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_failed_state_with_quarantined_uncertainty_when_validated_then_preserves_result(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["state_proofs"][0]["status"] = "FAILED"
        valid_bundle["state_proofs"][0]["fixture_matched"] = False
        valid_bundle["state_proofs"][0]["action_errors"] = ["Synthetic fixture did not load"]
        valid_bundle["results"][0]["status"] = "CANT_TELL"
        valid_bundle["results"][0]["quarantined"] = True
        valid_bundle["results"][0]["quarantine_reasons"] = ["State fixture was not proved"]

        # Act
        bundle = EvidenceBundle.model_validate(valid_bundle)

        # Assert
        assert bundle.results[0].status.value == "CANT_TELL"
        assert bundle.results[0].quarantined is True

    def test_given_browser_result_without_state_proof_when_validated_then_rejects_cell(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["results"][0]["status"] = "CANT_TELL"
        valid_bundle["results"][0]["state_proof_id"] = None

        # Act and assert
        with pytest.raises(ValidationError, match="requires project-owned state proof"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_unknown_journey_when_validated_then_rejects_result(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["results"][0]["journey_id"] = "V99"

        # Act and assert
        with pytest.raises(ValidationError, match="unknown journey or requirement"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_unknown_method_when_validated_then_rejects_result(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["results"][0]["method"] = "GENERIC_SCAN"

        # Act and assert
        with pytest.raises(ValidationError, match="Input should be 'SOURCE_INSPECTION'"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_unproved_state_when_validated_then_rejects_pass(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["state_proofs"][0]["status"] = "FAILED"
        valid_bundle["state_proofs"][0]["route_matched"] = False
        valid_bundle["state_proofs"][0]["action_errors"] = ["Expected route was not reached"]

        # Act and assert
        with pytest.raises(ValidationError, match="requires a proved state"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_unsafe_value_when_validated_then_rejects_bundle(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["asset_ledger"]["assets"][0]["owner"] = "access_token=example"

        # Act and assert
        with pytest.raises(ValidationError, match="Credential-shaped retained value"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_valid_minimal_bundle_when_validated_then_resolves_records(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Act
        bundle = EvidenceBundle.model_validate(valid_bundle)

        # Assert
        assert bundle.results[0].state_proof_id == bundle.state_proofs[0].proof_id

    def test_given_missing_browser_version_when_validated_then_rejects_provenance(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["run_manifest"]["browser_version"] = None

        # Act and assert
        with pytest.raises(ValidationError, match="browser name and version must be provided together"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_40_character_git_commit_when_validated_then_accepts_repository_binding(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["run_manifest"]["bindings"]["source"]["repository_commit"] = "a" * 40

        # Act
        bundle = EvidenceBundle.model_validate(valid_bundle)

        # Assert
        assert len(bundle.run_manifest.bindings.source.repository_commit) == 40

    def test_given_missing_harness_digest_when_validated_then_rejects_provenance(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        del valid_bundle["run_manifest"]["bindings"]["harness_package_digest"]

        # Act and assert
        with pytest.raises(ValidationError, match="harness_package_digest"):
            EvidenceBundle.model_validate(valid_bundle)

    def test_given_supersession_without_newer_observation_when_validated_then_rejects_history(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        previous = deepcopy(valid_bundle["results"][0])
        previous["result_id"] = "RESULT-V02-OLD"
        previous["observed_at"] = "2026-09-11T12:06:00Z"
        valid_bundle["results"][0]["supersedes_result_id"] = "RESULT-V02-OLD"
        valid_bundle["results"].append(previous)

        # Act and assert
        with pytest.raises(ValidationError, match="must be newer than the superseded result"):
            EvidenceBundle.model_validate(valid_bundle)


class TestFailSafeAggregation:
    def test_given_current_deciding_failure_when_summarized_then_failure_controls(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        valid_bundle["results"][0]["status"] = "FAIL"
        bundle = EvidenceBundle.model_validate(valid_bundle)

        # Act
        summary = summarize_evidence(bundle)

        # Assert
        assert summary.verdict.value == "FAIL"

    def test_given_expired_pass_when_summarized_then_pass_is_prevented(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["results"][0]["valid_until"] = "2026-09-11T12:06:00Z"
        bundle = EvidenceBundle.model_validate(valid_bundle)

        # Act
        summary = summarize_evidence(bundle)

        # Assert
        assert summary.verdict.value == "CANT_TELL"
        assert "stale or invalidated" in " ".join(summary.reasons)

    def test_given_invalidated_pass_when_summarized_then_pass_is_prevented(self, valid_bundle: dict[str, Any]) -> None:
        # Arrange
        valid_bundle["results"][0]["invalidated"] = True
        valid_bundle["results"][0]["invalidation_reasons"] = ["Fixture meaning changed"]
        bundle = EvidenceBundle.model_validate(valid_bundle)

        # Act
        summary = summarize_evidence(bundle)

        # Assert
        assert summary.verdict.value == "CANT_TELL"

    def test_given_explicit_current_conflict_when_summarized_then_both_results_are_preserved(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        conflicting = deepcopy(valid_bundle["results"][0])
        conflicting["result_id"] = "RESULT-V02-FAIL"
        conflicting["status"] = "FAIL"
        valid_bundle["results"].append(conflicting)
        valid_bundle["conflicts"].append(
            {
                "conflict_id": "CONFLICT-V02",
                "result_ids": ["RESULT-V02", "RESULT-V02-FAIL"],
                "state": "UNRESOLVED",
                "resolution": None,
                "reviewer_id": None,
            }
        )
        bundle = EvidenceBundle.model_validate(valid_bundle)

        # Act
        summary = summarize_evidence(bundle)

        # Assert
        assert len(bundle.results) == 2
        assert summary.verdict.value == "FAIL"

    def test_given_newer_linked_retest_when_summarized_then_old_failure_is_superseded(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        previous = deepcopy(valid_bundle["results"][0])
        previous["result_id"] = "RESULT-V02-OLD"
        previous["status"] = "FAIL"
        previous["observed_at"] = "2026-09-11T12:00:00Z"
        valid_bundle["results"][0]["supersedes_result_id"] = "RESULT-V02-OLD"
        valid_bundle["results"].append(previous)
        bundle = EvidenceBundle.model_validate(valid_bundle)

        # Act
        summary = summarize_evidence(bundle)

        # Assert
        assert summary.verdict.value == "PASS"
        assert len(bundle.results) == 2

    def test_given_valid_pass_when_rendered_then_summary_contains_no_score_or_attestation(
        self, valid_bundle: dict[str, Any]
    ) -> None:
        # Arrange
        bundle = EvidenceBundle.model_validate(valid_bundle)

        # Act
        summary = summarize_evidence(bundle)
        human_summary = format_human_summary(summary)

        # Assert
        assert summary.verdict.value == "PASS"
        assert "score" not in summary.model_dump()
        assert "Attestation: false" in human_summary


class TestBundleIntegrity:
    def test_given_matching_artifact_bytes_when_verified_then_integrity_passes(
        self, valid_bundle: dict[str, Any], tmp_path: Path
    ) -> None:
        # Arrange
        content = b'{"state":"proved"}'
        (tmp_path / "trace.json").write_bytes(content)
        bundle = EvidenceBundle.model_validate(valid_bundle)
        bundle.bundle_manifest.artifacts[0].path = "trace.json"
        bundle.bundle_manifest.artifacts[0].size_bytes = len(content)
        bundle.bundle_manifest.artifacts[0].sha256 = hashlib.sha256(content).hexdigest()
        bundle.bundle_manifest.run_manifest_digest = canonical_model_digest(bundle.run_manifest)

        # Act and assert
        verify_bundle_integrity(bundle, tmp_path)

    def test_given_mismatched_artifact_digest_when_verified_then_integrity_fails(
        self, valid_bundle: dict[str, Any], tmp_path: Path
    ) -> None:
        # Arrange
        content = b'{"state":"proved"}'
        (tmp_path / "trace.json").write_bytes(content)
        bundle = EvidenceBundle.model_validate(valid_bundle)
        bundle.bundle_manifest.artifacts[0].path = "trace.json"
        bundle.bundle_manifest.artifacts[0].size_bytes = len(content)
        bundle.bundle_manifest.artifacts[0].sha256 = "b" * 64
        bundle.bundle_manifest.run_manifest_digest = canonical_model_digest(bundle.run_manifest)

        # Act and assert
        with pytest.raises(ValueError, match="artifact ARTIFACT-TRACE digest mismatch"):
            verify_bundle_integrity(bundle, tmp_path)

    def test_given_valid_bundle_file_when_gate_runs_then_machine_summary_is_emitted(
        self, valid_bundle: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Arrange
        content = b'{"state":"proved"}'
        (tmp_path / "trace.json").write_bytes(content)
        bundle = EvidenceBundle.model_validate(valid_bundle)
        bundle.bundle_manifest.artifacts[0].path = "trace.json"
        bundle.bundle_manifest.artifacts[0].size_bytes = len(content)
        bundle.bundle_manifest.artifacts[0].sha256 = hashlib.sha256(content).hexdigest()
        bundle.bundle_manifest.run_manifest_digest = canonical_model_digest(bundle.run_manifest)
        bundle_path = tmp_path / "bundle.json"
        bundle_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

        # Act
        exit_code = main([str(bundle_path), "--repository-root", str(tmp_path)])
        output = capsys.readouterr().out

        # Assert
        assert exit_code == 0
        assert '"verdict": "PASS"' in output
        assert '"attestation": false' in output


class TestAccessibilityWorkflowSource:
    @pytest.mark.parametrize(
        ("path", "job_id", "step_name"),
        [
            (_ACCESSIBILITY_WORKFLOW_PATH, "evidence", "Validate project evidence contracts"),
            (_DOCUSAURUS_WORKFLOW_PATH, "docusaurus", "Validate project accessibility evidence contracts"),
        ],
    )
    def test_real_composer_tests_use_the_acquired_pinned_runtime(
        self,
        path: Path,
        job_id: str,
        step_name: str,
    ) -> None:
        steps = _load_yaml(path)["jobs"][job_id]["steps"]
        contract_index = next(index for index, step in enumerate(steps) if step["name"] == step_name)
        acquire_index = next(
            index for index, step in enumerate(steps) if step["name"].startswith("Acquire reviewed HVE")
        )
        contract = steps[contract_index]
        assert acquire_index < contract_index
        assert contract["env"]["HVE_SKILL_ROOT"].startswith("${{ runner.temp }}/hve-core/")
        assert 'uv run --project "$HVE_SKILL_ROOT" --frozen' in contract["run"]
        assert "tests/test_accessibility_evidence.py tests/test_accessibility_promotion.py" in contract["run"]

    def test_given_accessibility_workflow_when_reviewed_then_hve_provenance_is_immutable(self) -> None:
        # Act
        workflows = [
            _ACCESSIBILITY_WORKFLOW_PATH.read_text(encoding="utf-8"),
            _DOCUSAURUS_WORKFLOW_PATH.read_text(encoding="utf-8"),
        ]

        # Assert
        for workflow in workflows:
            assert "56c30bcbbba1a8235970c44f5e79a9d83e296f54" in workflow
            assert "c6875ad36ceeca2acec665195c385792f4b1f32f" in workflow
            assert "ef1de7239be999bb271184337e9edc15eb48fb66a69ca5dcb9628182350893a4" in workflow
            assert "dd7689d6b058e42facba4539f8414d6090d95ea82853c665e05026857a09026f" in workflow
            assert "f0265e2d836fb9765be0a6f15e4c21fefc72a46651e97490dea27535172b6377" in workflow

    def test_given_accessibility_workflow_when_reviewed_then_runtime_and_retention_contracts_are_present(
        self,
    ) -> None:
        # Act
        workflow = _load_yaml(_ACCESSIBILITY_WORKFLOW_PATH)
        events = workflow.get("on", workflow.get(True))
        evidence_job = workflow["jobs"]["evidence"]
        steps = {step["name"]: step for step in evidence_job["steps"]}

        # Assert
        assert set(events) == {"workflow_call", "workflow_dispatch", "schedule"}
        assert workflow["permissions"] == {"contents": "read"}
        assert "google-chrome --version" in steps["Verify system Chrome"]["run"]
        assert steps["Upload accessibility evidence"]["if"] == "always()"
        assert steps["Upload accessibility evidence"]["with"]["retention-days"] == 30
        product_condition = steps["Run deterministic product evidence"]["if"]
        assert "github.event_name == 'schedule'" in product_condition
        assert "github.event_name == 'workflow_dispatch'" in product_condition
        assert steps["Validate generated evidence bundle"]["if"] == "${{ inputs.validate-generated-bundle == true }}"
        contract_run = steps["Validate project evidence contracts"]["run"]
        assert "scripts/accessibility/evidence_gate.py --config-preview" in contract_run
        self_test_run = steps["Run HVE accessibility self-tests"]["run"]
        assert 'cd "$skill_root"' in self_test_run
        assert "uv run --frozen pytest -q" in self_test_run

    def test_given_docusaurus_workflow_when_reviewed_then_composition_is_fail_closed(self) -> None:
        # Act
        workflow = _load_yaml(_DOCUSAURUS_WORKFLOW_PATH)
        events = workflow.get("on", workflow.get(True))
        inputs = events["workflow_call"]["inputs"]
        steps = {step["name"]: step for step in workflow["jobs"]["docusaurus"]["steps"]}

        # Assert
        assert inputs["evidence-cadence"]["type"] == "string"
        assert inputs["evidence-cadence"]["default"] == "pull-request"
        assert inputs["required-completeness"]["type"] == "string"
        assert inputs["required-completeness"]["default"] == "automated"
        assert workflow["env"]["REQUIRED_COMPLETENESS"] == "${{ inputs.required-completeness }}"
        assert workflow["permissions"] == {"contents": "read"}
        prepare_run = steps["Prepare Docusaurus evidence composition"]["run"]
        compose_run = steps["Compose Docusaurus accessibility evidence"]["run"]
        validate_run = steps["Validate Docusaurus accessibility evidence"]["run"]
        handoff_run = steps["Prepare qualified-review handoff"]["run"]
        manifest_run = steps["Emit Docusaurus accessibility validation manifest"]["run"]
        assert "--prepare-docusaurus-composition" in prepare_run
        assert "compose-evidence \\" in compose_run
        assert compose_run.count('--source "$GITHUB_WORKSPACE/artifacts/accessibility/docusaurus/inputs/') == 2
        assert '--artifact-root "$GITHUB_WORKSPACE"' in compose_run
        assert '--require-completeness "$REQUIRED_COMPLETENESS"' in compose_run
        assert "--validate-composed-docusaurus-bundle" in validate_run
        assert '--require-completeness "$REQUIRED_COMPLETENESS"' in validate_run
        assert '--harness-root "$skill_root"' in validate_run
        assert "--prepare-docusaurus-validation-input" in manifest_run
        assert "emit-validation-manifest" in manifest_run
        assert "accessibility-validation-manifest.json" in manifest_run
        assert "schema-validation-report.json" in manifest_run
        assert "--prepare-docusaurus-contrast-review" in steps["Prepare Docusaurus contrast reviewer manifest"]["run"]
        assert "--prepare-docusaurus-reviewer-handoff" in handoff_run
        assert "contrast-review=${{ steps.contrast-review.outcome }}" in manifest_run
        retention_days = steps["Upload Docusaurus accessibility evidence"]["with"]["retention-days"]
        assert retention_days == "${{ inputs.evidence-cadence == 'release' && 90 || 30 }}"
        stage = steps["Stage portable Docusaurus evidence"]
        assert stage["if"] == "always()"
        assert '--stage-docusaurus-package "$RUNNER_TEMP/docusaurus-retained"' in stage["run"]
        assert '--verify-docusaurus-package "$RUNNER_TEMP/docusaurus-retained"' in stage["run"]
        assert "--repository-root ." in stage["run"]
        upload = steps["Upload Docusaurus accessibility evidence"]
        assert upload["if"] == "always()"
        assert upload["with"]["path"] == "${{ runner.temp }}/docusaurus-retained/"
        assert not any("browser diagnostics" in name.lower() for name in steps)
        binding_validation = (
            'PYTHONPATH="$skill_root/scripts" uv run --project "$skill_root" --frozen python - <<\'PY\''
        )
        assert binding_validation in steps["Validate Docusaurus screen-reader binding"]["run"]

    def test_given_scheduled_evidence_when_reviewed_then_linux_webkit_is_explicitly_bounded(self) -> None:
        # Act
        accessibility_workflow = _load_yaml(_ACCESSIBILITY_WORKFLOW_PATH)
        docusaurus_workflow = _load_yaml(_DOCUSAURUS_WORKFLOW_PATH)
        playwright_config = _DOCUSAURUS_PLAYWRIGHT_CONFIG_PATH.read_text(encoding="utf-8")
        scheduled_job = accessibility_workflow["jobs"]["scheduled-docusaurus"]
        steps = {step["name"]: step for step in docusaurus_workflow["jobs"]["docusaurus"]["steps"]}

        # Assert
        assert scheduled_job["uses"] == "./.github/workflows/docusaurus-tests.yml"
        assert scheduled_job["if"] == (
            "${{ github.event_name == 'schedule' || github.event_name == 'workflow_dispatch' }}"
        )
        assert scheduled_job["with"]["evidence-cadence"] == (
            "${{ github.event_name == 'schedule' && 'scheduled' || inputs.evidence-cadence }}"
        )
        assert "npx playwright install --with-deps webkit" in steps["Provision scheduled WebKit engine"]["run"]
        supplemental = steps["Run supplemental scheduled WebKit gates"]
        assert supplemental["if"] == "${{ inputs.evidence-cadence == 'scheduled' }}"
        assert supplemental["env"]["DOCS_E2E_OUTPUT_DIR"] == "test-results/webkit"
        assert "npx playwright test --project=linux-webkit" in supplemental["run"]
        assert "name: 'linux-webkit'" in playwright_config
        assert "testIgnore: '**/site-crawl.spec.ts'" in playwright_config


class TestDocusaurusCompositionAdapter:
    @pytest.fixture()
    def exact_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "requirementId": "WCAG22-2.1.1:DCS02:PLAYWRIGHT_KEYBOARD",
                "journeyIds": ["DCS02"],
                "methods": [
                    {
                        "method": "PLAYWRIGHT_KEYBOARD",
                        "probe": "project-playwright",
                        "human": False,
                    }
                ],
            }
        ]

    def test_given_exact_cell_when_title_changes_then_identity_still_passes(
        self, exact_catalog: list[dict[str, Any]]
    ) -> None:
        manifest = {
            "schemaVersion": "1.0.0",
            "runId": "run-1",
            "cells": [
                {
                    "requirementId": "WCAG22-2.1.1:DCS02:PLAYWRIGHT_KEYBOARD",
                    "journeyId": "DCS02",
                    "method": "PLAYWRIGHT_KEYBOARD",
                    "probe": "project-playwright",
                    "testId": "stable-navigation-test",
                    "project": "system-chrome",
                    "status": "PASS",
                    "artifactIds": ["docusaurus-playwright"],
                }
            ],
        }

        results = _docusaurus_exact_results(manifest, exact_catalog)

        assert results["WCAG22-2.1.1:DCS02:PLAYWRIGHT_KEYBOARD"]["status"] == "PASS"

    def test_given_missing_or_wrong_project_cell_when_mapped_then_fails_closed(
        self, exact_catalog: list[dict[str, Any]]
    ) -> None:
        manifest = {
            "schemaVersion": "1.0.0",
            "runId": "run-1",
            "cells": [
                {
                    "requirementId": "WCAG22-2.1.1:DCS02:PLAYWRIGHT_KEYBOARD",
                    "journeyId": "DCS02",
                    "method": "PLAYWRIGHT_KEYBOARD",
                    "probe": "project-playwright",
                    "testId": "stable-navigation-test",
                    "project": "linux-webkit",
                    "status": "PASS",
                    "artifactIds": ["docusaurus-playwright"],
                }
            ],
        }

        results = _docusaurus_exact_results(manifest, exact_catalog)

        assert results["WCAG22-2.1.1:DCS02:PLAYWRIGHT_KEYBOARD"]["status"] == "CANT_TELL"

    def test_given_duplicate_exact_cells_when_mapped_then_rejects_manifest(
        self, exact_catalog: list[dict[str, Any]]
    ) -> None:
        cell = {
            "requirementId": "WCAG22-2.1.1:DCS02:PLAYWRIGHT_KEYBOARD",
            "journeyId": "DCS02",
            "method": "PLAYWRIGHT_KEYBOARD",
            "probe": "project-playwright",
            "testId": "stable-navigation-test",
            "project": "system-chrome",
            "status": "PASS",
            "artifactIds": ["docusaurus-playwright"],
        }

        with pytest.raises(ValueError, match="duplicate exact Playwright cell"):
            _docusaurus_exact_results(
                {"schemaVersion": "1.0.0", "runId": "run-1", "cells": [cell, deepcopy(cell)]},
                exact_catalog,
            )

    def test_given_cadence_when_catalogs_are_built_then_manual_cells_are_release_only(self) -> None:
        # Arrange
        asset_ledger = AssetJourneyLedger.model_validate_json(_ASSET_LEDGER_PATH.read_text(encoding="utf-8"))
        requirement_ledger = RequirementEvidenceLedger.model_validate_json(
            _REQUIREMENT_LEDGER_PATH.read_text(encoding="utf-8")
        )

        # Act
        pull_request_assets, pull_request_requirements, pull_request_scope = _docusaurus_catalogs(
            asset_ledger, requirement_ledger, "pull-request"
        )
        _, release_requirements, release_scope = _docusaurus_catalogs(asset_ledger, requirement_ledger, "release")

        # Assert
        assert pull_request_scope["selectors"]["journeyIds"] == [f"DCS{number:02d}" for number in range(1, 13)]
        assert "DCS13" in {item["journeyId"] for item in pull_request_assets["journeys"]}
        assert any(item["methods"][0]["human"] for item in pull_request_requirements["requirements"])
        assert "qualified-human" not in pull_request_scope["selectors"]["probes"]
        assert len({item["sourceRequirementId"] for item in pull_request_requirements["requirements"]}) == 55
        assert "DCS13" in release_scope["selectors"]["journeyIds"]
        assert release_scope["manualEvidencePolicy"] == "required"
        assert any(item["methods"][0]["human"] for item in release_requirements["requirements"])
        assert len(release_requirements["requirements"]) == 190
        assert sum(item["methods"][0]["human"] for item in release_requirements["requirements"]) == 64
        assert sum(item["journeyIds"] == ["DCS13"] for item in release_requirements["requirements"]) == 44
        assert {
            item["requirementId"]
            for item in release_requirements["requirements"]
            if item["methods"][0]["method"] == "JAWS"
        } == {
            "WCAG22-1.3.2:DCS13:JAWS",
            "WCAG22-3.1.2:DCS13:JAWS",
            "WCAG22-4.1.2:DCS13:JAWS",
            "WCAG22-4.1.3:DCS13:JAWS",
        }

    def test_guarded_criteria_retain_exact_qualified_deciding_propositions(self) -> None:
        assets = AssetJourneyLedger.model_validate_json(_ASSET_LEDGER_PATH.read_text(encoding="utf-8"))
        ledger = RequirementEvidenceLedger.model_validate_json(_REQUIREMENT_LEDGER_PATH.read_text(encoding="utf-8"))
        _, catalog, _ = _docusaurus_catalogs(assets, ledger, "release")
        deciding = {
            item["sourceRequirementId"]
            for item in catalog["requirements"]
            if item["methods"][0]["disposition"] == "decides"
        }
        assert len(deciding) == 55
        for criterion in ("2.1.4", "2.2.1", "2.5.1", "2.5.4", "3.3.1"):
            requirement = next(item for item in ledger.requirements if item.criterion == criterion)
            expected = {item.proposition for item in requirement.methods if item.disposition == "DECIDES"}
            actual = {
                item["methods"][0]["proposition"]
                for item in catalog["requirements"]
                if item["sourceRequirementId"] == requirement.requirement_id
                and item["journeyIds"] == ["DCS13"]
                and item["methods"][0]["disposition"] == "decides"
                and item["methods"][0]["probe"] == "qualified-human"
            }
            assert actual == expected


@pytest.fixture()
def hve_runtime(monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Any]:
    configured = os.environ.get("HVE_SKILL_ROOT")
    if not configured:
        pytest.skip("Set HVE_SKILL_ROOT to the reviewed pinned accessibility skill")
    root = Path(configured).resolve()
    monkeypatch.syspath_prepend(str(root / "scripts"))
    return root, importlib.import_module("runtime_a11y.evidence_bundle._compose")


def test_hve_integrity_pins_match_immutable_git_bytes(hve_runtime: tuple[Path, Any]) -> None:
    from scripts.accessibility import promotion

    skill_root, _ = hve_runtime
    checkout = skill_root.parents[3]
    for relative, expected in promotion._HVE_HASHES.items():
        content = subprocess.run(
            ["git", "-C", str(checkout), "show", f"{promotion._HVE_REF}:{promotion._HVE_PATH}/{relative}"],
            check=True,
            capture_output=True,
        ).stdout
        actual = hashlib.sha256(content).hexdigest()
        assert expected == actual, f"Integrity pin must hash Git bytes, not checkout line endings: {relative}"
        for workflow_path in (_ACCESSIBILITY_WORKFLOW_PATH, _DOCUSAURUS_WORKFLOW_PATH):
            assert actual in workflow_path.read_text(encoding="utf-8")


@pytest.fixture()
def composed_docs_bundle(tmp_path: Path, hve_runtime: tuple[Path, Any]) -> tuple[Path, Path, dict[str, Any]]:
    root, composer = hve_runtime
    for source in (_ASSET_LEDGER_PATH, _REQUIREMENT_LEDGER_PATH):
        destination = tmp_path / ".github" / "accessibility" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    assets = AssetJourneyLedger.model_validate_json(_ASSET_LEDGER_PATH.read_text(encoding="utf-8"))
    ledger = RequirementEvidenceLedger.model_validate_json(_REQUIREMENT_LEDGER_PATH.read_text(encoding="utf-8"))
    asset_catalog, requirements, scope = _docusaurus_catalogs(assets, ledger, "release")
    run = {
        "schemaVersion": "1.0.0",
        "runId": "test-run",
        "campaignId": "docusaurus-accessibility",
        "composedAt": "2026-09-24T12:00:00Z",
        "sourceRevision": "a" * 40,
        **{
            key: _DIGEST
            for key in (
                "buildDigest",
                "configDigest",
                "fixtureDigest",
                "lockfileDigest",
                "toolDigest",
                "harnessDigest",
                "mappingDigest",
            )
        },
        "environment": {"class": "local-ci", "operatingSystem": "test", "locale": "en-US", "inputModes": ["keyboard"]},
    }
    cells, _ = composer._obligations(asset_catalog, requirements, scope)
    source = {
        "schemaVersion": "1.0.0",
        "sourceKind": "source",
        "producer": "contract test",
        "sourceRunId": "source-test",
        "observedAt": run["composedAt"],
        "boundTo": {key: run[key] for key in ("sourceRevision", "buildDigest", "configDigest", "fixtureDigest")},
        "results": [
            {
                **{
                    key: cell[key]
                    for key in ("requirementId", "journeyId", "state", "method", "probe", "disposition", "expected")
                },
                "resultId": f"result-{index}",
                "status": "PASS",
                "observed": "Synthetic test observation",
                "artifactIds": [],
            }
            for index, cell in enumerate(cells)
            if not cell["human"]
        ],
        "artifacts": [],
    }
    source["sourceDigest"] = gate._hve_canonical_digest(source, "hve-a11y:evidence-source:v1")
    bundle = composer.compose_evidence(
        asset_catalog=asset_catalog,
        requirement_catalog=requirements,
        scope=scope,
        run_context=run,
        sources=[source],
        state_proofs=[],
    )
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return root, path, bundle


class TestComposedDocusaurusVerdict:
    @staticmethod
    def _validate(fixture: tuple[Path, Path, dict[str, Any]], *, completeness: str = "automated") -> dict[str, Any]:
        harness, path, bundle = fixture
        bundle["bundleDigest"] = gate._hve_canonical_digest(
            {key: value for key, value in bundle.items() if key != "bundleDigest"},
            "hve-a11y:evidence-bundle:v1",
        )
        path.write_text(json.dumps(bundle), encoding="utf-8")
        return gate.validate_composed_docusaurus_bundle(
            path, path.parent, harness_root=harness, required_completeness=completeness
        )

    def test_automated_completeness_allows_pending_review_but_never_release(
        self, composed_docs_bundle: tuple[Path, Path, dict[str, Any]]
    ) -> None:
        assert self._validate(composed_docs_bundle)["verdict"] == "PASS"
        assert self._validate(composed_docs_bundle, completeness="reviewer")["verdict"] == "CANT_TELL"
        assert self._validate(composed_docs_bundle, completeness="release")["verdict"] == "CANT_TELL"

    def test_declared_upstream_release_incomplete_is_never_overridden(
        self, composed_docs_bundle: tuple[Path, Path, dict[str, Any]]
    ) -> None:
        bundle = composed_docs_bundle[2]
        for result in bundle["evidenceResults"]:
            if result["status"] == "NOT_ASSESSED":
                result["status"] = "PASS"
        bundle["scopeCompleteness"]["reviewerEvidence"] = "complete"
        assert any(result["status"] == "CANT_TELL" for result in bundle["evidenceResults"])
        assert bundle["scopeCompleteness"]["releaseEvidence"] == "incomplete"
        assert self._validate(composed_docs_bundle, completeness="release")["verdict"] == "CANT_TELL"

    def test_altered_bundle_digest_is_rejected(self, composed_docs_bundle: tuple[Path, Path, dict[str, Any]]) -> None:
        harness, path, bundle = composed_docs_bundle
        bundle["bundleDigest"] = "0" * 64
        gate._write_json(path, bundle)
        with pytest.raises(ValueError, match="bundle digest mismatch"):
            gate.validate_composed_docusaurus_bundle(path, path.parent, harness_root=harness)

    @pytest.mark.parametrize("reverse", [False, True])
    def test_current_fail_dominates_regardless_of_result_order(
        self, composed_docs_bundle: tuple[Path, Path, dict[str, Any]], reverse: bool
    ) -> None:
        bundle = composed_docs_bundle[2]
        result = next(item for item in bundle["evidenceResults"] if item["status"] == "PASS")
        failed = {**result, "resultId": "failing-result", "status": "FAIL"}
        bundle["evidenceResults"].append(failed)
        if reverse:
            bundle["evidenceResults"].reverse()
        assert self._validate(composed_docs_bundle)["verdict"] == "FAIL"

    def test_valid_supersession_ignores_historical_failure(
        self, composed_docs_bundle: tuple[Path, Path, dict[str, Any]]
    ) -> None:
        bundle = composed_docs_bundle[2]
        result = next(item for item in bundle["evidenceResults"] if item["status"] == "PASS")
        previous = {**result, "resultId": "historical-result", "status": "FAIL", "current": False}
        previous["observedAt"] = "2026-09-24T11:00:00Z"
        result["supersedesResultId"] = previous["resultId"]
        bundle["evidenceResults"].append(previous)
        assert self._validate(composed_docs_bundle)["verdict"] == "PASS"

    @pytest.mark.parametrize("mutation", ["unknown", "cross-cell", "current-flag", "duplicate", "cycle"])
    def test_invalid_supersession_is_rejected(
        self, composed_docs_bundle: tuple[Path, Path, dict[str, Any]], mutation: str
    ) -> None:
        bundle = composed_docs_bundle[2]
        results = [item for item in bundle["evidenceResults"] if item["status"] == "PASS"]
        first, second = results[:2]
        if mutation == "unknown":
            first["supersedesResultId"] = "unknown"
        elif mutation == "cross-cell":
            first["supersedesResultId"] = second["resultId"]
        elif mutation == "current-flag":
            first["current"] = False
        elif mutation == "duplicate":
            bundle["evidenceResults"].append(deepcopy(first))
        else:
            first["supersedesResultId"] = first["resultId"]
        with pytest.raises(ValueError):
            self._validate(composed_docs_bundle)

    @pytest.mark.parametrize("mutation", ["missing", "quarantine", "conflict", "incomplete", "expired"])
    def test_unresolved_evidence_cannot_pass(
        self, composed_docs_bundle: tuple[Path, Path, dict[str, Any]], mutation: str
    ) -> None:
        bundle = composed_docs_bundle[2]
        result = next(item for item in bundle["evidenceResults"] if item["status"] == "PASS")
        if mutation == "missing":
            bundle["evidenceResults"].remove(result)
        elif mutation == "quarantine":
            result["quarantined"] = True
        elif mutation == "conflict":
            bundle["conflicts"].append(
                {
                    "conflictId": "conflict-test",
                    "cellId": result["cellId"],
                    "resultIds": [result["resultId"], "other-result"],
                    "state": "unresolved",
                }
            )
        elif mutation == "incomplete":
            bundle["scopeCompleteness"]["automatedCollection"] = "incomplete"
        else:
            result["validUntil"] = "2026-09-24T11:00:00Z"
        assert self._validate(composed_docs_bundle)["verdict"] != "PASS"

    @pytest.mark.parametrize("mutation", ["schema", "empty", "remove", "duplicate", "catalog", "digest"])
    def test_schema_and_canonical_obligations_fail_closed(
        self, composed_docs_bundle: tuple[Path, Path, dict[str, Any]], mutation: str
    ) -> None:
        bundle = composed_docs_bundle[2]
        if mutation == "schema":
            bundle["unexpected"] = True
        elif mutation == "empty":
            bundle["expectedCells"] = []
        elif mutation == "remove":
            bundle["expectedCells"].pop()
        elif mutation == "duplicate":
            bundle["expectedCells"].append(bundle["expectedCells"][0])
        elif mutation == "catalog":
            bundle["catalogs"]["requirementMethod"]["requirements"][0]["methods"][0]["proposition"] = "drift"
        else:
            bundle["catalogs"]["assetJourneyDigest"] = "0" * 64
        with pytest.raises(ValueError):
            self._validate(composed_docs_bundle)


class TestDocusaurusRetainedPackage:
    def test_exact_build_marker_is_retained_in_original_build_digest(self, tmp_path: Path) -> None:
        source = tmp_path / "repository"
        build = source / "docs" / "docusaurus" / "build"
        build.mkdir(parents=True)
        marker = build / ".nojekyll"
        marker.write_bytes(b"")
        page = build / "index.html"
        page.write_text("<main>Retained build</main>", encoding="utf-8")
        package = tmp_path / "retained"

        manifest = gate.stage_docusaurus_package(source, package)

        retained_marker = package / "docs" / "docusaurus" / "build" / ".nojekyll"
        assert retained_marker.is_file()
        assert retained_marker.read_bytes() == marker.read_bytes()
        assert manifest["buildDigest"] == gate._digest_paths([marker, page], source)
        assert manifest["buildDigest"] != gate._digest_paths([page], source)
        assert gate.verify_docusaurus_package(package)["buildDigest"] == manifest["buildDigest"]

    @pytest.mark.parametrize("relative", [".hidden", ".gitignore", "nested/.nojekyll", ".cache/asset.json"])
    def test_build_marker_exception_does_not_allow_other_hidden_paths(self, tmp_path: Path, relative: str) -> None:
        source = tmp_path / "repository"
        hidden = source / "docs" / "docusaurus" / "build" / relative
        hidden.parent.mkdir(parents=True)
        hidden.write_bytes(b"not an audited marker")
        with pytest.raises(ValueError, match="Hidden"):
            gate.stage_docusaurus_package(source, tmp_path / "retained")

    def test_build_marker_exception_does_not_allow_symlinks(self, tmp_path: Path) -> None:
        source = tmp_path / "repository"
        marker = source / "docs" / "docusaurus" / "build" / ".nojekyll"
        marker.parent.mkdir(parents=True)
        target = tmp_path / "outside-marker"
        target.write_bytes(b"")
        try:
            marker.symlink_to(target)
        except OSError:
            pytest.skip("Symbolic links require privileges on this host")
        with pytest.raises(ValueError, match="Symlink"):
            gate.stage_docusaurus_package(source, tmp_path / "retained")

    @pytest.fixture()
    def package_source(
        self, composed_docs_bundle: tuple[Path, Path, dict[str, Any]], hve_runtime: tuple[Path, Any]
    ) -> Path:
        _, bundle_path, original = composed_docs_bundle
        root = bundle_path.parent
        evidence_root = root / "artifacts" / "accessibility" / "docusaurus"
        results_root = root / "docs" / "docusaurus" / "test-results"
        build_root = root / "docs" / "docusaurus" / "build"
        results_root.mkdir(parents=True)
        build_root.mkdir(parents=True)
        for name in gate._DOCUSAURUS_PACKAGE_OUTPUTS:
            gate._write_json(evidence_root / name, {})
        for name in gate._DOCUSAURUS_PACKAGE_RESULTS:
            gate._write_json(results_root / name, {})
        gate._write_json(build_root / "deployed-routes.json", {"routes": ["/"]})
        gate._write_json(build_root / "mermaid-routes.json", {"routes": []})
        (build_root / "index.html").write_text("<main>Test build</main>", encoding="utf-8")
        (build_root / ".nojekyll").write_bytes(b"")
        report = root / "docs" / "docusaurus" / "playwright-report" / "index.html"
        report.parent.mkdir()
        report.write_text("<main>Test report</main>", encoding="utf-8")
        crop = results_root / "contrast-crops" / "test.png"
        crop.parent.mkdir()
        crop.write_bytes(b"synthetic-crop")
        gate._write_json(
            results_root / "site-crawl-results.json",
            {
                "results": [{"contrastEvidence": [{"evidencePath": "contrast-crops/test.png"}]}],
            },
        )
        gate._write_json(
            evidence_root / "contrast-review.json",
            {
                "signatures": [
                    {
                        "evidence": [
                            {
                                "evidencePath": "contrast-crops/test.png",
                                "evidenceDigest": hashlib.sha256(crop.read_bytes()).hexdigest(),
                            }
                        ]
                    }
                ],
            },
        )
        gate._write_json(root / "docs" / "docusaurus" / "e2e" / "contrast-baseline.json", {"entries": []})
        gate._write_json(root / "docs" / "docusaurus" / "a11y-screen-reader.bindings.json", {"caseBindings": {}})
        source_files = [
            {
                "path": path.relative_to(root).as_posix(),
                "mode": "100644",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted((root / ".github" / "accessibility").glob("*.json"))
        ]
        gate._write_json(
            evidence_root / "source-input-manifest.json",
            {
                "files": source_files,
                "sourceInputDigest": hashlib.sha256(
                    json.dumps(source_files, separators=(",", ":"), sort_keys=True).encode()
                ).hexdigest(),
                "sourceRevision": original["runManifest"]["sourceRevision"],
            },
        )
        assets, requirements, scope, cells = gate._docusaurus_contract(root, "release")
        run = {**original["runManifest"], "buildDigest": gate._digest_paths(list(build_root.iterdir()), root)}
        artifact = gate._artifact_record(results_root / "playwright-results.json", root, "test-report")
        source = {
            "schemaVersion": "1.0.0",
            "sourceKind": "source",
            "producer": "contract test",
            "sourceRunId": "test-source",
            "observedAt": run["composedAt"],
            "boundTo": {key: run[key] for key in ("sourceRevision", "buildDigest", "configDigest", "fixtureDigest")},
            "results": [
                {
                    **{
                        key: cell[key]
                        for key in (
                            "requirementId",
                            "journeyId",
                            "state",
                            "method",
                            "probe",
                            "disposition",
                            "expected",
                        )
                    },
                    "resultId": f"result-{index}",
                    "status": "PASS",
                    "observed": "Synthetic test",
                    "artifactIds": ["test-report"],
                }
                for index, cell in enumerate(cells)
                if not cell["human"]
            ],
            "artifacts": [artifact],
        }
        source["sourceDigest"] = gate._hve_canonical_digest(source, "hve-a11y:evidence-source:v1")
        empty_source = {**source, "sourceRunId": "test-empty-source", "results": [], "artifacts": []}
        empty_source["sourceDigest"] = gate._hve_canonical_digest(
            {key: value for key, value in empty_source.items() if key != "sourceDigest"},
            "hve-a11y:evidence-source:v1",
        )
        bundle = hve_runtime[1].compose_evidence(
            asset_catalog=assets,
            requirement_catalog=requirements,
            scope=scope,
            run_context=run,
            sources=[source, empty_source],
            state_proofs=[],
        )
        for name, value in {
            "asset-journeys.json": assets,
            "requirement-methods.json": requirements,
            "evidence-scope.json": scope,
            "run-context.json": run,
            "state-proofs.json": [],
            "evidence-source.json": source,
            "evidence-playwright.json": empty_source,
            "source-check-result.json": {"status": "passed"},
        }.items():
            gate._write_json(evidence_root / "inputs" / name, value)
        gate._write_json(evidence_root / "evidence-bundle.json", bundle)
        gate._write_json(
            evidence_root / "evidence-summary.json",
            {
                "verdict": "PASS",
                "bundleDigest": bundle["bundleDigest"],
            },
        )
        handoff = evidence_root / "reviewer-handoff"
        templates = []
        for cell in cells:
            if cell["human"]:
                path = handoff / "supplement-templates" / f"{cell['cellId']}.json"
                gate._write_json(path, {"cellId": cell["cellId"], "templateStatus": "awaiting-qualified-review"})
                templates.append(
                    {
                        "path": path.relative_to(handoff).as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
        gate._write_json(handoff / "reviewer-campaign.json", {"templates": templates, "artifacts": {}})
        commands = []
        for command_id in gate._DOCUSAURUS_VALIDATION_COMMANDS:
            path = evidence_root / "command-results" / f"{command_id}.json"
            gate._write_json(path, {"commandId": command_id, "status": "passed"})
            commands.append(
                {
                    "commandId": command_id,
                    "status": "passed",
                    "resultArtifactDigest": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        validation = {"commands": commands, "revision": {"sourceRevision": run["sourceRevision"]}}
        gate._write_json(evidence_root / "validation-input.json", validation)
        validation["manifestDigest"] = gate._hve_canonical_digest(validation, "hve-a11y:validation-manifest:v1")
        gate._write_json(evidence_root / "accessibility-validation-manifest.json", validation)
        gate._write_json(evidence_root / "schema-validation-report.json", {"status": "valid"})
        return root

    def test_complete_package_relocates_and_recomposes_without_changing_envelopes(
        self, package_source: Path, hve_runtime: tuple[Path, Any]
    ) -> None:
        staged = package_source / "retained-output"
        manifest = gate.stage_docusaurus_package(package_source, staged)
        moved = package_source / "fresh-download"
        shutil.copytree(staged, moved)
        assert manifest["complete"] is True
        assert gate.verify_docusaurus_package(moved, require_complete=True, harness_root=hve_runtime[0]) == manifest
        inputs = moved / gate._DOCUSAURUS_EVIDENCE_ROOT / "inputs"
        original_inputs = package_source / gate._DOCUSAURUS_EVIDENCE_ROOT / "inputs"
        assert (inputs / "evidence-source.json").read_bytes() == (original_inputs / "evidence-source.json").read_bytes()
        values = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in inputs.glob("*.json")}
        rebuilt = hve_runtime[1].compose_evidence(
            asset_catalog=values["asset-journeys.json"],
            requirement_catalog=values["requirement-methods.json"],
            scope=values["evidence-scope.json"],
            run_context=values["run-context.json"],
            sources=[values["evidence-source.json"], values["evidence-playwright.json"]],
            state_proofs=values["state-proofs.json"],
        )
        assert rebuilt["bundleDigest"] == manifest["bundleDigest"]
        assert (
            gate.validate_composed_docusaurus_bundle(
                moved / gate._DOCUSAURUS_EVIDENCE_ROOT / "evidence-bundle.json",
                moved,
                harness_root=hve_runtime[0],
                required_completeness="automated",
            )["verdict"]
            == "PASS"
        )
        assert (moved / "docs" / "docusaurus" / "test-results" / "contrast-crops" / "test.png").is_file()
        build_files = list((moved / "docs" / "docusaurus" / "build").rglob("*"))
        assert gate._digest_paths([path for path in build_files if path.is_file()], moved) == manifest["buildDigest"]

    def test_complete_verification_requires_pinned_harness(self, package_source: Path) -> None:
        staged = package_source / "retained-output"
        gate.stage_docusaurus_package(package_source, staged)
        with pytest.raises(ValueError, match="harness"):
            gate.verify_docusaurus_package(staged, require_complete=True)

    @pytest.mark.parametrize("changed", ["build", "crop", "template", "source", "manifest", "input"])
    def test_changed_dependency_is_retained_only_as_non_promotable_diagnostics(
        self,
        package_source: Path,
        changed: str,
    ) -> None:
        paths = {
            "build": package_source / "docs" / "docusaurus" / "build" / "index.html",
            "crop": package_source / gate._DOCUSAURUS_RESULTS_ROOT / "contrast-crops" / "test.png",
            "source": package_source / ".github" / "accessibility" / "asset-journeys.json",
            "manifest": package_source / gate._DOCUSAURUS_EVIDENCE_ROOT / "evidence-bundle.json",
            "input": package_source / gate._DOCUSAURUS_EVIDENCE_ROOT / "inputs" / "asset-journeys.json",
            "template": next(
                (package_source / gate._DOCUSAURUS_EVIDENCE_ROOT / "reviewer-handoff" / "supplement-templates").glob(
                    "*.json"
                )
            ),
        }
        if changed == "manifest":
            bundle = json.loads(paths[changed].read_text(encoding="utf-8"))
            bundle["bundleDigest"] = "0" * 64
            gate._write_json(paths[changed], bundle)
        elif changed == "input":
            catalog = json.loads(paths[changed].read_text(encoding="utf-8"))
            catalog["journeys"][0]["role"] = "different role"
            gate._write_json(paths[changed], catalog)
        else:
            paths[changed].write_bytes(b"changed")
        target = package_source / "retained-output"
        manifest = gate.stage_docusaurus_package(package_source, target)
        assert manifest["complete"] is False
        assert manifest["promotable"] is False
        assert any("mismatch" in failure for failure in manifest["missing"])
        assert (target / paths[changed].relative_to(package_source)).read_bytes() == paths[changed].read_bytes()
        assert gate.verify_docusaurus_package(target) == manifest
        with pytest.raises(ValueError, match="incomplete"):
            gate.verify_docusaurus_package(target, require_complete=True)

    def test_missing_producer_retains_available_diagnostics_without_a_pass(self, tmp_path: Path) -> None:
        source = tmp_path / "repository"
        artifact = source / "docs" / "docusaurus" / "test-results" / "browser-version.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"channel":"chrome","version":"test"}', encoding="utf-8")
        package = source / "artifacts" / "accessibility" / "docusaurus" / "retained"

        result = gate.stage_docusaurus_package(source, package)

        assert result["complete"] is False
        assert result["promotable"] is False
        assert result["missing"]
        assert (package / artifact.relative_to(source)).read_bytes() == artifact.read_bytes()
        relocated = tmp_path / "download"
        shutil.copytree(package, relocated)
        assert gate.verify_docusaurus_package(relocated)["complete"] is False
        with pytest.raises(ValueError, match="incomplete"):
            gate.verify_docusaurus_package(relocated, require_complete=True)

    def test_partial_staging_does_not_import_unavailable_hve(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "repository"
        source.mkdir()
        target = tmp_path / "retained"

        def unavailable_hve(name: str, package: str | None = None) -> Any:
            raise AssertionError(f"Partial packaging must not import HVE: {name}")

        monkeypatch.setattr(gate.importlib, "import_module", unavailable_hve)
        status = main(
            [
                "--stage-docusaurus-package",
                str(target),
                "--repository-root",
                str(source),
                "--harness-root",
                str(tmp_path / "missing-hve"),
            ]
        )
        result = json.loads(capsys.readouterr().out)
        assert status == gate.EXIT_FAILURE
        assert result["complete"] is False
        assert result["promotable"] is False
        assert gate.verify_docusaurus_package(target)["complete"] is False

    @pytest.mark.parametrize("tamper", ["bytes", "size", "extra", "hidden", "duplicate", "escape"])
    def test_package_manifest_rejects_mutation(self, tmp_path: Path, tamper: str) -> None:
        source = tmp_path / "repository"
        artifact = source / "docs" / "docusaurus" / "test-results" / "browser-version.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"version":"123"}', encoding="utf-8")
        package = tmp_path / "retained"
        gate.stage_docusaurus_package(source, package)
        retained = package / artifact.relative_to(source)
        if tamper == "bytes":
            retained.write_text('{"version":"321"}', encoding="utf-8")
        elif tamper == "size":
            retained.write_bytes(b"x")
        elif tamper in {"extra", "hidden"}:
            (package / (".hidden" if tamper == "hidden" else "extra.json")).write_text("{}", encoding="utf-8")
        else:
            manifest_path = package / "package-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if tamper == "duplicate":
                manifest["files"].append(manifest["files"][0])
            else:
                manifest["files"][0]["path"] = "../outside.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(ValueError):
            gate.verify_docusaurus_package(package)

    def test_existing_nonempty_target_is_not_overwritten(self, tmp_path: Path) -> None:
        source = tmp_path / "repository"
        source.mkdir()
        target = tmp_path / "retained"
        target.mkdir()
        marker = target / "unrelated.json"
        marker.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="empty"):
            gate.stage_docusaurus_package(source, target)
        assert marker.read_text(encoding="utf-8") == "{}"

    def test_symlinked_producer_is_rejected(self, tmp_path: Path) -> None:
        source = tmp_path / "repository"
        artifact = source / "docs" / "docusaurus" / "test-results" / "browser-version.json"
        artifact.parent.mkdir(parents=True)
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        try:
            artifact.symlink_to(outside)
        except OSError:
            pytest.skip("Symbolic links require privileges on this host")
        with pytest.raises(ValueError, match="Symlink"):
            gate.stage_docusaurus_package(source, tmp_path / "retained")


class TestDocusaurusSourceManifest:
    @staticmethod
    def _repository(tmp_path: Path) -> Path:
        root = tmp_path / "repository"
        (root / "docs" / "docusaurus" / "build").mkdir(parents=True)
        (root / "scripts" / "accessibility").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / ".github" / "accessibility").mkdir(parents=True)
        (root / ".github" / "workflows").mkdir()
        (root / "docs" / "docusaurus" / "source.ts").write_text("export const value = 1;\n", encoding="utf-8")
        (root / "scripts" / "accessibility" / "evidence_gate.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "tests" / "test_accessibility_evidence.py").write_text("def test_value(): pass\n", encoding="utf-8")
        (root / ".github" / "accessibility" / "ledger.json").write_text("{}\n", encoding="utf-8")
        (root / ".github" / "workflows" / "docusaurus-tests.yml").write_text("name: test\n", encoding="utf-8")
        subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "test"], cwd=root, check=True)
        return root

    def test_given_tracked_and_untracked_changes_when_manifested_then_identity_changes(self, tmp_path: Path) -> None:
        root = self._repository(tmp_path)
        first = prepare_docusaurus_source_manifest(
            root, root / "artifacts/accessibility/docusaurus/source-input-manifest.json", "pull-request"
        )
        (root / "docs" / "docusaurus" / "source.ts").write_text("export const value = 2;\n", encoding="utf-8")
        (root / "docs" / "docusaurus" / "new-source.ts").write_text("export const added = true;\n", encoding="utf-8")

        second = prepare_docusaurus_source_manifest(
            root, root / "artifacts/accessibility/docusaurus/source-input-manifest.json", "pull-request"
        )

        assert first["sourceInputDigest"] != second["sourceInputDigest"]
        assert second["dirty"] is True
        assert "docs/docusaurus/new-source.ts" in {item["path"] for item in second["files"]}

    def test_given_generated_output_when_manifested_then_identity_is_unchanged(self, tmp_path: Path) -> None:
        root = self._repository(tmp_path)
        first = prepare_docusaurus_source_manifest(
            root, root / "artifacts/accessibility/docusaurus/source-input-manifest.json", "pull-request"
        )
        (root / "docs" / "docusaurus" / "build" / "generated.js").write_text("generated\n", encoding="utf-8")

        second = prepare_docusaurus_source_manifest(
            root, root / "artifacts/accessibility/docusaurus/source-input-manifest.json", "pull-request"
        )

        assert first["sourceInputDigest"] == second["sourceInputDigest"]

    @pytest.mark.parametrize(
        "relative",
        [
            "scripts/accessibility/promotion.py",
            "tests/test_accessibility_promotion.py",
            ".github/workflows/deploy-docs.yml",
            ".github/workflows/docusaurus-accessibility-promotion.yml",
        ],
    )
    def test_promotion_controls_bind_source_identity_and_remain_portable(self, tmp_path: Path, relative: str) -> None:
        root = self._repository(tmp_path)
        first = prepare_docusaurus_source_manifest(
            root, root / "artifacts/accessibility/docusaurus/source-input-manifest.json", "pull-request"
        )
        added = root / relative
        added.write_text("controlled source\n", encoding="utf-8")
        second = prepare_docusaurus_source_manifest(
            root, root / "artifacts/accessibility/docusaurus/source-input-manifest.json", "pull-request"
        )
        assert first["sourceInputDigest"] != second["sourceInputDigest"]
        assert relative in {item["path"] for item in second["files"]}
        assert gate._package_path(root, relative) == added

    def test_given_dirty_release_sources_when_manifested_then_rejects(self, tmp_path: Path) -> None:
        root = self._repository(tmp_path)
        (root / "docs" / "docusaurus" / "source.ts").write_text("dirty\n", encoding="utf-8")

        with pytest.raises(ValueError, match="requires clean declared source roots"):
            prepare_docusaurus_source_manifest(
                root, root / "artifacts/accessibility/docusaurus/source-input-manifest.json", "release"
            )


class TestDocusaurusContrastReview:
    @staticmethod
    def _write_inputs(
        root: Path,
        statuses: tuple[str, str] = (
            "computed-ratio-pass-candidate",
            "computed-ratio-pass-candidate",
        ),
        include_second: bool = True,
    ) -> tuple[Path, Path]:
        crop_dir = root / "crawl" / "contrast-evidence"
        crop_dir.mkdir(parents=True)
        (crop_dir / "evidence.png").write_bytes(b"crop")
        entries = [
            {
                "count": 1,
                "html": "<span>text</span>",
                "reason": "nonBmp",
                "routeFamilies": ["/docs"],
                "signature": signature,
                "state": "default",
                "target": '["span"]',
                "theme": theme,
            }
            for signature, theme in (("1" * 16, "light"), ("2" * 16, "dark"))
        ]
        baseline_path = root / "baseline.json"
        baseline_path.write_text(json.dumps({"entries": entries, "schemaVersion": 1}), encoding="utf-8")
        measurements = [
            {
                "background": "rgb(255, 255, 255)",
                "backgroundImage": None,
                "evidencePath": "contrast-evidence/evidence.png",
                "foreground": "rgb(0, 0, 0)",
                "methodStatus": status,
                "obscured": False,
                "opacity": 1,
                "ratio": 21 if "pass" in status else 2,
                "reason": "nonBmp",
                "requiredRatio": 4.5,
                "route": "/docs",
                "signature": signature,
                "state": "default",
                "target": '["span"]',
                "theme": theme,
                "tupleDigest": "a" * 64,
            }
            for (signature, theme), status in zip((("1" * 16, "light"), ("2" * 16, "dark")), statuses, strict=True)
        ]
        if not include_second:
            measurements.pop()
        crawl_path = root / "crawl" / "site-crawl-results.json"
        crawl_path.write_text(
            json.dumps({"results": [{"contrastEvidence": measurements}], "schemaVersion": 2}),
            encoding="utf-8",
        )
        return baseline_path, crawl_path

    def test_given_complete_signature_evidence_when_built_then_family_identity_is_stable(self, tmp_path: Path) -> None:
        baseline_path, crawl_path = self._write_inputs(tmp_path)

        first = prepare_docusaurus_contrast_review(tmp_path, baseline_path, crawl_path, tmp_path / "first.json")
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline["entries"].reverse()
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
        crawl = json.loads(crawl_path.read_text(encoding="utf-8"))
        crawl["results"][0]["contrastEvidence"].reverse()
        crawl_path.write_text(json.dumps(crawl), encoding="utf-8")
        second = prepare_docusaurus_contrast_review(tmp_path, baseline_path, crawl_path, tmp_path / "second.json")

        assert first["totals"] == {
            "families": 1,
            "signatures": 2,
            "statuses": {"computed-ratio-pass-candidate": 2},
        }
        assert first["families"] == second["families"]
        assert {item["signature"] for item in first["signatures"]} == {"1" * 16, "2" * 16}
        crop_digest = hashlib.sha256(b"crop").hexdigest()
        assert all(item["evidence"][0]["evidenceDigest"] == crop_digest for item in first["signatures"])
        assert all(set(item["requiredReviewerFields"].values()) == {None} for item in first["signatures"])

    def test_given_subthreshold_member_when_built_then_failure_candidate_blocks_family_sharing(
        self, tmp_path: Path
    ) -> None:
        baseline_path, crawl_path = self._write_inputs(
            tmp_path,
            statuses=("computed-ratio-pass-candidate", "computed-ratio-failure-candidate"),
        )

        result = prepare_docusaurus_contrast_review(tmp_path, baseline_path, crawl_path, tmp_path / "review.json")

        assert result["totals"]["statuses"] == {
            "computed-ratio-failure-candidate": 1,
            "computed-ratio-pass-candidate": 1,
        }
        assert result["families"][0]["sharedConclusion"] is None

    def test_given_mixed_statuses_within_one_signature_when_built_then_family_sharing_is_blocked(
        self, tmp_path: Path
    ) -> None:
        baseline_path, crawl_path = self._write_inputs(tmp_path)
        crawl = json.loads(crawl_path.read_text(encoding="utf-8"))
        for measurement in list(crawl["results"][0]["contrastEvidence"]):
            mixed = dict(measurement)
            mixed["methodStatus"] = "qualified-review-required"
            crawl["results"][0]["contrastEvidence"].append(mixed)
        crawl_path.write_text(json.dumps(crawl), encoding="utf-8")

        result = prepare_docusaurus_contrast_review(tmp_path, baseline_path, crawl_path, tmp_path / "review.json")

        assert result["families"][0]["sharedConclusion"] is None

    def test_given_missing_signature_evidence_when_built_then_generation_fails_closed(self, tmp_path: Path) -> None:
        baseline_path, crawl_path = self._write_inputs(tmp_path, include_second=False)

        with pytest.raises(ValueError, match=r"missing=.*2222222222222222"):
            prepare_docusaurus_contrast_review(tmp_path, baseline_path, crawl_path, tmp_path / "review.json")


class TestDocusaurusReviewerHandoff:
    @staticmethod
    def _write_inputs(root: Path, *, remove_cell: bool = False) -> tuple[Path, Path]:
        for source in (_ASSET_LEDGER_PATH, _REQUIREMENT_LEDGER_PATH):
            destination = root / ".github" / "accessibility" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        assets, _, _, expected = gate._docusaurus_contract(root, "release")
        cells = [item for item in expected if item["human"]]
        if remove_cell:
            cells.pop()
        release = {
            "bundleDigest": "b" * 64,
            "catalogs": {"assetJourney": assets},
            "expectedCells": cells,
            "runManifest": {
                "buildDigest": "c" * 64,
                "campaignId": "docusaurus-accessibility",
                "configDigest": "d" * 64,
                "fixtureDigest": "e" * 64,
                "sourceRevision": "f" * 40,
            },
            "schemaVersion": "1.0.0",
            "scopeCompleteness": {"releaseEvidence": "incomplete", "reviewerEvidence": "pending"},
        }
        contrast = {
            "schemaVersion": "1.0.0",
            "totals": {"families": 55, "signatures": 111, "statuses": {"qualified-review-required": 111}},
        }
        release_path = root / "release.json"
        contrast_path = root / "contrast.json"
        release_path.write_text(json.dumps(release), encoding="utf-8")
        contrast_path.write_text(json.dumps(contrast), encoding="utf-8")
        binding_path = root / "docs" / "docusaurus" / "a11y-screen-reader.bindings.json"
        binding_path.parent.mkdir(parents=True)
        binding_path.write_text(
            json.dumps(
                {
                    "caseBindings": {
                        f"case-{index}": {
                            "executions": [
                                {
                                    "assertions": [{"type": "contains", "value": "expected"}],
                                    "commands": [{"kind": "pause", "durationMs": 500}],
                                    "id": f"execution-{index}",
                                    "route": f"/route-{index}",
                                    "state": "initial",
                                    "title": f"Task {index}",
                                }
                            ],
                            "targetRefs": [f"target-{index}"],
                        }
                        for index in range(6)
                    }
                }
            ),
            encoding="utf-8",
        )
        return release_path, contrast_path

    def test_given_release_cells_when_built_then_templates_bind_identity_without_claiming_review(
        self, tmp_path: Path
    ) -> None:
        release_path, contrast_path = self._write_inputs(tmp_path)

        result = prepare_docusaurus_reviewer_handoff(tmp_path, release_path, contrast_path, tmp_path / "handoff")
        template = json.loads((tmp_path / "handoff" / result["templates"][0]["path"]).read_text(encoding="utf-8"))

        assert result["totals"] == {"humanCells": 64, "templates": 64}
        assert result["methodCounts"]["JAWS"] == 4
        assert result["methodCounts"]["NVDA"] == 4
        assert result["assistiveTechnologyRunbook"]["methods"] == ["NVDA", "JAWS"]
        assert len(result["assistiveTechnologyRunbook"]["executionRecipes"]) == 6
        assert (
            result["templates"][0]["sha256"]
            == hashlib.sha256((tmp_path / "handoff" / result["templates"][0]["path"]).read_bytes()).hexdigest()
        )
        assert template["templateStatus"] == "awaiting-qualified-review"
        assert template["schemaVersion"] == "1.0.0-template"
        assert set(template["requiredReviewerFields"].values()) == {None}
        assert template["privacyBoundary"]["restrictedObservationsStoredInGit"] is False
        assert len(list((tmp_path / "handoff" / "supplement-templates").glob("*.json"))) == 64

    def test_given_release_inventory_drift_when_built_then_handoff_fails_closed(self, tmp_path: Path) -> None:
        release_path, contrast_path = self._write_inputs(tmp_path, remove_cell=True)

        with pytest.raises(ValueError, match="canonical human cell inventory drifted"):
            prepare_docusaurus_reviewer_handoff(tmp_path, release_path, contrast_path, tmp_path / "handoff")

    @pytest.mark.parametrize("mutation", ["duplicate", "proposition", "method"])
    def test_human_cell_identity_and_proposition_cannot_drift(self, tmp_path: Path, mutation: str) -> None:
        release_path, contrast_path = self._write_inputs(tmp_path)
        bundle = json.loads(release_path.read_text(encoding="utf-8"))
        if mutation == "duplicate":
            bundle["expectedCells"].append(bundle["expectedCells"][0])
        elif mutation == "proposition":
            bundle["expectedCells"][0]["expected"] = "Unreviewed proposition"
        else:
            bundle["expectedCells"][0]["method"] = "SOURCE_INSPECTION"
        gate._write_json(release_path, bundle)
        with pytest.raises(ValueError, match=r"Duplicate|inventory drifted"):
            prepare_docusaurus_reviewer_handoff(tmp_path, release_path, contrast_path, tmp_path / "handoff")


class TestDocusaurusScreenReaderConsumer:
    def test_given_local_catalog_and_binding_when_loaded_then_selected_cases_are_closed(self) -> None:
        # Act
        catalog = json.loads(_DOCUSAURUS_SCREEN_READER_CATALOG_PATH.read_text(encoding="utf-8"))
        binding = json.loads(_DOCUSAURUS_SCREEN_READER_BINDING_PATH.read_text(encoding="utf-8"))

        # Assert
        expected_cases = {
            "SR-INTEGRITY-001",
            "HVE-NVDA-006",
            "HVE-NVDA-010",
            "HVE-NVDA-011",
            "HVE-NVDA-014",
        }
        assert {item["caseId"] for item in catalog["cases"]} == expected_cases
        assert set(binding["caseBindings"]) == expected_cases
        assert binding["catalogId"] == catalog["catalogId"]
        assert binding["product"]["surfaceProfiles"] == ["document"]
        assert "bundleDiscovery" not in binding
        assert all(item.get("executions") for item in binding["caseBindings"].values())

    def test_given_docusaurus_workflow_when_reviewed_then_hve_and_binding_checks_are_retained(self) -> None:
        # Act
        workflow = _load_yaml(_DOCUSAURUS_WORKFLOW_PATH)
        steps = {step["name"]: step for step in workflow["jobs"]["docusaurus"]["steps"]}

        # Assert
        assert 'cd "$skill_root"' in steps["Run pinned HVE accessibility self-tests"]["run"]
        self_test_run = steps["Run pinned HVE accessibility self-tests"]["run"]
        assert "node --test tests/runtime_a11y/runner/*.test.mjs" in self_test_run
        binding_run = steps["Validate Docusaurus screen-reader binding"]["run"]
        assert "materializeMethodCells" in binding_run
        assert "screen-reader-method-cells.json" in binding_run


class TestPresentationTaxonomyActivation:
    def test_given_no_slide_taxonomy_when_validated_then_guard_is_inactive(self, tmp_path: Path) -> None:
        # Act
        result = validate_presentation_taxonomy(tmp_path)

        # Assert
        assert result == {"signals": [], "status": "inactive"}

    def test_given_partial_slide_taxonomy_when_validated_then_guard_fails_closed(self, tmp_path: Path) -> None:
        # Arrange
        (tmp_path / "slides").mkdir()

        # Act and assert
        with pytest.raises(ValueError, match="Presentation taxonomy is active"):
            validate_presentation_taxonomy(tmp_path)

    def test_given_complete_slide_taxonomy_when_validated_then_guard_is_active(self, tmp_path: Path) -> None:
        # Arrange
        (tmp_path / "slides").mkdir()
        docs_root = tmp_path / "docs" / "docusaurus"
        (docs_root / "e2e").mkdir(parents=True)
        (docs_root / "e2e" / "slides.spec.ts").write_text("test('slides', () => {})\n", encoding="utf-8")
        (docs_root / "a11y-screen-reader.bindings.json").write_text(
            json.dumps(
                {
                    "product": {"surfaceProfiles": ["document", "presentation"]},
                    "bundleDiscovery": {
                        "metadataSource": "deck.json",
                        "servedRouteTemplate": "/slides/{slug}.html",
                    },
                }
            ),
            encoding="utf-8",
        )

        # Act
        result = validate_presentation_taxonomy(tmp_path)

        # Assert
        assert result == {"signals": ["slides"], "status": "active"}


class TestDocusaurusValidationManifestInput:
    @pytest.fixture()
    def validation_outcomes(self) -> list[str]:
        return [
            "hve-self-tests=success",
            "screen-reader-binding=success",
            "evidence-contracts=success",
            "presentation-taxonomy=success",
            "accessibility-lint=success",
            "label-consistency=success",
            "test-coverage=success",
            "browser=success",
            "contrast-review=success",
            "compose-evidence=success",
            "validate-evidence=success",
        ]

    def test_given_complete_outcomes_when_prepared_then_input_is_digest_bound(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        validation_outcomes: list[str],
    ) -> None:
        # Arrange
        artifact_root = tmp_path / "artifacts" / "accessibility" / "docusaurus"
        artifact_root.mkdir(parents=True)
        (artifact_root / "contrast-review.json").write_text("{}\n", encoding="utf-8")
        (artifact_root / "evidence-bundle.json").write_text("{}\n", encoding="utf-8")
        (artifact_root / "screen-reader-method-cells.json").write_text("{}\n", encoding="utf-8")

        def run_stub(command: list[str], **kwargs: Any) -> SimpleNamespace:
            if command[:2] == ["git", "diff"]:
                return SimpleNamespace(stdout=b"diff")
            if command[:2] == ["git", "status"]:
                return SimpleNamespace(stdout="")
            if command[0] == "node":
                assert kwargs["env"]["NODE_DISABLE_COMPILE_CACHE"] == "1"
                return SimpleNamespace(stdout="v24.19.0\n")
            return SimpleNamespace(stdout="uv 0.9.0\n")

        monkeypatch.setattr("scripts.accessibility.evidence_gate.subprocess.run", run_stub)

        # Act
        output = artifact_root / "validation-input.json"
        payload = prepare_docusaurus_validation_input(
            tmp_path,
            output,
            "a" * 40,
            validation_outcomes,
        )

        # Assert
        assert output.is_file()
        assert len(payload["commands"]) == 11
        assert all(item["status"] == "passed" for item in payload["commands"])
        assert payload["revision"]["tracked"] is True
        assert len(payload["generatedInventory"]) == 3
        assert payload["assistiveTechnologySample"]["advisory"] is True
        assert len(payload["assistiveTechnologySample"]["journeys"]) == 5

    def test_given_missing_outcome_when_prepared_then_fails_closed(
        self, tmp_path: Path, validation_outcomes: list[str]
    ) -> None:
        # Act and assert
        with pytest.raises(ValueError, match="Missing Docusaurus validation outcomes"):
            prepare_docusaurus_validation_input(
                tmp_path,
                tmp_path / "validation-input.json",
                "a" * 40,
                validation_outcomes[:-1],
            )

    def test_given_pr_workflow_when_reviewed_then_accessibility_filter_and_required_gate_are_present(self) -> None:
        # Act
        workflow = _load_yaml(_PR_WORKFLOW_PATH)
        changes_job = workflow["jobs"]["changes"]
        accessibility_job = workflow["jobs"]["accessibility-evidence"]

        # Assert
        assert changes_job["outputs"]["accessibility"] == "${{ steps.filter.outputs.accessibility }}"
        filter_step = next(step for step in changes_job["steps"] if step.get("id") == "filter")
        assert filter_step["run"] == "node scripts/ci/select-checks.mjs"
        contract = json.loads((_REPOSITORY_ROOT / "scripts/ci/ci-contract.json").read_text(encoding="utf-8"))
        assert "accessibility" in contract["selectors"]
        lane = next(item for item in contract["lanes"] if item["id"] == "accessibility-evidence")
        assert lane["selector"] == "accessibility"
        assert lane["outcomeSchema"] == "required"
        assert accessibility_job["uses"] == "./.github/workflows/accessibility-evidence.yml"
        assert accessibility_job["needs"] == "changes"
        assert accessibility_job["if"] == "needs.changes.outputs.accessibility == 'true'"
        assert accessibility_job["with"]["run-product-evidence"] is True
        assert workflow["jobs"]["pr-validation-summary"]["needs"].count("accessibility-evidence") == 1


class TestAccessibilityRuntimeConfigs:
    @pytest.fixture()
    def runtime_configs(self) -> dict[str, dict[str, Any]]:
        paths = {
            "viewer": _REPOSITORY_ROOT / "data-management" / "viewer" / "frontend" / "a11y-runtime.config.json",
            "viewer-docs": _REPOSITORY_ROOT / "data-management" / "viewer" / "backend" / "a11y-runtime.config.json",
            "vlm-docs": _REPOSITORY_ROOT / "evaluation" / "vlm_judge" / "a11y-api-runtime.config.json",
            "shim-docs": _REPOSITORY_ROOT / "evaluation" / "vlm_judge" / "a11y-openai-shim-runtime.config.json",
        }
        return {name: json.loads(path.read_text(encoding="utf-8")) for name, path in paths.items()}

    def test_given_runtime_configs_when_loaded_then_targets_are_unique_loopback_only(
        self, runtime_configs: dict[str, dict[str, Any]]
    ) -> None:
        # Act
        base_urls = {config["baseUrl"] for config in runtime_configs.values()}

        # Assert
        assert base_urls == {
            "http://127.0.0.1:5173",
            "http://127.0.0.1:8000",
            "http://127.0.0.1:8001",
            "http://127.0.0.1:8002",
        }
        assert all(config["serveMode"] == "served" for config in runtime_configs.values())

    def test_given_runtime_configs_when_loaded_then_all_web_journey_ids_are_accounted_for(
        self, runtime_configs: dict[str, dict[str, Any]]
    ) -> None:
        # Arrange
        expected_viewer = {f"V{number:02d}" for number in range(1, 20)}
        expected_docs = {f"F{number:02d}" for number in range(1, 7)}

        # Act
        viewer_ids = {surface["id"] for surface in runtime_configs["viewer"]["surfaces"]}
        docs_ids = {
            surface["id"]
            for name, config in runtime_configs.items()
            if name != "viewer"
            for surface in config["surfaces"]
        }

        # Assert
        assert viewer_ids == expected_viewer
        assert docs_ids == expected_docs

    def test_given_runtime_configs_when_loaded_then_states_and_probe_scopes_resolve(
        self, runtime_configs: dict[str, dict[str, Any]]
    ) -> None:
        # Act
        unresolved: list[str] = []
        for name, config in runtime_configs.items():
            surfaces = {surface["id"]: surface for surface in config["surfaces"]}
            if not surfaces or not config["probeScoping"]:
                unresolved.append(f"{name}:empty")
            for surface_id, surface in surfaces.items():
                if not surface.get("states") or not surface.get("selector") or not surface.get("route"):
                    unresolved.append(f"{name}:{surface_id}:incomplete")
            for scope in config["probeScoping"]:
                if not scope.get("surfaces") or not scope.get("states"):
                    unresolved.append(f"{name}:{scope.get('probe')}:empty-scope")
                for surface_id in scope.get("surfaces", []):
                    if surface_id not in surfaces:
                        unresolved.append(f"{name}:{scope['probe']}:{surface_id}")

        # Assert
        assert unresolved == []

    def test_given_runtime_configs_when_serialized_then_no_remote_or_credential_values_are_retained(
        self, runtime_configs: dict[str, dict[str, Any]]
    ) -> None:
        # Act
        serialized = json.dumps(runtime_configs).lower()

        # Assert
        assert "https://" not in serialized
        assert "password" not in serialized
        assert "token" not in serialized
        assert "secret" not in serialized


class TestGitHubSurfaceContracts:
    def test_given_repository_github_sources_when_validated_then_all_in_scope_surfaces_are_accounted_for(self) -> None:
        # Act
        summary = validate_github_surfaces(_REPOSITORY_ROOT)

        # Assert
        assert summary == {
            "issueForms": 5,
            "markdownTemplates": 1,
            "contactLinks": 2,
            "zeroInputDispatches": 9,
            "typedDispatches": 3,
            "providerOutcomes": "NOT_ASSESSED",
        }

    def test_given_github_sources_when_validated_then_deploy_docs_remains_excluded(self) -> None:
        # Act
        summary = validate_github_surfaces(_REPOSITORY_ROOT)

        # Assert
        assert "deploy-docs" not in summary

    def test_given_main_workflow_when_reviewed_then_accessibility_blocks_release(self) -> None:
        # Act
        workflow = _load_yaml(_MAIN_WORKFLOW_PATH)
        accessibility_job = workflow["jobs"]["accessibility-evidence"]
        docusaurus_job = workflow["jobs"]["docusaurus-tests"]
        release_needs = workflow["jobs"]["release-please"]["needs"]

        # Assert
        assert accessibility_job["uses"] == "./.github/workflows/accessibility-evidence.yml"
        assert accessibility_job["with"]["run-product-evidence"] is True
        assert docusaurus_job["uses"] == "./.github/workflows/docusaurus-tests.yml"
        assert docusaurus_job["with"]["evidence-cadence"] == "release"
        assert docusaurus_job["with"]["required-completeness"] == "automated"
        assert release_needs.count("accessibility-evidence") == 1
        assert release_needs.count("docusaurus-tests") == 1

    def test_given_accessibility_workflows_when_reviewed_then_playwright_has_one_owner(self) -> None:
        # Act
        viewer_workflow = _load_yaml(_VIEWER_WORKFLOW_PATH)
        accessibility_workflow = _load_yaml(_ACCESSIBILITY_WORKFLOW_PATH)
        viewer_steps = {step["name"]: step for step in viewer_workflow["jobs"]["frontend-checks"]["steps"]}
        evidence_steps = {step["name"]: step for step in accessibility_workflow["jobs"]["evidence"]["steps"]}

        # Assert
        assert all("npm run test:a11y" not in str(step.get("run", "")) for step in viewer_steps.values())
        assert any("npm run test:coverage" in str(step.get("run", "")) for step in viewer_steps.values())
        product_run = evidence_steps["Run deterministic product evidence"]["run"]
        assert 'npm --prefix "$GITHUB_WORKSPACE" ci' in product_run
        assert "npm run test:a11y" in product_run
        assert "viewer-evidence-manifest.json" in product_run
        assert "expected_titles != actual_titles" in product_run
        assert "VIEWER_A11Y_EXPECTED_TESTS" not in product_run
        assert "playwright-results.xml" in product_run
        upload = evidence_steps["Upload accessibility evidence"]
        assert "artifacts/accessibility/" in upload["with"]["path"]
        assert upload["if"] == "always()"
        assert upload["with"]["retention-days"] == 30

    def test_given_direct_evidence_events_when_parsed_then_viewer_product_evidence_runs(self) -> None:
        workflow = _load_yaml(_ACCESSIBILITY_WORKFLOW_PATH)
        product_step = next(
            step
            for step in workflow["jobs"]["evidence"]["steps"]
            if step.get("name") == "Run deterministic product evidence"
        )

        condition = product_step["if"]
        assert "github.event_name == 'schedule'" in condition
        assert "github.event_name == 'workflow_dispatch'" in condition
        assert "viewer-evidence-manifest.json" in product_step["run"]

    def test_given_docs_publication_when_parsed_then_release_evidence_is_required(self) -> None:
        workflow = _load_yaml(_REPOSITORY_ROOT / ".github" / "workflows" / "deploy-docs.yml")

        triggers = workflow.get("on", workflow.get(True))
        assert triggers["workflow_run"]["workflows"] == ["Docusaurus Accessibility Promotion"]
        steps = {step["name"]: step for step in workflow["jobs"]["build"]["steps"]}
        verification = steps["Verify release completeness and retained build digest"]["run"]
        assert "verify-promoted" in verification
        assert '--harness-root "$HVE_SKILL_ROOT"' in verification
        upload = steps["Upload exact reviewed site bytes"]
        assert upload["with"]["path"] == "artifacts/accessibility/publish/collection/docs/docusaurus/build"
        assert not any("npm run build" in str(step.get("run", "")) for step in steps.values())


class TestCanonicalAssetJourneyLedger:
    @pytest.fixture()
    def asset_ledger(self) -> AssetJourneyLedger:
        return AssetJourneyLedger.model_validate_json(_ASSET_LEDGER_PATH.read_text(encoding="utf-8"))

    def test_given_canonical_ledger_when_validated_then_accounts_for_every_inventory_id(
        self, asset_ledger: AssetJourneyLedger
    ) -> None:
        # Arrange
        expected_ids = {f"V{number:02d}" for number in range(1, 20)}
        expected_ids |= {f"F{number:02d}" for number in range(1, 7)}
        expected_ids |= {f"G{number:02d}" for number in range(1, 10)}
        expected_ids |= {f"P{number:02d}" for number in range(1, 4)}
        expected_ids |= {f"D{number:02d}" for number in range(1, 4)}
        expected_ids |= {f"A{number:02d}" for number in range(1, 11)}
        expected_ids |= {f"J{number:02d}" for number in range(1, 17)}
        expected_ids |= {f"H{number:02d}" for number in range(1, 13)}
        expected_ids |= {f"DCS{number:02d}" for number in range(1, 14)}

        # Act
        actual_ids = {journey.journey_id for journey in asset_ledger.journeys}

        # Assert
        assert actual_ids == expected_ids

    def test_given_canonical_ledger_when_validated_then_preserves_all_lifecycle_classes(
        self, asset_ledger: AssetJourneyLedger
    ) -> None:
        # Act
        lifecycle_values = {asset.lifecycle.value for asset in asset_ledger.assets}
        lifecycle_values |= {journey.lifecycle.value for journey in asset_ledger.journeys}

        # Assert
        assert lifecycle_values == {
            "ABSENT",
            "ACTIVE",
            "ASPIRATIONAL",
            "BROKEN",
            "CONDITIONAL",
            "DORMANT",
            "ENVIRONMENT_GATED",
            "MACHINE_ONLY",
            "PLANNED",
        }

    def test_given_viewer_asset_when_auth_is_gated_then_lifecycle_axes_remain_independent(
        self, asset_ledger: AssetJourneyLedger
    ) -> None:
        # Arrange
        viewer = next(asset for asset in asset_ledger.assets if asset.asset_id == "ASSET-VIEWER")
        authentication = next(journey for journey in asset_ledger.journeys if journey.journey_id == "V01")

        # Assert
        assert (viewer.lifecycle.value, authentication.lifecycle.value) == ("ACTIVE", "ENVIRONMENT_GATED")

    def test_given_canonical_ledger_when_resolving_evidence_then_all_paths_exist(
        self, asset_ledger: AssetJourneyLedger
    ) -> None:
        # Act
        references = [reference for asset in asset_ledger.assets for reference in asset.evidence_refs]
        references += [reference for journey in asset_ledger.journeys for reference in journey.evidence_refs]
        missing = sorted({reference for reference in references if not (_REPOSITORY_ROOT / reference).exists()})

        # Assert
        assert missing == []

    def test_given_canonical_ledger_when_reviewed_then_exclusions_and_product_boundaries_are_explicit(
        self, asset_ledger: AssetJourneyLedger
    ) -> None:
        # Arrange
        exclusions = {exclusion.scope for exclusion in asset_ledger.exclusions}
        playback = next(journey for journey in asset_ledger.journeys if journey.journey_id == "V06")
        docusaurus = next(asset for asset in asset_ledger.assets if asset.asset_id == "ASSET-DOCUSAURUS")
        fixture = next(fixture for fixture in asset_ledger.fixtures if fixture.fixture_id == "FIXTURE-DOCUSAURUS")

        # Assert
        assert exclusions == {"GitHub Pages", "external directory"}
        assert playback.dependency_refs == ["project-user-ui-surface-area-plan:P02-T02"]
        assert docusaurus.ownership.value == "PROJECT"
        assert "local" in docusaurus.activation.lower()
        assert fixture.environment_class.value == "LOCAL_CI"
        assert fixture.synthetic is False

    def test_given_docusaurus_journeys_when_reviewed_then_local_build_and_manual_boundaries_are_explicit(
        self, asset_ledger: AssetJourneyLedger
    ) -> None:
        # Arrange
        journeys = {journey.journey_id: journey for journey in asset_ledger.journeys}
        docusaurus_ids = {f"DCS{number:02d}" for number in range(1, 14)}

        # Assert
        assert docusaurus_ids <= journeys.keys()
        assert {journeys[journey_id].fixture_id for journey_id in docusaurus_ids} == {"FIXTURE-DOCUSAURUS"}
        assert journeys["DCS13"].lifecycle.value == "ENVIRONMENT_GATED"
        assert "NVDA" in {method.value for method in journeys["DCS13"].required_methods}
        assert journeys["DCS01"].predecessor_ids == []
        assert journeys["DCS13"].successor_ids == []

    def test_given_resize_and_zoom_journeys_when_reviewed_then_deciding_and_supplemental_scopes_are_separate(
        self, asset_ledger: AssetJourneyLedger
    ) -> None:
        journeys = {journey.journey_id: journey for journey in asset_ledger.journeys}

        assert "browser zoom" not in journeys["V19"].state.lower()
        assert "browser zoom" not in journeys["DCS09"].state.lower()
        assert "zoom" not in journeys["DCS10"].state.lower()
        assert "text resize" in journeys["DCS10"].state.lower()
        assert "supplemental browser zoom" in journeys["DCS13"].state.lower()
        assert "zoom-specific" in journeys["DCS13"].invalidation.lower()

    def test_given_non_web_archetypes_when_reviewed_then_host_and_lifecycle_boundaries_are_explicit(
        self, asset_ledger: AssetJourneyLedger
    ) -> None:
        # Arrange
        journeys = {journey.journey_id: journey for journey in asset_ledger.journeys}

        # Assert
        assert journeys["A03"].dependency_refs == ["Host behavior requires qualified evidence"]
        assert journeys["A04"].lifecycle.value == "ENVIRONMENT_GATED"
        assert journeys["J07"].lifecycle.value == "BROKEN"
        assert "absent" in " ".join(journeys["J07"].dependency_refs).lower()

    def test_given_decision_artifacts_when_reviewed_then_integrity_cannot_replace_equivalence(
        self, asset_ledger: AssetJourneyLedger
    ) -> None:
        # Arrange
        journeys = {journey.journey_id: journey for journey in asset_ledger.journeys}

        # Assert
        assert "SOURCE_INSPECTION" in [method.value for method in journeys["A05"].required_methods]
        assert "MEDIA_EQUIVALENCE_REVIEW" in [method.value for method in journeys["A06"].required_methods]
        for journey_id in ("A07", "A08", "A09"):
            methods = {method.value for method in journeys[journey_id].required_methods}
            assert methods == {"ARTIFACT_INTEGRITY", "MEDIA_EQUIVALENCE_REVIEW"}


class TestCanonicalRequirementEvidenceLedger:
    @pytest.fixture()
    def asset_ledger(self) -> AssetJourneyLedger:
        return AssetJourneyLedger.model_validate_json(_ASSET_LEDGER_PATH.read_text(encoding="utf-8"))

    @pytest.fixture()
    def requirement_ledger(self) -> RequirementEvidenceLedger:
        return RequirementEvidenceLedger.model_validate_json(_REQUIREMENT_LEDGER_PATH.read_text(encoding="utf-8"))

    def test_given_wcag_records_when_validated_then_all_55_active_aa_criteria_are_present(
        self, requirement_ledger: RequirementEvidenceLedger
    ) -> None:
        # Arrange
        expected = {
            "1.1.1",
            "1.2.1",
            "1.2.2",
            "1.2.3",
            "1.2.4",
            "1.2.5",
            "1.3.1",
            "1.3.2",
            "1.3.3",
            "1.3.4",
            "1.3.5",
            "1.4.1",
            "1.4.2",
            "1.4.3",
            "1.4.4",
            "1.4.5",
            "1.4.10",
            "1.4.11",
            "1.4.12",
            "1.4.13",
            "2.1.1",
            "2.1.2",
            "2.1.4",
            "2.2.1",
            "2.2.2",
            "2.3.1",
            "2.4.1",
            "2.4.2",
            "2.4.3",
            "2.4.4",
            "2.4.5",
            "2.4.6",
            "2.4.7",
            "2.4.11",
            "2.5.1",
            "2.5.2",
            "2.5.3",
            "2.5.4",
            "2.5.7",
            "2.5.8",
            "3.1.1",
            "3.1.2",
            "3.2.1",
            "3.2.2",
            "3.2.3",
            "3.2.4",
            "3.2.6",
            "3.3.1",
            "3.3.2",
            "3.3.3",
            "3.3.4",
            "3.3.7",
            "3.3.8",
            "4.1.2",
            "4.1.3",
        }

        # Act
        actual = {
            requirement.criterion
            for requirement in requirement_ledger.requirements
            if requirement.framework is Framework.WCAG_2_2
        }

        # Assert
        assert actual == expected

    def test_given_framework_ledger_when_counted_then_frameworks_remain_separate(
        self, requirement_ledger: RequirementEvidenceLedger
    ) -> None:
        # Act
        counts = {
            framework: sum(requirement.framework is framework for requirement in requirement_ledger.requirements)
            for framework in Framework
        }

        # Assert
        assert counts == {
            Framework.WCAG_2_2: 55,
            Framework.REVISED_SECTION_508: 14,
            Framework.SECTION_504: 15,
        }

    def test_given_framework_ledger_when_validated_then_all_six_evidence_packs_are_assigned(
        self, requirement_ledger: RequirementEvidenceLedger
    ) -> None:
        # Act
        actual = {
            evidence_pack.value
            for requirement in requirement_ledger.requirements
            for evidence_pack in requirement.evidence_packs
        }

        # Assert
        assert actual == {
            "E1_SOURCE_CONTENT",
            "E2_DETERMINISTIC_WEB",
            "E3_NON_TEXT_GENERATED",
            "E4_HUMAN_AT",
            "E5_PROVIDER_NATIVE_ROBOT",
            "E6_PROCESS_GOVERNANCE",
        }

    def test_given_framework_ledger_when_resolving_journeys_then_all_bindings_exist(
        self, asset_ledger: AssetJourneyLedger, requirement_ledger: RequirementEvidenceLedger
    ) -> None:
        # Arrange
        journey_ids = {journey.journey_id for journey in asset_ledger.journeys}

        # Act
        unresolved = sorted(
            {
                journey_id
                for requirement in requirement_ledger.requirements
                for journey_id in requirement.journey_ids
                if journey_id not in journey_ids
            }
        )

        # Assert
        assert unresolved == []

    def test_given_wcag_records_when_reviewed_then_every_criterion_has_docusaurus_coverage(
        self, requirement_ledger: RequirementEvidenceLedger
    ) -> None:
        # Arrange
        wcag_requirements = [
            requirement
            for requirement in requirement_ledger.requirements
            if requirement.framework is Framework.WCAG_2_2
        ]

        # Act
        uncovered = [
            requirement.criterion
            for requirement in wcag_requirements
            if not any(journey_id.startswith("DCS") for journey_id in requirement.journey_ids)
        ]

        # Assert
        assert uncovered == []

    def test_given_guarded_docusaurus_criteria_when_reviewed_then_activation_and_human_review_are_bound(
        self, requirement_ledger: RequirementEvidenceLedger
    ) -> None:
        # Arrange
        guarded_criteria = {
            "1.2.1",
            "1.2.2",
            "1.2.3",
            "1.2.4",
            "1.2.5",
            "1.3.5",
            "1.4.2",
            "2.1.4",
            "2.2.1",
            "2.2.2",
            "2.5.1",
            "2.5.4",
            "2.5.7",
            "3.1.2",
            "3.3.1",
            "3.3.3",
            "3.3.4",
            "3.3.7",
            "3.3.8",
        }
        requirements = {
            requirement.criterion: requirement
            for requirement in requirement_ledger.requirements
            if requirement.framework is Framework.WCAG_2_2
        }

        # Act
        guarded_bindings = {
            criterion: set(requirements[criterion].journey_ids) & {"DCS12", "DCS13"} for criterion in guarded_criteria
        }

        # Assert
        assert guarded_bindings == {criterion: {"DCS12", "DCS13"} for criterion in guarded_criteria}

    def test_given_508_records_when_reviewed_then_official_chapter_identifiers_are_preserved(
        self, requirement_ledger: RequirementEvidenceLedger
    ) -> None:
        # Arrange
        records = {
            requirement.requirement_id: requirement.criterion
            for requirement in requirement_ledger.requirements
            if requirement.framework is Framework.REVISED_SECTION_508
        }

        # Assert
        assert "E201.1" in records["SECTION508-APPENDIX-A"]
        assert records["SECTION508-CHAPTER4"].startswith("401.1")
        assert records["SECTION508-CHAPTER5"].startswith("501.1")
        assert records["SECTION508-CHAPTER6"].startswith("601.1")
        assert "WCAG 2.0 SC 4.1.1" in records["SECTION508-WCAG20-4.1.1"]
        assert all("E205.5" not in criterion for criterion in records.values())

    def test_given_unassessed_framework_records_when_reviewed_then_unknown_facts_do_not_become_inapplicable(
        self, requirement_ledger: RequirementEvidenceLedger
    ) -> None:
        # Act
        unresolved = [
            requirement
            for requirement in requirement_ledger.requirements
            if requirement.framework in {Framework.REVISED_SECTION_508, Framework.SECTION_504}
        ]

        # Assert
        assert all(requirement.applicability.value == "UNDETERMINED" for requirement in unresolved)
        assert all(requirement.evidence_status.value == "NOT_ASSESSED" for requirement in unresolved)


def test_given_accessibility_runtime_contracts_when_inspected_then_native_toolchains_own_them() -> None:
    annotation_model = (_REPOSITORY_ROOT / "data-management/viewer/backend/src/api/models/annotations.py").read_text(
        encoding="utf-8"
    )
    viewer_manifest = json.loads(
        (_REPOSITORY_ROOT / "data-management/viewer/frontend/package.json").read_text(encoding="utf-8")
    )
    viewer_lock = json.loads((_REPOSITORY_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    breadcrumb_tsx = _REPOSITORY_ROOT / "docs/docusaurus/src/theme/DocBreadcrumbs/index.tsx"
    breadcrumb_js = _REPOSITORY_ROOT / "docs/docusaurus/src/theme/DocBreadcrumbs/index.js"

    assert annotation_model.splitlines()[7] == "from __future__ import annotations"
    assert viewer_manifest["devDependencies"]["@playwright/test"] == "1.61.1"
    assert viewer_lock["packages"]["data-management/viewer/frontend"]["devDependencies"]["@playwright/test"] == "1.61.1"
    assert breadcrumb_tsx.is_file()
    assert not breadcrumb_js.exists()
    assert "interface BreadcrumbLinkProps" in breadcrumb_tsx.read_text(encoding="utf-8")


def test_given_config_preview_when_run_then_reports_no_mutation(capsys: pytest.CaptureFixture[str]) -> None:
    # Act
    exit_code = main(["--config-preview"])
    output = capsys.readouterr().out

    # Assert
    assert exit_code == 0
    assert '"mutation": "none"' in output
