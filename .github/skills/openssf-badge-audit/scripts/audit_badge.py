from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

_PROJECT_ID = 12195
_PROJECT_NAME = "Physical AI Toolchain"
_REPOSITORY_URL = "https://github.com/microsoft/physical-ai-toolchain"
_PROJECT_URL = f"https://www.bestpractices.dev/projects/{_PROJECT_ID}.json"
_BADGE_URL = f"https://www.bestpractices.dev/projects/{_PROJECT_ID}/badge.json"
_CRITERION_SUFFIXES = ("_status", "_justification")
_BADGE_LEVELS = {"in_progress", "passing", "silver", "gold"}
_CRITERION_STATUSES = {"Met", "Unmet", "N/A", "?", "Unknown"}
_CRITERIA = frozenset(
    {
        "access_continuity",
        "accessibility_best_practices",
        "achieve_passing",
        "achieve_silver",
        "assurance_case",
        "automated_integration_testing",
        "build",
        "build_common_tools",
        "build_floss_tools",
        "build_non_recursive",
        "build_preserve_debug",
        "build_repeatable",
        "build_reproducible",
        "build_standard_variables",
        "bus_factor",
        "code_of_conduct",
        "code_review_standards",
        "coding_standards",
        "coding_standards_enforced",
        "contribution",
        "contribution_requirements",
        "contributors_unassociated",
        "copyright_per_file",
        "crypto_algorithm_agility",
        "crypto_call",
        "crypto_certificate_verification",
        "crypto_credential_agility",
        "crypto_floss",
        "crypto_keylength",
        "crypto_password_storage",
        "crypto_pfs",
        "crypto_published",
        "crypto_random",
        "crypto_tls12",
        "crypto_used_network",
        "crypto_verification_private",
        "crypto_weaknesses",
        "crypto_working",
        "dco",
        "delivery_mitm",
        "delivery_unsigned",
        "dependency_monitoring",
        "description_good",
        "discussion",
        "documentation_achievements",
        "documentation_architecture",
        "documentation_basics",
        "documentation_current",
        "documentation_interface",
        "documentation_quick_start",
        "documentation_roadmap",
        "documentation_security",
        "dynamic_analysis",
        "dynamic_analysis_enable_assertions",
        "dynamic_analysis_fixed",
        "dynamic_analysis_unsafe",
        "english",
        "enhancement_responses",
        "external_dependencies",
        "floss_license",
        "floss_license_osi",
        "governance",
        "hardened_site",
        "hardening",
        "implement_secure_design",
        "input_validation",
        "installation_common",
        "installation_development_quick",
        "installation_standard_variables",
        "interact",
        "interfaces_current",
        "internationalization",
        "know_common_errors",
        "know_secure_design",
        "license_location",
        "license_per_file",
        "maintained",
        "maintenance_or_update",
        "no_leaked_credentials",
        "regression_tests_added50",
        "release_notes",
        "release_notes_vulns",
        "repo_distributed",
        "repo_interim",
        "repo_public",
        "repo_track",
        "report_archive",
        "report_process",
        "report_responses",
        "report_tracker",
        "require_2FA",
        "roles_responsibilities",
        "secure_2FA",
        "security_review",
        "signed_releases",
        "sites_https",
        "sites_password_security",
        "small_tasks",
        "static_analysis",
        "static_analysis_common_vulnerabilities",
        "static_analysis_fixed",
        "static_analysis_often",
        "test",
        "test_branch_coverage80",
        "test_continuous_integration",
        "test_invocation",
        "test_most",
        "test_policy",
        "test_policy_mandated",
        "test_statement_coverage80",
        "test_statement_coverage90",
        "tests_are_added",
        "tests_documented_added",
        "two_person_review",
        "updateable_reused_components",
        "version_semver",
        "version_tags",
        "version_tags_signed",
        "version_unique",
        "vulnerabilities_critical_fixed",
        "vulnerabilities_fixed_60_days",
        "vulnerability_report_credit",
        "vulnerability_report_private",
        "vulnerability_report_process",
        "vulnerability_report_response",
        "vulnerability_response_process",
        "warnings",
        "warnings_fixed",
        "warnings_strict",
    }
)


class AuditError(RuntimeError):
    """Raised when badge evidence cannot be collected or validated."""


