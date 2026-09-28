"""Tests for VLA promotion decisions and guarded registration."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from training.vla.scripts.decide_promotion import decide_promotion
from training.vla.scripts.guarded_register import guard_and_register, validate_local_evidence
from training.vla.scripts.vla_contracts import (
    ContractError,
    RecordKind,
    canonical_json,
    fingerprint,
    sha256_bytes,
    sha256_file,
    write_record,
)

_DIGEST = "a" * 64
_COMMIT = "b" * 40
_OUTPUT_PREFIX = "azureml://jobs/evidence-job/outputs"


def _record(kind: RecordKind) -> dict[str, Any]:
    return {"schema_version": 1, "kind": kind.value, "created_at": "2026-09-28T00:00:00Z"}


def _evidence() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    training = {
        **_record(RecordKind.RUN),
        "run_id": "training-run",
        "identities": {"dataset": _DIGEST},
        "effective_batch": {
            "micro_batch_per_rank": 1,
            "world_size": 1,
            "accumulation_steps": 1,
            "total": 1,
        },
    }
    candidate = {
        **_record(RecordKind.CANDIDATE),
        "policy_type": "pi0",
        "training_fingerprint": fingerprint(training),
        "dataset_fingerprint": _DIGEST,
        "artifact_manifest_sha256": _DIGEST,
        "evidence_job": "evidence-job",
        "candidate_output": f"{_OUTPUT_PREFIX}/candidate",
        "manifest_output": f"{_OUTPUT_PREFIX}/candidate_manifest",
    }
    evaluation = {
        **_record(RecordKind.EVALUATION),
        "evaluation_id": "evaluation-run",
        "run_fingerprint": fingerprint(training),
        "candidate_fingerprint": fingerprint(candidate),
        "dataset_fingerprint": _DIGEST,
        "status": "complete",
        "outputs_complete": True,
        "episodes_requested": 3,
        "episodes_evaluated": 3,
        "reasons": [],
        "evaluation_output": f"{_OUTPUT_PREFIX}/evaluation",
        "metrics": {"mse": 0.05, "throughput_hz": 35.0},
    }
    policy = {
        "schema_version": 1,
        "minimum_episodes": 3,
        "metric_rules": {
            "mse": {"operator": "lte", "limit": 0.1},
            "throughput_hz": {"operator": "gte", "limit": 30.0},
        },
        "allowed_producer_identity": "trusted-managed-identity",
        "pipeline_contract_fingerprint": _DIGEST,
        "code_revision": _COMMIT,
    }
    return candidate, training, evaluation, policy


def _decide(
    candidate: dict[str, Any],
    training: dict[str, Any],
    evaluation: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    return decide_promotion(
        candidate,
        training,
        evaluation,
        policy,
        "robot-policy",
        "evidence-job",
        f"{_OUTPUT_PREFIX}/policy",
        "2026-09-28T01:00:00Z",
    )


def test_complete_evidence_within_thresholds_passes_deterministically() -> None:
    evidence = _evidence()

    first = _decide(*evidence)
    second = _decide(*(deepcopy(record) for record in evidence))

    assert first == second
    assert first["status"] == "passed"
    assert first["reasons"] == []


@pytest.mark.parametrize(
    ("metric", "value", "reason"),
    [
        ("mse", 0.2, "threshold_exceeded:mse"),
        ("throughput_hz", 20.0, "threshold_not_met:throughput_hz"),
    ],
)
def test_threshold_violation_fails(metric: str, value: float, reason: str) -> None:
    candidate, training, evaluation, policy = _evidence()
    evaluation["metrics"][metric] = value

    decision = _decide(candidate, training, evaluation, policy)

    assert decision["status"] == "failed"
    assert decision["reasons"] == [reason]


def test_missing_required_metric_is_inconclusive() -> None:
    candidate, training, evaluation, policy = _evidence()
    del evaluation["metrics"]["mse"]

    decision = _decide(candidate, training, evaluation, policy)

    assert decision["status"] == "inconclusive"
    assert decision["reasons"] == ["missing_metric:mse"]


def test_incomplete_evaluation_is_inconclusive() -> None:
    candidate, training, evaluation, policy = _evidence()
    evaluation.update(
        status="inconclusive",
        episodes_evaluated=2,
        reasons=["insufficient_episode_coverage"],
    )

    decision = _decide(candidate, training, evaluation, policy)

    assert decision["status"] == "inconclusive"
    assert decision["reasons"] == ["evaluation_inconclusive", "insufficient_episode_coverage"]


def test_identity_mismatch_is_inconclusive() -> None:
    candidate, training, evaluation, policy = _evidence()
    evaluation["candidate_fingerprint"] = "c" * 64

    decision = _decide(candidate, training, evaluation, policy)

    assert decision["status"] == "inconclusive"
    assert decision["reasons"] == ["evaluation_candidate_fingerprint_mismatch"]


def test_required_baseline_mismatch_is_inconclusive() -> None:
    candidate, training, evaluation, policy = _evidence()
    policy["baseline"] = {"asset_id": "azureml:baseline:1", "fingerprint": _DIGEST}

    decision = _decide(candidate, training, evaluation, policy)

    assert decision["status"] == "inconclusive"
    assert decision["reasons"] == ["baseline_evidence_mismatch"]


def _registration_evidence(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate, training, evaluation, policy = _evidence()
    candidate_path = tmp_path / "candidate"
    candidate_path.mkdir()
    model_path = candidate_path / "model.safetensors"
    model_path.write_bytes(b"candidate")
    files = [{"path": model_path.name, "sha256": sha256_file(model_path)}]
    candidate["artifact_manifest_sha256"] = sha256_bytes(canonical_json(files).encode("utf-8"))
    candidate["files"] = files
    evaluation["candidate_fingerprint"] = fingerprint(candidate)
    decision = _decide(candidate, training, evaluation, policy)

    candidate_manifest = tmp_path / "candidate-manifest"
    evaluation_dir = tmp_path / "evaluation"
    policy_dir = tmp_path / "policy"
    decision_dir = tmp_path / "decision"
    write_record(candidate_manifest / "candidate-manifest.json", candidate)
    write_record(evaluation_dir / "evaluation-record.json", evaluation)
    policy_dir.mkdir()
    (policy_dir / "promotion-policy.json").write_text(json.dumps(policy), encoding="utf-8")
    write_record(decision_dir / "promotion-decision.json", decision)

    kwargs = {
        "candidate_path": candidate_path,
        "candidate_manifest_path": candidate_manifest / "candidate-manifest.json",
        "evaluation_path": evaluation_dir / "evaluation-record.json",
        "policy_path": policy_dir / "promotion-policy.json",
        "decision_path": decision_dir / "promotion-decision.json",
        "expected_model_name": "robot-policy",
        "expected_evidence_job": "evidence-job",
        "expected_producer_identity": "trusted-managed-identity",
        "expected_pipeline_contract_fingerprint": _DIGEST,
        "expected_code_revision": _COMMIT,
    }
    return kwargs, {
        "candidate_manifest": candidate,
        "evaluation": evaluation,
        "policy": policy,
        "decision": decision,
    }


def _trusted_job(identity: str = "trusted-managed-identity") -> dict[str, Any]:
    return {
        "name": "evidence-job",
        "status": "Completed",
        "identity": {"principal_id": identity},
        "tags": {"pipeline_contract_fingerprint": _DIGEST, "code_revision": _COMMIT},
        "outputs": {name: {} for name in ("candidate", "candidate_manifest", "evaluation", "policy", "decision")},
    }


def test_guarded_registration_registers_only_authoritative_passed_evidence(tmp_path: Path) -> None:
    kwargs, authoritative = _registration_evidence(tmp_path)
    backend = MagicMock()
    backend.resolve_job.return_value = _trusted_job()
    backend.load_evidence.return_value = authoritative
    backend.register.return_value = "registered"

    result = guard_and_register(
        lambda: validate_local_evidence(**kwargs),
        lambda: backend,
        kwargs["candidate_path"],
        "robot-policy",
        "evidence-job",
    )

    assert result == "registered"
    backend.register.assert_called_once()


@pytest.mark.parametrize("status", ["failed", "inconclusive"])
def test_non_passed_decision_denies_before_backend_creation(tmp_path: Path, status: str) -> None:
    kwargs, _ = _registration_evidence(tmp_path)
    decision = json.loads(kwargs["decision_path"].read_text(encoding="utf-8"))
    decision["status"] = status
    decision["reasons"] = ["denied"]
    write_record(kwargs["decision_path"], decision)
    backend_factory = MagicMock()

    with pytest.raises(ContractError, match="requires a passed promotion decision"):
        guard_and_register(
            lambda: validate_local_evidence(**kwargs),
            backend_factory,
            kwargs["candidate_path"],
            "robot-policy",
            "evidence-job",
        )

    backend_factory.assert_not_called()


def test_tampered_candidate_denies_before_backend_creation(tmp_path: Path) -> None:
    kwargs, _ = _registration_evidence(tmp_path)
    (kwargs["candidate_path"] / "model.safetensors").write_bytes(b"tampered")
    backend_factory = MagicMock()

    with pytest.raises(ContractError, match="does not match candidate files"):
        guard_and_register(
            lambda: validate_local_evidence(**kwargs),
            backend_factory,
            kwargs["candidate_path"],
            "robot-policy",
            "evidence-job",
        )

    backend_factory.assert_not_called()


def test_mismatched_model_name_denies_before_backend_creation(tmp_path: Path) -> None:
    kwargs, _ = _registration_evidence(tmp_path)
    kwargs["expected_model_name"] = "different-model"
    backend_factory = MagicMock()

    with pytest.raises(ContractError, match="model name does not match"):
        guard_and_register(
            lambda: validate_local_evidence(**kwargs),
            backend_factory,
            kwargs["candidate_path"],
            "different-model",
            "evidence-job",
        )

    backend_factory.assert_not_called()


@pytest.mark.parametrize("content", [None, "not-json"])
def test_missing_or_malformed_decision_denies_before_backend_creation(tmp_path: Path, content: str | None) -> None:
    kwargs, _ = _registration_evidence(tmp_path)
    if content is None:
        kwargs["decision_path"].unlink()
    else:
        kwargs["decision_path"].write_text(content, encoding="utf-8")
    backend_factory = MagicMock()

    with pytest.raises(ContractError, match="Unable to load lifecycle record"):
        guard_and_register(
            lambda: validate_local_evidence(**kwargs),
            backend_factory,
            kwargs["candidate_path"],
            "robot-policy",
            "evidence-job",
        )

    backend_factory.assert_not_called()


def test_self_consistent_forged_evidence_cannot_reach_registry(tmp_path: Path) -> None:
    kwargs, authoritative = _registration_evidence(tmp_path)
    forged_authoritative = deepcopy(authoritative)
    forged_authoritative["candidate_manifest"]["artifact_manifest_sha256"] = "c" * 64
    backend = MagicMock()
    backend.resolve_job.return_value = _trusted_job()
    backend.load_evidence.return_value = forged_authoritative

    with pytest.raises(ContractError, match="does not match the authoritative evidence"):
        guard_and_register(
            lambda: validate_local_evidence(**kwargs),
            lambda: backend,
            kwargs["candidate_path"],
            "robot-policy",
            "evidence-job",
        )

    backend.register.assert_not_called()


def test_untrusted_job_identity_cannot_reach_registry(tmp_path: Path) -> None:
    kwargs, authoritative = _registration_evidence(tmp_path)
    backend = MagicMock()
    backend.resolve_job.return_value = _trusted_job("attacker")
    backend.load_evidence.return_value = authoritative

    with pytest.raises(ContractError, match="producer identity is not trusted"):
        guard_and_register(
            lambda: validate_local_evidence(**kwargs),
            lambda: backend,
            kwargs["candidate_path"],
            "robot-policy",
            "evidence-job",
        )

    backend.register.assert_not_called()
