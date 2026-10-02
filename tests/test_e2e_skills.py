from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INFRASTRUCTURE_SKILL = REPO_ROOT / ".github" / "skills" / "e2e-infrastructure"
TESTS_SKILL = REPO_ROOT / ".github" / "skills" / "e2e-tests"
DRIVER_SCRIPTS = (
    INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh",
    TESTS_SKILL / "scripts" / "run-e2e-tests.sh",
)
SCRIPTS = (
    *DRIVER_SCRIPTS,
    INFRASTRUCTURE_SKILL / "scripts" / "lib" / "operation-state.sh",
    INFRASTRUCTURE_SKILL / "scripts" / "lib" / "terraform-operations.sh",
    TESTS_SKILL / "scripts" / "lib" / "handle-state.sh",
)
INFRASTRUCTURE_TEMPLATE = INFRASTRUCTURE_SKILL / "templates" / "terraform.tfvars.example"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def _create_handle(tmp_path: Path, statuses: dict[str, str]) -> Path:
    handle = tmp_path / "handle"
    handle.mkdir()
    handle.chmod(0o700)
    (handle / "tests.txt").write_text("\n".join(statuses) + "\n", encoding="utf-8")
    for test_name, status in statuses.items():
        attempt = handle / test_name / "attempt-1"
        attempt.mkdir(parents=True)
        (handle / test_name / "status").write_text(f"{status}\n", encoding="utf-8")
        (handle / test_name / "latest-attempt").write_text("1\n", encoding="utf-8")
        (handle / test_name / "pid").write_text("12345\n", encoding="utf-8")
        (attempt / "command.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        (attempt / "output.log").write_text("[e2e] first\n[e2e] latest\n", encoding="utf-8")
    return handle


def _create_runner_attempt(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    repo_root = tmp_path / "repo"
    (repo_root / "tests" / "e2e").mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python"
    _write_executable(
        fake_python,
        """
        #!/usr/bin/env bash
        if [[ "$*" == *"--collect-only"* ]]; then
            printf '%s\n' 'tests/e2e/test_e2e_sample.py::test_case'
            exit 0
        fi
        printf '%s\n' "${FAKE_TEST_OUTPUT:-}"
        exit "${FAKE_TEST_EXIT_CODE:-0}"
        """,
    )
    _write_executable(
        fake_bin / "timeout",
        """
        #!/usr/bin/env bash
        shift
        exec "$@"
        """,
    )

    handle = tmp_path / "attempt-handle"
    handle.mkdir()
    handle.chmod(0o700)
    (handle / "tests.txt").write_text("test_e2e_sample\n", encoding="utf-8")
    (handle / "commands.tsv").write_text(
        "TIMESTAMP\tTEST\tATTEMPT\tPID\tCOMMAND_FILE\tOUTPUT_LOG\n",
        encoding="utf-8",
    )
    kubeconfig = handle / "kubeconfig"
    kubeconfig.touch()
    config = {
        "schemaVersion": 1,
        "repositoryRoot": str(repo_root),
        "subscriptionId": "subscription",
        "resourceGroup": "resource-group",
        "aksCluster": "cluster",
        "azuremlWorkspace": "workspace",
        "azuremlCompute": "gpu-cluster",
        "storageAccount": "storage",
        "vlaStorageAccount": "storage",
        "kubeconfig": str(kubeconfig),
        "xdgConfigHome": str(handle / "xdg-config"),
        "osmoGatewayPort": 9000,
        "watchdogSeconds": 60,
        "e2ePython": str(fake_python),
    }
    config_path = handle / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    config_path.chmod(0o600)

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    return handle, repo_root, environment


def _create_infrastructure_fixture(
    tmp_path: Path,
    *,
    state: str = "",
    terraform_output: str = "{}",
    existing_tfvars: bool = False,
    first_instance_unavailable: bool = False,
) -> tuple[Path, dict[str, str]]:
    repo_root = tmp_path / "repo"
    (repo_root / ".git").mkdir(parents=True)
    terraform_dir = repo_root / "infrastructure" / "terraform"
    prerequisites = terraform_dir / "prerequisites"
    prerequisites.mkdir(parents=True)
    (repo_root / "infrastructure" / "setup" / "generated").mkdir(parents=True)
    (prerequisites / "az-sub-init.sh").write_text(
        'export ARM_SUBSCRIPTION_ID="subscription"\n',
        encoding="utf-8",
    )
    if existing_tfvars:
        shutil.copyfile(INFRASTRUCTURE_TEMPLATE, terraform_dir / "terraform.tfvars")

    fake_bin = tmp_path / "infra-bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "terraform",
        """
        #!/usr/bin/env bash
        printf 'terraform %s\n' "$*" >>"${FAKE_COMMAND_LOG:?}"
        [[ "$1" == -chdir=* ]] && shift
        command_name="${1:-}"
        shift || true
        case "$command_name" in
            init) exit 0 ;;
            state)
                [[ "${1:-}" == "list" ]] || exit 2
                [[ -f "${FAKE_DESTROYED_MARKER:?}" ]] || printf '%s' "${FAKE_TERRAFORM_STATE:-}"
                ;;
            show) printf '%s\n' '{"values":{}}' ;;
            plan)
                count=$(cat "${FAKE_PLAN_COUNT:?}" 2>/dev/null || printf '0')
                count=$((count + 1))
                printf '%s' "$count" >"${FAKE_PLAN_COUNT}"
                [[ "$count" -gt "${FAKE_PLAN_FAILURES:-0}" ]]
                ;;
            apply)
                count=$(cat "${FAKE_APPLY_COUNT:?}" 2>/dev/null || printf '0')
                count=$((count + 1))
                printf '%s' "$count" >"${FAKE_APPLY_COUNT}"
                [[ "$count" -gt "${FAKE_APPLY_FAILURES:-0}" ]]
                ;;
            destroy)
                count=$(cat "${FAKE_DESTROY_COUNT:?}" 2>/dev/null || printf '0')
                count=$((count + 1))
                printf '%s' "$count" >"${FAKE_DESTROY_COUNT}"
                if [[ "$count" -le "${FAKE_DESTROY_FAILURES:-0}" ]]; then
                    exit 1
                fi
                touch "${FAKE_DESTROYED_MARKER}"
                ;;
            console)
                cat >/dev/null
                printf '%s\n' '{"Purpose":"e2e-testing"}'
                ;;
            output)
                printf '%s\n' "${FAKE_TERRAFORM_OUTPUT}"
                ;;
            *) exit 0 ;;
        esac
        """,
    )
    _write_executable(
        fake_bin / "az",
        """
        #!/usr/bin/env bash
        command_line="$*"
        printf 'az %s\n' "$command_line" >>"${FAKE_COMMAND_LOG:?}"
        if [[ -n "${FAKE_AZ_FAILURE_PATTERN:-}" &&
              "$command_line" == *"${FAKE_AZ_FAILURE_PATTERN}"* ]]; then
            exit 9
        fi
        case "$command_line" in
            "account get-access-token"*) exit 0 ;;
            "group show"*) exit 0 ;;
            "group delete"*)
                touch "${FAKE_GROUP_DELETED_MARKER:?}"
                exit 0
                ;;
            "group exists"*)
                if [[ -f "${FAKE_GROUP_DELETED_MARKER:?}" ]]; then
                    printf 'false\n'
                elif [[ "${FAKE_GROUP_EXISTS:-false}" == "true" ]]; then
                    printf 'true\n'
                elif [[ "${FAKE_FIRST_INSTANCE_UNAVAILABLE:-false}" == "true" &&
                      "$command_line" == *"-001"* ]]; then
                    printf 'true\n'
                else
                    printf 'false\n'
                fi
                ;;
            "rest "*) printf 'true\n' ;;
            "storage account check-name "*) printf 'true\n' ;;
            "acr check-name "*) printf 'true\n' ;;
            *) exit 0 ;;
        esac
        """,
    )
    for command_name in ("envsubst", "helm", "kubectl", "osmo", "sleep"):
        _write_executable(fake_bin / command_name, "#!/usr/bin/env bash\nexit 0\n")

    command_log = tmp_path / "commands.log"
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_TERRAFORM_STATE"] = state
    environment["FAKE_TERRAFORM_OUTPUT"] = terraform_output
    environment["FAKE_FIRST_INSTANCE_UNAVAILABLE"] = str(first_instance_unavailable).lower()
    environment["FAKE_COMMAND_LOG"] = str(command_log)
    environment["FAKE_DESTROYED_MARKER"] = str(tmp_path / "destroyed")
    environment["FAKE_GROUP_DELETED_MARKER"] = str(tmp_path / "group-deleted")
    environment["FAKE_PLAN_COUNT"] = str(tmp_path / "plan-count")
    environment["FAKE_APPLY_COUNT"] = str(tmp_path / "apply-count")
    environment["FAKE_DESTROY_COUNT"] = str(tmp_path / "destroy-count")
    return repo_root, environment


@pytest.mark.parametrize("script", SCRIPTS)
def test_e2e_skill_script_has_valid_bash_syntax(script: Path) -> None:
    subprocess.run(["bash", "-n", str(script)], check=True, cwd=REPO_ROOT)


@pytest.mark.parametrize("script", DRIVER_SCRIPTS)
def test_e2e_skill_script_exposes_help(script: Path) -> None:
    result = subprocess.run(
        ["bash", str(script), "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "Usage:" in result.stdout


@pytest.mark.parametrize("script", DRIVER_SCRIPTS)
def test_e2e_skill_script_is_executable(script: Path) -> None:
    assert os.access(script, os.X_OK)


def test_e2e_infrastructure_template_is_tracked_as_an_example() -> None:
    assert INFRASTRUCTURE_TEMPLATE.is_file()
    assert not (INFRASTRUCTURE_SKILL / "templates" / "terraform.tfvars").exists()


def test_e2e_infrastructure_rejects_invalid_automation_principal() -> None:
    script = INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh"

    result = subprocess.run(
        [
            "bash",
            str(script),
            "deploy",
            "--automation-principal-object-id",
            "not-a-uuid",
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "--automation-principal-object-id must be a UUID" in result.stderr


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["deploy", "--max-attempts", "0"], "--max-attempts must be a positive integer"),
        (["deploy", "--delete-after-hours", "0"], "--delete-after-hours must be a positive integer"),
        (["deploy", "--delete-after-tag", ""], "--delete-after-tag must not be empty"),
        (["deploy", "--max-attempts"], "--max-attempts requires a value"),
    ],
)
def test_e2e_infrastructure_rejects_invalid_options(arguments: list[str], message: str) -> None:
    script = INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh"

    result = subprocess.run(
        ["bash", str(script), *arguments],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert message in result.stderr


def test_e2e_infrastructure_preview_selects_first_available_instance(tmp_path: Path) -> None:
    script = INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh"
    repo_root, environment = _create_infrastructure_fixture(tmp_path)

    result = subprocess.run(
        ["bash", str(script), "deploy", "--repo-root", str(repo_root), "--config-preview"],
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Instance: 001" in result.stdout
    assert not (repo_root / "infrastructure" / "terraform" / "terraform.tfvars").exists()


def test_e2e_infrastructure_preview_skips_unavailable_instance(tmp_path: Path) -> None:
    script = INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh"
    repo_root, environment = _create_infrastructure_fixture(tmp_path, first_instance_unavailable=True)

    result = subprocess.run(
        ["bash", str(script), "deploy", "--repo-root", str(repo_root), "--config-preview"],
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Instance: 002" in result.stdout


def test_e2e_infrastructure_azure_api_failure_fails_closed(tmp_path: Path) -> None:
    script = INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh"
    repo_root, environment = _create_infrastructure_fixture(tmp_path)
    environment["FAKE_AZ_FAILURE_PATTERN"] = "group exists"

    result = subprocess.run(
        ["bash", str(script), "deploy", "--repo-root", str(repo_root), "--config-preview"],
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Resource-group availability check failed" in result.stderr
    assert not (repo_root / "infrastructure" / "terraform" / "terraform.tfvars").exists()


def test_e2e_infrastructure_rejects_resume_state_for_another_instance(tmp_path: Path) -> None:
    script = INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh"
    terraform_output = '{"resource_group":{"value":{"name":"rg-e2e-dev-999"}}}'
    repo_root, environment = _create_infrastructure_fixture(
        tmp_path,
        state="module.platform.resource\n",
        terraform_output=terraform_output,
        existing_tfvars=True,
    )

    result = subprocess.run(
        ["bash", str(script), "deploy", "--repo-root", str(repo_root), "--config-preview"],
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Terraform state belongs to 'rg-e2e-dev-999'" in result.stderr


def test_e2e_infrastructure_refuses_undeploy_without_state_or_context(tmp_path: Path) -> None:
    script = INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh"
    repo_root, environment = _create_infrastructure_fixture(tmp_path, existing_tfvars=True)

    result = subprocess.run(
        ["bash", str(script), "undeploy", "--repo-root", str(repo_root), "--config-preview"],
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "refusing to infer an environment to destroy" in result.stderr


def _undeploy_output(instance: str = "000") -> str:
    resource_group = f"rg-e2e-dev-{instance}"
    return json.dumps(
        {
            "resource_group": {
                "value": {
                    "name": resource_group,
                    "id": f"/subscriptions/subscription/resourceGroups/{resource_group}",
                    "location": "eastus",
                }
            },
            "aks_cluster": {
                "value": {
                    "name": f"aks-e2e-dev-{instance}",
                    "id": (
                        "/subscriptions/subscription/resourceGroups/"
                        f"{resource_group}/providers/Microsoft.ContainerService/"
                        f"managedClusters/aks-e2e-dev-{instance}"
                    ),
                }
            },
            "key_vault_name": {"value": ""},
        }
    )


@pytest.mark.parametrize("confirmation", [None, "rg-e2e-dev-999"])
def test_e2e_infrastructure_requires_exact_undeploy_confirmation(
    tmp_path: Path,
    confirmation: str | None,
) -> None:
    script = INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh"
    repo_root, environment = _create_infrastructure_fixture(
        tmp_path,
        state="module.platform.resource\n",
        terraform_output=_undeploy_output(),
        existing_tfvars=True,
    )
    arguments = ["bash", str(script), "undeploy", "--repo-root", str(repo_root)]
    if confirmation is not None:
        arguments.extend(["--confirm-resource-group", confirmation])

    result = subprocess.run(
        arguments,
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Undeploy requires --confirm-resource-group 'rg-e2e-dev-000'" in result.stderr
    command_log = Path(environment["FAKE_COMMAND_LOG"]).read_text(encoding="utf-8")
    assert "terraform " not in "\n".join(line for line in command_log.splitlines() if " destroy " in f" {line} ")
    assert "az group delete" not in command_log


def test_e2e_infrastructure_retries_destroy_and_targets_confirmed_group(tmp_path: Path) -> None:
    script = INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh"
    repo_root, environment = _create_infrastructure_fixture(
        tmp_path,
        state="module.platform.resource\n",
        terraform_output=_undeploy_output(),
        existing_tfvars=True,
    )
    environment["FAKE_GROUP_EXISTS"] = "true"
    environment["FAKE_DESTROY_FAILURES"] = "1"

    result = subprocess.run(
        [
            "bash",
            str(script),
            "undeploy",
            "--repo-root",
            str(repo_root),
            "--confirm-resource-group",
            "rg-e2e-dev-000",
            "--max-attempts",
            "2",
        ],
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    command_log = Path(environment["FAKE_COMMAND_LOG"]).read_text(encoding="utf-8")
    assert command_log.count(" destroy ") == 2
    assert "az group delete --name rg-e2e-dev-000 --yes --only-show-errors" in command_log
    summaries = list(
        (repo_root / "infrastructure" / "setup" / "generated" / "dev" / "e2e-operations").glob("*/summary.json")
    )
    assert len(summaries) == 1
    assert json.loads(summaries[0].read_text(encoding="utf-8"))["status"] == "succeeded"


def test_e2e_infrastructure_destroy_retry_exhaustion_fails_closed(tmp_path: Path) -> None:
    script = INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh"
    repo_root, environment = _create_infrastructure_fixture(
        tmp_path,
        state="module.platform.resource\n",
        terraform_output=_undeploy_output(),
        existing_tfvars=True,
    )
    environment["FAKE_GROUP_EXISTS"] = "true"
    environment["FAKE_DESTROY_FAILURES"] = "2"

    result = subprocess.run(
        [
            "bash",
            str(script),
            "undeploy",
            "--repo-root",
            str(repo_root),
            "--confirm-resource-group",
            "rg-e2e-dev-000",
            "--max-attempts",
            "2",
        ],
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Terraform destroy failed after 2 attempts" in result.stderr
    command_log = Path(environment["FAKE_COMMAND_LOG"]).read_text(encoding="utf-8")
    assert command_log.count(" destroy ") == 2
    assert "az group delete" not in command_log


@pytest.mark.parametrize("operation", ["plan", "apply", "destroy"])
def test_terraform_retry_helper_recovers_each_operation(
    tmp_path: Path,
    operation: str,
) -> None:
    counter = tmp_path / "attempts"
    reconcile_counter = tmp_path / "reconciles"
    result = subprocess.run(
        [
            "bash",
            "-c",
            """
            set -o errexit -o nounset -o pipefail
            source "$1/scripts/lib/common.sh"
            source "$1/.github/skills/e2e-infrastructure/scripts/lib/terraform-operations.sh"
            max_attempts=3
            counter="$2"
            reconcile_counter="$3"
            terraform_operation_attempt() {
                local count
                count=$(cat "$counter" 2>/dev/null || printf '0')
                count=$((count + 1))
                printf '%s' "$count" >"$counter"
                [[ "$count" -ge 2 ]]
            }
            reconcile_failed_managed_redis() {
                local count
                count=$(cat "$reconcile_counter" 2>/dev/null || printf '0')
                printf '%s' "$((count + 1))" >"$reconcile_counter"
            }
            record_operation_event() { :; }
            sleep() { :; }
            run_terraform_operation_with_retries "$4"
            """,
            "retry-test",
            str(REPO_ROOT),
            str(counter),
            str(reconcile_counter),
            operation,
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert counter.read_text(encoding="utf-8") == "2"
    expected_reconciles = "1" if operation == "apply" else None
    assert (
        reconcile_counter.read_text(encoding="utf-8") if reconcile_counter.exists() else None
    ) == expected_reconciles


@pytest.mark.parametrize("operation", ["plan", "apply", "destroy"])
def test_terraform_retry_helper_exhausts_each_operation(
    tmp_path: Path,
    operation: str,
) -> None:
    counter = tmp_path / "attempts"
    result = subprocess.run(
        [
            "bash",
            "-c",
            """
            set -o errexit -o nounset -o pipefail
            source "$1/scripts/lib/common.sh"
            source "$1/.github/skills/e2e-infrastructure/scripts/lib/terraform-operations.sh"
            max_attempts=2
            counter="$2"
            terraform_operation_attempt() {
                local count
                count=$(cat "$counter" 2>/dev/null || printf '0')
                printf '%s' "$((count + 1))" >"$counter"
                return 1
            }
            reconcile_failed_managed_redis() { :; }
            record_operation_event() { :; }
            sleep() { :; }
            run_terraform_operation_with_retries "$3"
            """,
            "retry-test",
            str(REPO_ROOT),
            str(counter),
            operation,
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert f"Terraform {operation} failed after 2 attempts" in result.stderr
    assert counter.read_text(encoding="utf-8") == "2"


def test_prevent_destroy_parser_returns_only_unique_diagnosed_addresses(tmp_path: Path) -> None:
    plan = tmp_path / "plan.jsonl"
    plan.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "diagnostic",
                        "diagnostic": {
                            "summary": "Instance cannot be destroyed",
                            "detail": (
                                "Resource module.platform.azurerm_resource.safe has lifecycle.prevent_destroy set"
                            ),
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "diagnostic",
                        "diagnostic": {
                            "summary": "Instance cannot be destroyed",
                            "detail": (
                                "Resource module.platform.azurerm_resource.safe has lifecycle.prevent_destroy set"
                            ),
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "diagnostic",
                        "diagnostic": {
                            "summary": "Unrelated error",
                            "detail": "Resource module.platform.azurerm_resource.other failed",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; extract_prevent_destroy_addresses "$2"',
            "parser-test",
            str(INFRASTRUCTURE_SKILL / "scripts" / "lib" / "terraform-operations.sh"),
            str(plan),
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "module.platform.azurerm_resource.safe"


@pytest.mark.parametrize(
    ("statuses", "overall"),
    [
        ({"test_e2e_first": "PASSED", "test_e2e_second": "PASSED"}, "PASSED"),
        ({"test_e2e_first": "PASSED", "test_e2e_second": "RUNNING"}, "RUNNING"),
        ({"test_e2e_first": "PASSED", "test_e2e_second": "FAILED"}, "NEEDS_ATTENTION"),
    ],
)
def test_e2e_runner_aggregates_handle_status(
    tmp_path: Path,
    statuses: dict[str, str],
    overall: str,
) -> None:
    script = TESTS_SKILL / "scripts" / "run-e2e-tests.sh"
    handle = _create_handle(tmp_path, statuses)

    result = subprocess.run(
        ["bash", str(script), "--status", str(handle)],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert f"OVERALL\t{overall}" in result.stdout
    assert "[e2e] latest" in result.stdout


def test_e2e_runner_rejects_invalid_handle_test_name(tmp_path: Path) -> None:
    script = TESTS_SKILL / "scripts" / "run-e2e-tests.sh"
    handle = tmp_path / "handle"
    handle.mkdir()
    handle.chmod(0o700)
    (handle / "tests.txt").write_text("../escape\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(script), "--status", str(handle)],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "invalid test name" in result.stderr


def test_e2e_runner_cleanup_rejects_active_attempt(tmp_path: Path) -> None:
    script = TESTS_SKILL / "scripts" / "run-e2e-tests.sh"
    handle = _create_handle(tmp_path, {"test_e2e_sample": "RUNNING"})

    result = subprocess.run(
        ["bash", str(script), "--cleanup", str(handle)],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Cannot clean up while test_e2e_sample is RUNNING" in result.stderr


def test_e2e_runner_cleanup_accepts_terminal_attempts(tmp_path: Path) -> None:
    script = TESTS_SKILL / "scripts" / "run-e2e-tests.sh"
    handle = _create_handle(tmp_path, {"test_e2e_sample": "PASSED"})

    result = subprocess.run(
        ["bash", str(script), "--cleanup", str(handle)],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert f"CLEANED\t{handle}" in result.stdout


def test_e2e_runner_resubmit_rejects_test_outside_handle(tmp_path: Path) -> None:
    script = TESTS_SKILL / "scripts" / "run-e2e-tests.sh"
    handle = _create_handle(tmp_path, {"test_e2e_selected": "FAILED"})
    config_path = handle / "config.json"
    config_path.write_text("{}\n", encoding="utf-8")
    config_path.chmod(0o600)

    result = subprocess.run(
        ["bash", str(script), "--resubmit", str(handle), "test_e2e_other"],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Test is not part of this E2E handle" in result.stderr


@pytest.mark.parametrize(
    ("exit_code", "output", "expected_status", "expected_return_code"),
    [
        (0, "1 passed", "PASSED", 0),
        (0, "1 skipped", "FAILED_SETUP_SKIP", 1),
        (124, "watchdog", "TRANSIENT_WATCHDOG", 1),
        (1, "SkuNotAvailable", "TRANSIENT", 1),
        (1, "assertion failed", "FAILED", 1),
    ],
)
def test_e2e_runner_classifies_attempt_results(
    tmp_path: Path,
    exit_code: int,
    output: str,
    expected_status: str,
    expected_return_code: int,
) -> None:
    script = TESTS_SKILL / "scripts" / "run-e2e-tests.sh"
    handle, _, environment = _create_runner_attempt(tmp_path)
    environment["FAKE_TEST_EXIT_CODE"] = str(exit_code)
    environment["FAKE_TEST_OUTPUT"] = output

    result = subprocess.run(
        ["bash", str(script), "--run-one", str(handle), "test_e2e_sample"],
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    attempt = handle / "test_e2e_sample" / "attempt-1"
    assert result.returncode == expected_return_code
    assert (attempt / "status").read_text(encoding="utf-8").strip() == expected_status
    assert (handle / "test_e2e_sample" / "status").read_text(encoding="utf-8").strip() == expected_status
    assert (attempt / "exit-code").read_text(encoding="utf-8").strip() == str(exit_code)
    assert (attempt / "command.sh").stat().st_mode & 0o077 == 0


def test_e2e_runner_command_preserves_isolation_without_persisting_hf_token(tmp_path: Path) -> None:
    script = TESTS_SKILL / "scripts" / "run-e2e-tests.sh"
    handle, _, environment = _create_runner_attempt(tmp_path)
    environment["HF_TOKEN"] = "sensitive-test-value"

    result = subprocess.run(
        ["bash", str(script), "--run-one", str(handle), "test_e2e_sample"],
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    command = (handle / "test_e2e_sample" / "attempt-1" / "command.sh").read_text(encoding="utf-8")
    assert result.returncode == 0
    assert f"export KUBECONFIG={shlex.quote(str(handle / 'kubeconfig'))}" in command
    assert f"export XDG_CONFIG_HOME={shlex.quote(str(handle / 'xdg-config'))}" in command
    assert "HF_TOKEN" not in command
    assert "sensitive-test-value" not in command


def test_e2e_runner_rejects_permissive_handle_directory(tmp_path: Path) -> None:
    script = TESTS_SKILL / "scripts" / "run-e2e-tests.sh"
    handle = _create_handle(tmp_path, {"test_e2e_sample": "PASSED"})
    handle.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script), "--status", str(handle)],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "permissions must be 700" in result.stderr


def test_e2e_runner_rejects_symlinked_config(tmp_path: Path) -> None:
    script = TESTS_SKILL / "scripts" / "run-e2e-tests.sh"
    handle, _, environment = _create_runner_attempt(tmp_path)
    config = handle / "config.json"
    target = tmp_path / "outside.json"
    target.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(0o600)
    config.unlink()
    config.symlink_to(target)

    result = subprocess.run(
        ["bash", str(script), "--run-one", str(handle), "test_e2e_sample"],
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "contains a symbolic link" in result.stderr


def test_e2e_runner_refuses_to_signal_reused_pid(tmp_path: Path) -> None:
    script = TESTS_SKILL / "scripts" / "run-e2e-tests.sh"
    handle = _create_handle(tmp_path, {"test_e2e_sample": "PASSED"})
    identity = handle / "osmo-gateway.json"
    identity.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "pid": os.getpid(),
                "role": "osmo-gateway",
                "command": "not-the-live-command",
                "started": "not-the-live-start-time",
            }
        ),
        encoding="utf-8",
    )
    identity.chmod(0o600)

    result = subprocess.run(
        ["bash", str(script), "--cleanup", str(handle)],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert os.getpid() > 0
    assert "Refusing to stop PID" in result.stderr


def test_e2e_runner_parallel_handles_keep_evidence_isolated(tmp_path: Path) -> None:
    script = TESTS_SKILL / "scripts" / "run-e2e-tests.sh"
    first, _, first_environment = _create_runner_attempt(tmp_path / "first")
    second, _, second_environment = _create_runner_attempt(tmp_path / "second")

    processes = [
        subprocess.Popen(
            ["bash", str(script), "--run-one", str(handle), "test_e2e_sample"],
            cwd=REPO_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for handle, environment in ((first, first_environment), (second, second_environment))
    ]
    results = [process.communicate(timeout=30) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    for handle in (first, second):
        events = (handle / "events.jsonl").read_text(encoding="utf-8")
        assert str(handle) not in events
        assert '"event":"attempt"' in events
        assert (handle / "test_e2e_sample" / "attempt-1" / "status").read_text(encoding="utf-8").strip() == "PASSED"


def test_e2e_skills_do_not_embed_local_paths_or_fixed_cloud_ids() -> None:
    prohibited = (
        "/Users/",
        ".agents/skills",
        "~/.local/copilot",
    )
    cloud_id = re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")

    for skill in (INFRASTRUCTURE_SKILL, TESTS_SKILL):
        for path in skill.rglob("*"):
            if path.is_file():
                content = path.read_text(encoding="utf-8")
                assert not any(value in content for value in prohibited), path
                assert cloud_id.search(content) is None, path
