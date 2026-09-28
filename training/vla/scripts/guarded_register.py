"""Register a finalized VLA candidate only after trusted evidence verification."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

try:
    from .decide_promotion import load_policy
    from .finalize_candidate import verify_candidate
    from .vla_contracts import (
        ContractError,
        RecordKind,
        canonical_json,
        fingerprint,
        load_record,
        promotion_policy_fingerprint,
    )
except ImportError:
    from decide_promotion import load_policy
    from finalize_candidate import verify_candidate
    from vla_contracts import (
        ContractError,
        RecordKind,
        canonical_json,
        fingerprint,
        load_record,
        promotion_policy_fingerprint,
    )

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2

_EVIDENCE_FILES = {
    "candidate_manifest": "candidate-manifest.json",
    "evaluation": "evaluation-record.json",
    "policy": "promotion-policy.json",
    "decision": "promotion-decision.json",
}


class RegistrationBackend(Protocol):
    """Azure ML operations used only after local evidence passes."""

    def resolve_job(self, job_name: str) -> Any:
        """Resolve the authoritative evidence pipeline job."""

    def load_evidence(self, job_name: str) -> dict[str, dict[str, Any]]:
        """Download authoritative small evidence outputs from the job."""

    def register(self, candidate_path: Path, model_name: str, policy_type: str, tags: Mapping[str, str]) -> Any:
        """Register the finalized candidate folder."""


def validate_local_evidence(
    candidate_path: Path,
    candidate_manifest_path: Path,
    evaluation_path: Path,
    policy_path: Path,
    decision_path: Path,
    expected_model_name: str,
    expected_evidence_job: str,
    expected_producer_identity: str,
    expected_pipeline_contract_fingerprint: str,
    expected_code_revision: str,
) -> dict[str, Any]:
    """Validate all local evidence before constructing an Azure client."""
    candidate = verify_candidate(candidate_path, candidate_manifest_path)
    evaluation = load_record(evaluation_path, RecordKind.EVALUATION)
    policy = load_policy(policy_path)
    decision = load_record(decision_path, RecordKind.PROMOTION)
    if decision["status"] != "passed" or decision["reasons"]:
        raise ContractError("Registration requires a passed promotion decision without reasons")
    if decision["model_name"] != expected_model_name:
        raise ContractError("Promotion decision model name does not match the requested model name")
    if decision["evidence_job"] != expected_evidence_job or candidate["evidence_job"] != expected_evidence_job:
        raise ContractError("Evidence records do not match the requested evidence job")
    if decision["candidate_fingerprint"] != fingerprint(candidate):
        raise ContractError("Promotion decision candidate fingerprint does not match the candidate manifest")
    if decision["evaluation_fingerprint"] != fingerprint(evaluation):
        raise ContractError("Promotion decision evaluation fingerprint does not match evaluation evidence")
    if decision["training_fingerprint"] != candidate["training_fingerprint"]:
        raise ContractError("Promotion decision training fingerprint does not match the candidate")
    if decision["policy_fingerprint"] != promotion_policy_fingerprint(policy):
        raise ContractError("Promotion decision policy fingerprint does not match the policy")
    expected_policy_values = {
        "producer_identity": expected_producer_identity,
        "pipeline_contract_fingerprint": expected_pipeline_contract_fingerprint,
        "code_revision": expected_code_revision,
    }
    for decision_field, expected_value in expected_policy_values.items():
        if decision[decision_field] != expected_value:
            raise ContractError(f"Promotion decision {decision_field} does not match the trusted expectation")
    if policy["allowed_producer_identity"] != expected_producer_identity:
        raise ContractError("Promotion policy producer identity does not match the trusted expectation")
    if policy["pipeline_contract_fingerprint"] != expected_pipeline_contract_fingerprint:
        raise ContractError("Promotion policy pipeline contract does not match the trusted expectation")
    if policy["code_revision"] != expected_code_revision:
        raise ContractError("Promotion policy code revision does not match the trusted expectation")

    output_prefix = f"azureml://jobs/{expected_evidence_job}/outputs"
    expected_references = {
        "candidate": f"{output_prefix}/candidate",
        "candidate_manifest": f"{output_prefix}/candidate_manifest",
        "evaluation": f"{output_prefix}/evaluation",
        "policy": f"{output_prefix}/policy",
        "decision": f"{output_prefix}/decision",
    }
    if decision["immutable_inputs"] != expected_references:
        raise ContractError("Promotion decision immutable inputs do not match the evidence job outputs")
    if candidate["candidate_output"] != expected_references["candidate"]:
        raise ContractError("Candidate output reference does not match the evidence job")
    if candidate["manifest_output"] != expected_references["candidate_manifest"]:
        raise ContractError("Candidate manifest reference does not match the evidence job")
    if evaluation["evaluation_output"] != expected_references["evaluation"]:
        raise ContractError("Evaluation output reference does not match the evidence job")
    return {
        "candidate": candidate,
        "evaluation": evaluation,
        "policy": policy,
        "decision": decision,
    }


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def validate_authoritative_job(
    job: Any,
    expected_job: str,
    expected_identity: str,
    expected_pipeline_contract_fingerprint: str,
    expected_code_revision: str,
) -> None:
    """Validate completed job identity, tags, and required named outputs."""
    if str(_field(job, "name", "")) != expected_job:
        raise ContractError("Resolved Azure ML job name does not match the evidence job")
    if str(_field(job, "status", "")).lower() != "completed":
        raise ContractError("Evidence job must have completed successfully")
    identity = _field(job, "identity", {})
    identity_values = {
        str(value)
        for value in (
            _field(identity, "principal_id"),
            _field(identity, "client_id"),
            _field(identity, "resource_id"),
        )
        if value
    }
    user_assigned = _field(identity, "user_assigned_identities", {})
    if isinstance(user_assigned, Mapping):
        identity_values.update(str(value) for value in user_assigned)
    if expected_identity not in identity_values:
        raise ContractError("Evidence job producer identity is not trusted")
    tags = _field(job, "tags", {})
    contract_matches = (
        isinstance(tags, Mapping)
        and tags.get("pipeline_contract_fingerprint") == expected_pipeline_contract_fingerprint
    )
    if not contract_matches:
        raise ContractError("Evidence job pipeline contract fingerprint does not match")
    if tags.get("code_revision") != expected_code_revision:
        raise ContractError("Evidence job code revision does not match")
    outputs = _field(job, "outputs", {})
    required_outputs = {*_EVIDENCE_FILES, "candidate"}
    if not isinstance(outputs, Mapping) or not required_outputs <= set(outputs):
        raise ContractError("Evidence job is missing required named outputs")


def validate_authoritative_evidence(
    local_evidence: Mapping[str, Mapping[str, Any]],
    authoritative_evidence: Mapping[str, Mapping[str, Any]],
) -> None:
    """Require mounted evidence to equal the records downloaded from the trusted job."""
    for name in _EVIDENCE_FILES:
        local_name = "candidate" if name == "candidate_manifest" else name
        if name not in authoritative_evidence:
            raise ContractError(f"Authoritative evidence is missing {name}")
        if canonical_json(local_evidence[local_name]) != canonical_json(authoritative_evidence[name]):
            raise ContractError(f"Mounted {name} does not match the authoritative evidence job output")


def guard_and_register(
    local_evidence_factory: Callable[[], dict[str, Any]],
    backend_factory: Callable[[], RegistrationBackend],
    candidate_path: Path,
    expected_model_name: str,
    expected_evidence_job: str,
) -> Any:
    """Validate local and authoritative evidence before registry mutation."""
    local_evidence = local_evidence_factory()
    backend = backend_factory()
    job = backend.resolve_job(expected_evidence_job)
    policy = local_evidence["policy"]
    validate_authoritative_job(
        job,
        expected_evidence_job,
        policy["allowed_producer_identity"],
        policy["pipeline_contract_fingerprint"],
        policy["code_revision"],
    )
    validate_authoritative_evidence(local_evidence, backend.load_evidence(expected_evidence_job))
    candidate = local_evidence["candidate"]
    decision = local_evidence["decision"]
    return backend.register(
        candidate_path,
        expected_model_name,
        candidate["policy_type"],
        {
            "evidence_job": expected_evidence_job,
            "candidate_fingerprint": decision["candidate_fingerprint"],
            "evaluation_fingerprint": decision["evaluation_fingerprint"],
            "policy_fingerprint": decision["policy_fingerprint"],
        },
    )


class AzureMLRegistrationBackend:
    """Azure ML evidence resolution and model registration backend."""

    def __init__(self, subscription_id: str, resource_group: str, workspace_name: str) -> None:
        from azure.ai.ml import MLClient
        from azure.identity import DefaultAzureCredential

        self._client = MLClient(DefaultAzureCredential(), subscription_id, resource_group, workspace_name)

    def resolve_job(self, job_name: str) -> Any:
        return self._client.jobs.get(job_name)

    def load_evidence(self, job_name: str) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for output_name, filename in _EVIDENCE_FILES.items():
                output_dir = root / output_name
                self._client.jobs.download(name=job_name, output_name=output_name, download_path=output_dir)
                matches = list(output_dir.rglob(filename))
                if len(matches) != 1:
                    raise ContractError(f"Unable to resolve authoritative {output_name} evidence")
                try:
                    record = json.loads(matches[0].read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ContractError(f"Unable to load authoritative {output_name} evidence: {exc}") from exc
                if not isinstance(record, dict):
                    raise ContractError(f"Authoritative {output_name} evidence must be a JSON object")
                records[output_name] = record
        return records

    def register(self, candidate_path: Path, model_name: str, policy_type: str, tags: Mapping[str, str]) -> Any:
        from azure.ai.ml.constants import AssetTypes
        from azure.ai.ml.entities import Model

        model = Model(
            path=str(candidate_path),
            name=model_name,
            description="LeRobot VLA policy approved by immutable promotion evidence",
            type=AssetTypes.CUSTOM_MODEL,
            tags={"framework": "lerobot", "policy_type": policy_type, **tags},
        )
        return self._client.models.create_or_update(model)


def create_parser() -> argparse.ArgumentParser:
    """Create the guarded registration command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--evidence-job", required=True)
    parser.add_argument("--producer-identity", required=True)
    parser.add_argument("--pipeline-contract-fingerprint", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--workspace-name", required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    """Validate evidence and register the finalized candidate."""
    def local_evidence_factory() -> dict[str, Any]:
        return validate_local_evidence(
            args.candidate,
            args.candidate_manifest / "candidate-manifest.json",
            args.evaluation / "evaluation-record.json",
            args.policy / "promotion-policy.json",
            args.decision / "promotion-decision.json",
            args.model_name,
            args.evidence_job,
            args.producer_identity,
            args.pipeline_contract_fingerprint,
            args.code_revision,
        )

    def backend_factory() -> AzureMLRegistrationBackend:
        return AzureMLRegistrationBackend(
            args.subscription_id,
            args.resource_group,
            args.workspace_name,
        )

    registered = guard_and_register(
        local_evidence_factory,
        backend_factory,
        args.candidate,
        args.model_name,
        args.evidence_job,
    )
    print(f"Model registered: {registered.name} (version: {registered.version})")
    return EXIT_SUCCESS


def main() -> int:
    """Run guarded VLA registration."""
    try:
        return run(create_parser().parse_args())
    except ContractError as exc:
        print(f"Registration denied: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stderr.close()
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
