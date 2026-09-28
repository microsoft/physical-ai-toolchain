"""Evaluate VLA lifecycle evidence against an explicit promotion policy."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from .vla_contracts import (
        SCHEMA_VERSION,
        ContractError,
        RecordKind,
        fingerprint,
        load_record,
        promotion_policy_fingerprint,
        validate_promotion_policy,
        validate_record,
        write_record,
    )
except ImportError:
    from vla_contracts import (
        SCHEMA_VERSION,
        ContractError,
        RecordKind,
        fingerprint,
        load_record,
        promotion_policy_fingerprint,
        validate_promotion_policy,
        validate_record,
        write_record,
    )

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2


def load_policy(path: Path) -> dict[str, Any]:
    """Load and validate a promotion policy JSON file."""
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"Unable to load promotion policy {path}: {exc}") from exc
    if not isinstance(policy, dict):
        raise ContractError("Promotion policy must be a JSON object")
    validate_promotion_policy(policy)
    return policy


def decide_promotion(
    candidate: Mapping[str, Any],
    training: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    policy: Mapping[str, Any],
    model_name: str,
    evidence_job: str,
    policy_reference: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Return a validated promotion record without performing registry mutation."""
    validate_record(candidate, RecordKind.CANDIDATE)
    validate_record(training, RecordKind.RUN)
    validate_record(evaluation, RecordKind.EVALUATION)
    validate_promotion_policy(policy)
    if not model_name.strip():
        raise ContractError("Model name must not be empty")
    if candidate["evidence_job"] != evidence_job:
        raise ContractError("Candidate evidence job does not match the decision evidence job")

    inconclusive: list[str] = []
    failed: list[str] = []
    training_fingerprint = fingerprint(training)
    candidate_fingerprint = fingerprint(candidate)
    evaluation_fingerprint = fingerprint(evaluation)
    policy_fingerprint = promotion_policy_fingerprint(policy)

    if candidate["training_fingerprint"] != training_fingerprint:
        inconclusive.append("candidate_training_fingerprint_mismatch")
    if evaluation["run_fingerprint"] != training_fingerprint:
        inconclusive.append("evaluation_training_fingerprint_mismatch")
    if evaluation["candidate_fingerprint"] != candidate_fingerprint:
        inconclusive.append("evaluation_candidate_fingerprint_mismatch")
    if evaluation["dataset_fingerprint"] != candidate["dataset_fingerprint"]:
        inconclusive.append("evaluation_dataset_fingerprint_mismatch")
    if evaluation["status"] != "complete":
        inconclusive.append("evaluation_inconclusive")
    if evaluation["episodes_evaluated"] < policy["minimum_episodes"]:
        inconclusive.append("insufficient_episode_coverage")

    metrics = evaluation.get("metrics")
    if not isinstance(metrics, Mapping):
        metrics = {}
    for name, rule in sorted(policy["metric_rules"].items()):
        value = metrics.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            inconclusive.append(f"missing_metric:{name}")
            continue
        if rule["operator"] == "lte" and value > rule["limit"]:
            failed.append(f"threshold_exceeded:{name}")
        elif rule["operator"] == "gte" and value < rule["limit"]:
            failed.append(f"threshold_not_met:{name}")

    expected_baseline = policy.get("baseline")
    if expected_baseline is not None and evaluation.get("baseline") != expected_baseline:
        inconclusive.append("baseline_evidence_mismatch")

    reasons = sorted(set(inconclusive or failed))
    status = "inconclusive" if inconclusive else "failed" if failed else "passed"
    output_prefix = f"azureml://jobs/{evidence_job}/outputs"
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": RecordKind.PROMOTION.value,
        "created_at": created_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "model_name": model_name,
        "training_fingerprint": training_fingerprint,
        "candidate_fingerprint": candidate_fingerprint,
        "evaluation_fingerprint": evaluation_fingerprint,
        "policy_fingerprint": policy_fingerprint,
        "status": status,
        "reasons": reasons,
        "evidence_job": evidence_job,
        "producer_identity": policy["allowed_producer_identity"],
        "pipeline_contract_fingerprint": policy["pipeline_contract_fingerprint"],
        "code_revision": policy["code_revision"],
        "immutable_inputs": {
            "candidate": candidate["candidate_output"],
            "candidate_manifest": candidate["manifest_output"],
            "evaluation": evaluation["evaluation_output"],
            "policy": policy_reference,
            "decision": f"{output_prefix}/decision",
        },
    }
    validate_record(record, RecordKind.PROMOTION)
    return record


def create_parser() -> argparse.ArgumentParser:
    """Create the promotion-decision command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--training-record", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--decision-output", type=Path, required=True)
    parser.add_argument("--policy-output", type=Path, required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    """Load evidence and emit immutable decision and policy outputs."""
    evidence_job = os.environ.get("AZUREML_ROOT_RUN_ID") or os.environ.get("AZUREML_RUN_ID", "")
    if not evidence_job:
        raise ContractError("Azure ML evidence job identity is required")
    candidate = load_record(args.candidate_manifest / "candidate-manifest.json", RecordKind.CANDIDATE)
    training = load_record(args.training_record / "training-record.json", RecordKind.RUN)
    evaluation = load_record(args.evaluation / "evaluation-record.json", RecordKind.EVALUATION)
    policy = load_policy(args.policy)
    record = decide_promotion(
        candidate,
        training,
        evaluation,
        policy,
        args.model_name,
        evidence_job,
        f"azureml://jobs/{evidence_job}/outputs/policy",
    )
    args.policy_output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.policy, args.policy_output / "promotion-policy.json")
    write_record(args.decision_output / "promotion-decision.json", record)
    print(fingerprint(record))
    return EXIT_SUCCESS


def main() -> int:
    """Run promotion decision evaluation."""
    try:
        return run(create_parser().parse_args())
    except ContractError as exc:
        print(f"Promotion decision failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stderr.close()
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