def find_repository_root(script_path: Path) -> Path:
    """Find the repository root from this script's installed skill location."""
    expected_suffix = Path(".github/skills/openssf-badge-audit/scripts/audit_badge.py")

    for candidate in (script_path.parent, *script_path.parents):
        try:
            relative_path = script_path.relative_to(candidate)
        except ValueError:
            continue
        if relative_path == expected_suffix:
            return candidate

    raise AuditError("Unable to identify the repository root from the script path.")


def fetch_json(url: str) -> JsonObject:
    """Fetch and decode one public JSON document."""
    request = Request(url, headers={"User-Agent": "physical-ai-toolchain-openssf-badge-audit/1.0"})

    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read()
    except HTTPError as error:
        raise AuditError(f"Request to {url} failed with HTTP {error.code}.") from error
    except TimeoutError as error:
        raise AuditError(f"Request to {url} timed out.") from error
    except URLError as error:
        raise AuditError(f"Request to {url} failed: {error.reason}.") from error

    try:
        decoded = payload.decode("utf-8")
        document = json.loads(decoded)
    except UnicodeDecodeError as error:
        raise AuditError(f"Response from {url} was not UTF-8.") from error
    except json.JSONDecodeError as error:
        raise AuditError(f"Response from {url} was not valid JSON.") from error

    if not isinstance(document, dict):
        raise AuditError(f"Response from {url} must be a JSON object.")

    return document


def read_repository_proposal(repository_root: Path) -> JsonObject:
    """Read the repository's proposed Best Practices questionnaire values."""
    proposal_path = repository_root / ".bestpractices.json"

    try:
        contents = proposal_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise AuditError(f"Required repository proposal is missing: {proposal_path}.") from error
    except OSError as error:
        raise AuditError(f"Unable to read repository proposal {proposal_path}: {error.strerror}.") from error

    try:
        proposal = json.loads(contents)
    except json.JSONDecodeError as error:
        raise AuditError(f"Repository proposal {proposal_path} is not valid JSON.") from error

    if not isinstance(proposal, dict):
        raise AuditError(f"Repository proposal {proposal_path} must be a JSON object.")

    return proposal


def require_string(document: JsonObject, field: str, source: str) -> str:
    """Return a required nonempty string field."""
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AuditError(f"{source} is missing required nonempty string field {field!r}.")
    return value


def require_integer(document: JsonObject, field: str, source: str) -> int:
    """Return a required integer field without accepting booleans."""
    value = document.get(field)
    if type(value) is not int:
        raise AuditError(f"{source} is missing required integer field {field!r}.")
    return value


def require_integer_range(document: JsonObject, field: str, source: str, minimum: int, maximum: int) -> int:
    """Return a required integer field within an inclusive range."""
    value = require_integer(document, field, source)
    if not minimum <= value <= maximum:
        raise AuditError(f"{source} field {field!r} must be between {minimum} and {maximum}.")
    return value


def validate_timestamp(value: str, source: str) -> None:
    """Require an ISO 8601 timestamp."""
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AuditError(f"{source} has an invalid updated_at timestamp: {value!r}.") from error


def validate_live_data(project: JsonObject, badge: JsonObject) -> None:
    """Validate project identity and fields required for a reproducible audit."""
    for source, document in (("Project response", project), ("Badge response", badge)):
        project_id = require_integer(document, "id", source)
        project_name = require_string(document, "name", source)
        if project_id != _PROJECT_ID or project_name != _PROJECT_NAME:
            raise AuditError(f"{source} identity does not match project {_PROJECT_ID} ({_PROJECT_NAME}).")

        updated_at = require_string(document, "updated_at", source)
        validate_timestamp(updated_at, source)

    if require_string(project, "repo_url", "Project response") != _REPOSITORY_URL:
        raise AuditError("Project response repository URL does not match this repository.")

    for source, document in (("Project response", project), ("Badge response", badge)):
        badge_level = require_string(document, "badge_level", source)
        if badge_level not in _BADGE_LEVELS:
            raise AuditError(f"{source} has unsupported badge_level {badge_level!r}.")

        require_integer_range(document, "tiered_percentage", source, 0, 300)

    for field in ("badge_percentage_0", "badge_percentage_1", "badge_percentage_2"):
        require_integer_range(project, field, "Project response", 0, 100)

    if project["badge_level"] != badge["badge_level"]:
        raise AuditError("Project and badge responses disagree on badge_level.")
    if project["tiered_percentage"] != badge["tiered_percentage"]:
        raise AuditError("Project and badge responses disagree on tiered_percentage.")
    if project["updated_at"] != badge["updated_at"]:
        raise AuditError("Project and badge responses disagree on updated_at.")


