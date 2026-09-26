from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


class PolicyOutputError(ValueError):
    """Raised when dependency review returns an incomplete or invalid policy output."""


@dataclass(frozen=True)
class ExceptionEvaluation:
    accepted: bool
    scoped_exception_count: int
    remaining_vulnerabilities: list[dict[str, Any]]
    invalid_license_count: int
    denied_change_count: int


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise PolicyOutputError(f"Dependency review output field {field!r} must be a list")
    return value


def _normalize_manifest(manifest: Any) -> str:
    if not isinstance(manifest, str):
        return ""
    if manifest.startswith("./"):
        manifest = manifest[2:]
    if manifest.startswith("/"):
        manifest = manifest[1:]
    return manifest


def _matches_exception(
    change: dict[str, Any],
    vulnerability: dict[str, Any],
    *,
    advisory: str,
    manifest: str,
    package: str,
) -> bool:
    return (
        change.get("change_type") == "added"
        and change.get("ecosystem") == "pip"
        and change.get("name") == package
        and _normalize_manifest(change.get("manifest")) == manifest
        and vulnerability.get("advisory_ghsa_id") == advisory
    )


def evaluate_exception(
    vulnerable_changes: Any,
    invalid_license_changes: Any,
    denied_changes: Any,
    *,
    advisory: str,
    manifest: str,
    package: str,
) -> ExceptionEvaluation:
    changes = _require_list(vulnerable_changes, "vulnerable-changes")
    if not isinstance(invalid_license_changes, dict):
        raise PolicyOutputError("Dependency review output field 'invalid-license-changes' must be an object")
    forbidden = _require_list(invalid_license_changes.get("forbidden"), "invalid-license-changes.forbidden")
    unresolved = _require_list(invalid_license_changes.get("unresolved"), "invalid-license-changes.unresolved")
    denied = _require_list(denied_changes, "denied-changes")

    scoped_exception_count = 0
    remaining_vulnerabilities = []
    for index, raw_change in enumerate(changes):
        if not isinstance(raw_change, dict):
            raise PolicyOutputError(f"Dependency review vulnerable change at index {index} must be an object")
        vulnerabilities = _require_list(
            raw_change.get("vulnerabilities"),
            f"vulnerable-changes[{index}].vulnerabilities",
        )
        remaining = []
        for vulnerability_index, raw_vulnerability in enumerate(vulnerabilities):
            if not isinstance(raw_vulnerability, dict):
                raise PolicyOutputError(
                    f"Dependency review vulnerability at index {index}:{vulnerability_index} must be an object"
                )
            if _matches_exception(
                raw_change,
                raw_vulnerability,
                advisory=advisory,
                manifest=manifest,
                package=package,
            ):
                scoped_exception_count += 1
            else:
                remaining.append(raw_vulnerability)
        if remaining:
            remaining_vulnerabilities.append({**raw_change, "vulnerabilities": remaining})

    invalid_license_count = len(forbidden) + len(unresolved)
    denied_change_count = len(denied)
    return ExceptionEvaluation(
        accepted=(
            scoped_exception_count > 0
            and not remaining_vulnerabilities
            and invalid_license_count == 0
            and denied_change_count == 0
        ),
        scoped_exception_count=scoped_exception_count,
        remaining_vulnerabilities=remaining_vulnerabilities,
        invalid_license_count=invalid_license_count,
        denied_change_count=denied_change_count,
    )


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise PolicyOutputError(f"Required environment variable {name} is missing or empty")
    return value


def _load_json_env(name: str) -> Any:
    raw = _require_env(name)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyOutputError(f"Environment variable {name} does not contain valid JSON") from exc


def main() -> int:
    if os.getenv("DEPENDENCY_REVIEW_OUTCOME") == "success":
        return 0

    try:
        advisory = _require_env("SCOPED_GHSA")
        manifest = _require_env("SCOPED_MANIFEST")
        package = _require_env("SCOPED_PACKAGE")
        evaluation = evaluate_exception(
            _load_json_env("VULNERABLE_CHANGES"),
            _load_json_env("INVALID_LICENSE_CHANGES"),
            _load_json_env("DENIED_CHANGES"),
            advisory=advisory,
            manifest=manifest,
            package=package,
        )
    except PolicyOutputError as exc:
        print(f"::error::{exc}")
        return 1

    if evaluation.accepted:
        print(f"::notice::Accepted {advisory} only for {package} in {manifest}")
        return 0

    print("::error::Dependency review found policy violations outside the path-scoped exception")
    print(json.dumps(evaluation.remaining_vulnerabilities, indent=2, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