def is_non_osps_criterion_field(field: str) -> bool:
    """Return whether a field is an in-scope questionnaire status or justification."""
    return not field.startswith("OSPS-") and field.endswith(_CRITERION_SUFFIXES)


def is_unknown_status(value: JsonValue) -> bool:
    """Return whether a status value explicitly represents unknown evidence."""
    return value is None or (isinstance(value, str) and value.strip().casefold() in {"?", "unknown"})


def validate_proposal_fields(project: JsonObject, proposal: JsonObject) -> None:
    """Validate proposed criterion fields against the live questionnaire schema."""
    for field, value in project.items():
        if field.endswith("_status") and not field.startswith("OSPS-") and value not in _CRITERION_STATUSES:
            raise AuditError(f"Project response field {field!r} has unsupported status {value!r}.")

    proposed_criteria = {
        field.removesuffix("_status")
        for field in proposal
        if field.endswith("_status") and not field.startswith("OSPS-")
    }
    if proposed_criteria != _CRITERIA:
        missing = sorted(_CRITERIA - proposed_criteria)
        unexpected = sorted(proposed_criteria - _CRITERIA)
        raise AuditError(
            "Repository proposal criterion inventory does not match the canonical legacy metal criteria "
            f"(missing={missing}, unexpected={unexpected})."
        )

    for field, value in proposal.items():
        if not is_non_osps_criterion_field(field):
            continue

        criterion, suffix = field.rsplit("_", 1)
        if criterion not in _CRITERIA:
            raise AuditError(f"Repository proposal field {field!r} is not a legacy metal criterion field.")
        live_status_field = f"{criterion}_status"
        if live_status_field not in project:
            raise AuditError(f"Project response is missing declared criterion field {live_status_field!r}.")
        if suffix == "status" and value not in _CRITERION_STATUSES:
            raise AuditError(f"Repository proposal field {field!r} has unsupported status {value!r}.")


def compare_proposals(project: JsonObject, proposal: JsonObject) -> list[JsonObject]:
    """Return stable, field-level differences between live and proposed criteria."""
    fields = sorted(field for field in proposal if is_non_osps_criterion_field(field))
    differences: list[JsonObject] = []

    for field in fields:
        live_value = project.get(field)
        repository_value = proposal.get(field)
        if live_value != repository_value:
            differences.append(
                {
                    "criterion": field.rsplit("_", 1)[0],
                    "field": field,
                    "live": live_value,
                    "repository_proposal": repository_value,
                }
            )

    return differences


def unresolved_statuses(project: JsonObject, proposal: JsonObject) -> list[JsonObject]:
    """Return proposed status fields that are unknown or missing on either side."""
    return [
        {
            "field": field,
            "live": project.get(field),
            "repository_proposal": proposal[field],
        }
        for field in sorted(proposal)
        if field.endswith("_status")
        and not field.startswith("OSPS-")
        and (is_unknown_status(project.get(field)) or is_unknown_status(proposal[field]))
    ]


def build_report(project: JsonObject, proposal: JsonObject) -> JsonObject:
    """Build the deterministic audit payload."""
    return {
        "live_badge": {
            "gold_percentage": project["badge_percentage_2"],
            "level": project["badge_level"],
            "passing_percentage": project["badge_percentage_0"],
            "silver_percentage": project["badge_percentage_1"],
            "tiered_percentage": project["tiered_percentage"],
            "updated_at": project["updated_at"],
        },
        "project": {
            "id": project["id"],
            "name": project["name"],
            "repo_url": project["repo_url"],
        },
        "repository_proposal_differences": compare_proposals(project, proposal),
        "unresolved_non_osps_status_fields": unresolved_statuses(project, proposal),
    }


def main() -> int:
    """Collect and compare live and repository badge evidence."""
    try:
        script_path = Path(__file__).resolve()
        repository_root = find_repository_root(script_path)
        project = fetch_json(_PROJECT_URL)
        badge = fetch_json(_BADGE_URL)
        validate_live_data(project, badge)
        proposal = read_repository_proposal(repository_root)
        validate_proposal_fields(project, proposal)
        report = build_report(project, proposal)
    except AuditError as error:
        print(f"audit_badge.py: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"audit_badge.py: Unable to resolve script path: {error.strerror}.", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
