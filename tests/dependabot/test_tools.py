"""Hermetic tests for the scripts the Dependabot verification suite runs.

The docs e2e container runner is exercised through its Docker-free ``--compare-reports``
mode with synthetic Playwright and contrast-ledger reports, so the parity decision is
covered without containers, browsers, or a network.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_E2E_SCRIPT = REPO_ROOT / "docs" / "docusaurus" / "scripts" / "run-e2e-container.sh"

requires_bash_and_jq = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="requires bash and jq",
)

# A failing Playwright test: (project, spec file, spec title, error message).
Failure = tuple[str, str, str, str]


def _playwright_report(failures: list[Failure]) -> dict[str, object]:
    """Build a Playwright JSON report with nested suites, a passing test, and the given failures."""
    specs: list[dict[str, object]] = [
        {
            "file": "structure.spec.ts",
            "title": "renders the home page",
            "tests": [{"projectName": "chrome", "status": "expected", "results": [{"status": "passed"}]}],
        }
    ]
    for project, file, title, message in failures:
        specs.append(
            {
                "file": file,
                "title": title,
                "tests": [
                    {
                        "projectName": project,
                        "status": "unexpected",
                        "results": [
                            {"status": "failed", "error": {"message": "first attempt"}},
                            {"status": "failed", "error": {"message": message}},
                        ],
                    }
                ],
            }
        )
    return {
        "suites": [{"title": "root", "specs": [], "suites": [{"title": "nested", "specs": specs}]}],
        "errors": [],
        "stats": {"expected": 1, "unexpected": len(failures), "flaky": 0, "skipped": 0},
    }


def _ledger(assessments: list[tuple[str, str]]) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "assessments": [
            {"status": status, "signature": signature, "detail": "synthetic"} for status, signature in assessments
        ],
    }


def _write_reports(
    directory: Path,
    failures: list[Failure],
    assessments: list[tuple[str, str]] | None = None,
) -> Path:
    directory.mkdir(parents=True)
    (directory / "playwright-results.json").write_text(json.dumps(_playwright_report(failures)), encoding="utf-8")
    if assessments is not None:
        (directory / "contrast-ledger.json").write_text(json.dumps(_ledger(assessments)), encoding="utf-8")
    return directory


def _compare(head: Path, base: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(DOCS_E2E_SCRIPT), "--compare-reports", str(head), str(base)],
        capture_output=True,
        text=True,
        check=False,
    )


def _summary(blocking: int, route_findings: tuple[str, ...] = (), evaluated: str = "179 of 179") -> str:
    """Build the crawl's evidence-summary failure message, as site-crawl.spec.ts reports it."""
    lines = [
        "Error: Docusaurus route and contrast evidence summary",
        "=============================================",
        f"Route states evaluated: {evaluated} expected",
        f"Blocking findings: {blocking}",
        "",
    ]
    if route_findings:
        lines += ["Route findings", "--------------", *(f"  {finding}" for finding in route_findings), ""]
    lines += [
        "Contrast ledger findings",
        "------------------------",
        f"  [unresolved] sig-b color-contrast is unresolved ({blocking} nodes)",
        "",
        "expect(received).toEqual(expected) // deep equality",
        "",
        f"+ Received  + {blocking}",
    ]
    return "\n".join(lines)


_CONTRAST_FAILURE: Failure = (
    "chrome",
    "site-crawl.spec.ts",
    "rejects unresolved contrast signatures",
    _summary(12),
)

_NAVIGATION_FAILURE = """Error: expect(page).toHaveURL(expected) failed

Expected: predicate to succeed
Received: "http://127.0.0.1:3001/physical-ai-toolchain/{route}"
Timeout: 5000ms

Call log:
  - Expect "toHaveURL" with timeout 5000ms
    {attempts} \u00d7 unexpected value "http://127.0.0.1:3001/physical-ai-toolchain/{route}"

  {line} |   await expect(page).toHaveURL((url) => url.pathname === tierPath);
       |                      ^
    at /work/docs/docusaurus/e2e/navigation-search.spec.ts:{line}:22"""


def _navigation_failure(route: str, *, attempts: int = 9, line: int = 483) -> Failure:
    message = _NAVIGATION_FAILURE.format(route=route, attempts=attempts, line=line)
    return ("chrome", "navigation-search.spec.ts", "responsive navigation opens a tier", message)


@requires_bash_and_jq
def test_head_without_failures_passes(tmp_path: Path) -> None:
    head = _write_reports(tmp_path / "head", [], [("accepted", "sig-a")])
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE], [("unresolved", "sig-b")])

    result = _compare(head, base)

    assert result.returncode == 0, result.stderr
    assert "Parity" in result.stdout


@requires_bash_and_jq
def test_identical_failures_report_parity(tmp_path: Path) -> None:
    head = _write_reports(tmp_path / "head", [_CONTRAST_FAILURE], [("unresolved", "sig-b")])
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE], [("unresolved", "sig-b")])

    result = _compare(head, base)

    assert result.returncode == 0, result.stderr
    assert "Parity" in result.stdout


@requires_bash_and_jq
def test_evidence_summary_counts_do_not_break_parity(tmp_path: Path) -> None:
    head_failure = (*_CONTRAST_FAILURE[:3], _summary(14))
    head = _write_reports(tmp_path / "head", [head_failure])
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE])

    result = _compare(head, base)

    assert result.returncode == 0, result.stderr


@requires_bash_and_jq
def test_a_new_route_finding_in_the_evidence_summary_is_reported(tmp_path: Path) -> None:
    finding = "/docs/new (light/default): Error: page.goto: Timeout 15000ms exceeded"
    head = _write_reports(tmp_path / "head", [(*_CONTRAST_FAILURE[:3], _summary(13, (finding,)))])
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE])

    result = _compare(head, base)

    assert result.returncode == 1
    assert "finding|/docs/new (light/default): Error: page.goto: Timeout <duration> exceeded" in result.stderr


@requires_bash_and_jq
def test_incomplete_route_states_in_the_evidence_summary_are_reported(tmp_path: Path) -> None:
    head = _write_reports(tmp_path / "head", [(*_CONTRAST_FAILURE[:3], _summary(12, evaluated="178 of 179"))])
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE])

    result = _compare(head, base)

    assert result.returncode == 1
    assert "incomplete route states" in result.stderr


@requires_bash_and_jq
def test_the_same_assertion_with_a_different_received_value_is_reported(tmp_path: Path) -> None:
    head = _write_reports(tmp_path / "head", [_navigation_failure("")])
    base = _write_reports(tmp_path / "base", [_navigation_failure("docs/getting-started/")])

    result = _compare(head, base)

    assert result.returncode == 1
    assert 'Received: "http://127.0.0.1:<port>/physical-ai-toolchain/"' in result.stderr


@requires_bash_and_jq
def test_call_logs_code_frames_and_timings_do_not_break_parity(tmp_path: Path) -> None:
    head = _write_reports(tmp_path / "head", [_navigation_failure("", attempts=4, line=490)])
    base = _write_reports(tmp_path / "base", [_navigation_failure("", attempts=9, line=483)])

    result = _compare(head, base)

    assert result.returncode == 0, result.stderr


@requires_bash_and_jq
@pytest.mark.parametrize(
    ("base_stats", "expected"),
    [
        pytest.param(None, "base run has no usable test evidence", id="base-missing"),
        pytest.param({"expected": 0, "unexpected": 0, "flaky": 0, "skipped": 4}, "no test executed", id="base-empty"),
    ],
)
def test_parity_needs_executed_tests_on_both_refs(
    tmp_path: Path, base_stats: dict[str, int] | None, expected: str
) -> None:
    head = tmp_path / "head"
    head.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    if base_stats is not None:
        report = {"suites": [], "errors": [], "stats": base_stats}
        (base / "playwright-results.json").write_text(json.dumps(report), encoding="utf-8")

    result = _compare(head, base)

    # Matching absence on both refs used to cancel out and report parity.
    assert result.returncode == 1
    assert "head run has no usable test evidence" in result.stderr
    assert expected in result.stderr


@requires_bash_and_jq
def test_a_new_failing_test_is_reported(tmp_path: Path) -> None:
    new_failure = (
        "chrome",
        "static-server.spec.ts",
        "serves base assets",
        "TypeError: pathRegexp.match is not a function",
    )
    head = _write_reports(tmp_path / "head", [_CONTRAST_FAILURE, new_failure])
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE])

    result = _compare(head, base)

    assert result.returncode == 1
    assert "serves base assets" in result.stderr
    assert "rejects unresolved contrast signatures" not in result.stderr


@requires_bash_and_jq
def test_the_same_test_failing_differently_is_reported(tmp_path: Path) -> None:
    changed = (*_CONTRAST_FAILURE[:3], "TimeoutError: page.goto: Timeout exceeded")
    head = _write_reports(tmp_path / "head", [changed])
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE])

    result = _compare(head, base)

    assert result.returncode == 1
    assert "TimeoutError" in result.stderr


@requires_bash_and_jq
def test_a_new_contrast_signature_is_reported(tmp_path: Path) -> None:
    head = _write_reports(tmp_path / "head", [_CONTRAST_FAILURE], [("unresolved", "sig-b"), ("unresolved", "sig-new")])
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE], [("unresolved", "sig-b")])

    result = _compare(head, base)

    assert result.returncode == 1
    assert "contrast|unresolved|sig-new" in result.stderr
    assert "sig-b" not in result.stderr


@requires_bash_and_jq
def test_a_missing_head_report_is_a_failure(tmp_path: Path) -> None:
    head = tmp_path / "head"
    head.mkdir()
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE])

    result = _compare(head, base)

    assert result.returncode == 1
    assert "missing playwright-results.json" in result.stderr


@requires_bash_and_jq
def test_a_run_level_error_without_tests_is_a_new_failure(tmp_path: Path) -> None:
    head = tmp_path / "head"
    head.mkdir()
    report = {
        "suites": [],
        "errors": [{"message": "Error: Process from config.webServer exited early.\nTypeError: pathRegexp.match"}],
        "stats": {"expected": 0, "unexpected": 0, "flaky": 0, "skipped": 0},
    }
    (head / "playwright-results.json").write_text(json.dumps(report), encoding="utf-8")
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE], [("unresolved", "sig-b")])

    result = _compare(head, base)

    assert result.returncode == 1
    assert "error|Error: Process from config.webServer exited early." in result.stderr
    assert "report|no tests ran" in result.stderr


@requires_bash_and_jq
def test_a_run_where_no_test_executed_is_a_new_failure(tmp_path: Path) -> None:
    head = tmp_path / "head"
    head.mkdir()
    report = {"suites": [], "errors": [], "stats": {"expected": 0, "unexpected": 0, "flaky": 0, "skipped": 12}}
    (head / "playwright-results.json").write_text(json.dumps(report), encoding="utf-8")
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE])

    result = _compare(head, base)

    assert result.returncode == 1
    assert "report|no tests ran" in result.stderr


@requires_bash_and_jq
def test_an_unreadable_report_fails_instead_of_reading_as_no_failures(tmp_path: Path) -> None:
    head = _write_reports(tmp_path / "head", [_CONTRAST_FAILURE])
    (head / "playwright-results.json").write_text("{not json", encoding="utf-8")
    base = _write_reports(tmp_path / "base", [_CONTRAST_FAILURE])

    result = _compare(head, base)

    assert result.returncode == 1
    assert "Cannot read the head reports" in result.stderr


@requires_bash_and_jq
def test_config_preview_needs_no_docker(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(DOCS_E2E_SCRIPT), "--config-preview", "--compare-base", "HEAD", "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr
    assert "Compare Base" in result.stdout


COMPARE_PLANS_SCRIPT = REPO_ROOT / "infrastructure" / "terraform" / "scripts" / "compare-plans.sh"

requires_terraform_tooling = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("bash", "git", "jq", "tar")),
    reason="requires bash, git, jq, and tar",
)

# A fake terraform that records each invocation's working directory and arguments,
# mimics init and plan side effects inside that directory, and prints the plan JSON
# the test wrote for the base or head copy.
_FAKE_TERRAFORM = r"""#!/usr/bin/env bash
set -o errexit -o nounset
dir="$PWD"
args=()
for arg in "$@"; do
  case "$arg" in
    -chdir=*) dir="${arg#-chdir=}" ;;
    *) args+=("$arg") ;;
  esac
done
printf '%s\t%s\n' "$dir" "${args[*]}" >> "$FAKE_TERRAFORM_LOG"
case "${args[0]}" in
  init)
    mkdir -p "$dir/.terraform"
    echo copy-lock > "$dir/.terraform.lock.hcl"
    ;;
  plan)
    if [[ -n "${FAKE_TERRAFORM_FAIL_PLAN:-}" ]]; then
      echo "Error: invalid value $FAKE_TERRAFORM_FAIL_PLAN"
      exit 1
    fi
    for arg in "${args[@]}"; do
      if [[ "$arg" == -out=* ]]; then echo plan > "${arg#-out=}"; fi
    done
    echo "Plan: 1 to add, 0 to change, 0 to destroy."
    sleep "${FAKE_TERRAFORM_SLEEP:-0}"
    exit 2
    ;;
  show)
    label=base
    if [[ "$dir" == */head/* ]]; then label=head; fi
    cat "$FAKE_PLAN_DIR/plan-$label.json"
    ;;
esac
"""


def _plan_json(extra_address: str = "", *, sku: str = "y") -> str:
    changes: list[dict[str, object]] = [
        {"address": "azurerm_resource_group.main", "change": {"actions": ["no-op"], "before": {}, "after": {}}},
        {
            "address": "module.vpn.azurerm_virtual_network_gateway.main",
            "change": {
                "actions": ["update"],
                "before": {"sku": "x", "name": "secret-value"},
                "after": {"sku": sku, "name": "secret-value"},
            },
        },
    ]
    if extra_address:
        changes.append(
            {"address": extra_address, "change": {"actions": ["update"], "before": {"a": 1}, "after": {"a": 2}}}
        )
    return json.dumps({"resource_changes": changes, "output_changes": {}})


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _terraform_repo(tmp_path: Path, *, with_state: bool = True) -> Path:
    """Create a repository with base and head commits plus untracked local stack files."""
    repo = tmp_path / "repo"
    stack = repo / "infrastructure" / "terraform"
    stack.mkdir(parents=True)
    (repo / "scripts" / "lib").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "scripts" / "lib" / "common.sh", repo / "scripts" / "lib" / "common.sh")
    (stack / "main.tf").write_text('resource "azurerm_resource_group" "main" {}\n', encoding="utf-8")
    (stack / "versions.tf").write_text("# azurerm < 5.4.1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")
    _git(repo, "tag", "base")
    (stack / "versions.tf").write_text("# azurerm < 5.7.1\n", encoding="utf-8")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qam", "head")

    if with_state:
        (stack / "terraform.tfstate").write_text('{"version": 4}\n', encoding="utf-8")
    (stack / "terraform.tfvars").write_text('environment = "dev"\n', encoding="utf-8")
    (stack / ".terraform.lock.hcl").write_text("user-lock\n", encoding="utf-8")
    (stack / ".terraform" / "providers").mkdir(parents=True)
    (stack / ".terraform" / "providers" / "marker").write_text("user-provider\n", encoding="utf-8")
    return repo


def _local_stack_files(repo: Path) -> dict[str, str]:
    stack = repo / "infrastructure" / "terraform"
    return {
        str(path.relative_to(stack)): path.read_text(encoding="utf-8")
        for path in sorted(stack.rglob("*"))
        if path.is_file()
        and (path.name.startswith("terraform.") or ".terraform" in path.parts or path.suffix == ".hcl")
    }


def _compare_command(
    tmp_path: Path, *, head_extra: str = "", sleep_seconds: int = 0, base_sku: str = "y", head_sku: str = "y"
) -> tuple[list[str], dict[str, str]]:
    """Install the fake terraform and plan outputs, then return the compare command and environment."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "terraform"
    fake.write_text(_FAKE_TERRAFORM, encoding="utf-8")
    fake.chmod(0o755)
    (tmp_path / "plan-base.json").write_text(_plan_json(sku=base_sku), encoding="utf-8")
    (tmp_path / "plan-head.json").write_text(_plan_json(head_extra, sku=head_sku), encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_TERRAFORM_LOG": str(tmp_path / "terraform.log"),
        "FAKE_PLAN_DIR": str(tmp_path),
        "TMPDIR": str(scratch),
        "NO_COLOR": "1",
        "FAKE_TERRAFORM_SLEEP": str(sleep_seconds),
    }
    command = [
        "bash",
        str(COMPARE_PLANS_SCRIPT),
        "--base",
        "base",
        "--head",
        "HEAD",
        "--stack",
        "root",
        "--output-dir",
        str(tmp_path / "out"),
    ]
    return command, env


def _run_compare(tmp_path: Path, repo: Path, *, head_extra: str = "") -> subprocess.CompletedProcess[str]:
    command, env = _compare_command(tmp_path, head_extra=head_extra)
    return subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, check=False)


@requires_terraform_tooling
def test_compare_plans_runs_only_in_archived_copies(tmp_path: Path) -> None:
    repo = _terraform_repo(tmp_path)
    before = _local_stack_files(repo)

    result = _run_compare(tmp_path, repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _local_stack_files(repo) == before
    assert list((tmp_path / "scratch").iterdir()) == []

    invocations = [line.split("\t") for line in (tmp_path / "terraform.log").read_text(encoding="utf-8").splitlines()]
    assert [args.split()[0] for _, args in invocations] == ["init", "plan", "show", "init", "plan", "show"]
    assert all(directory.startswith(str(tmp_path / "scratch")) for directory, _ in invocations)
    assert all("-backend=false" in args for _, args in invocations if args.startswith("init"))
    state_path = repo / "infrastructure" / "terraform" / "terraform.tfstate"
    for _, args in invocations:
        if args.startswith("plan"):
            assert "-lock=false" in args
            assert f"-state={state_path.resolve()}" in args
    assert not any(args.startswith("apply") for _, args in invocations)


@requires_terraform_tooling
def test_compare_plans_reports_keys_but_never_values(tmp_path: Path) -> None:
    repo = _terraform_repo(tmp_path)

    result = _run_compare(tmp_path, repo)

    summary = json.loads((tmp_path / "out" / "root" / "head-summary.json").read_text(encoding="utf-8"))
    assert summary["changes"] == ["module.vpn.azurerm_virtual_network_gateway.main update [sku]"]
    assert "secret-value" not in result.stdout + result.stderr
    assert "secret-value" not in (tmp_path / "out" / "root" / "head-summary.json").read_text(encoding="utf-8")


@requires_terraform_tooling
def test_compare_plans_fails_when_the_head_change_set_differs(tmp_path: Path) -> None:
    repo = _terraform_repo(tmp_path)

    result = _run_compare(tmp_path, repo, head_extra="azurerm_storage_account.extra")

    assert result.returncode == 1
    assert "+azurerm_storage_account.extra update [a]" in result.stdout


def _retained_text(directory: Path) -> str:
    return "".join(path.read_text(encoding="utf-8") for path in directory.rglob("*") if path.is_file())


@requires_terraform_tooling
def test_compare_plans_detects_value_changes_without_showing_values(tmp_path: Path) -> None:
    repo = _terraform_repo(tmp_path)
    command, env = _compare_command(tmp_path, base_sku="VpnGw2", head_sku="VpnGw5")

    result = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, check=False)

    # The value-free summaries match ("update [sku]"), so only the private comparison sees the difference.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "resource module.vpn.azurerm_virtual_network_gateway.main" in result.stdout
    for value in ("VpnGw2", "VpnGw5", "secret-value"):
        assert value not in result.stdout + result.stderr
        assert value not in _retained_text(tmp_path / "out")
    assert list((tmp_path / "scratch").iterdir()) == []


@requires_terraform_tooling
def test_compare_plans_keeps_failure_diagnostics_private(tmp_path: Path) -> None:
    repo = _terraform_repo(tmp_path)
    command, env = _compare_command(tmp_path)
    env["FAKE_TERRAFORM_FAIL_PLAN"] = "private-marker-value"

    result = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, check=False)

    assert result.returncode == 1
    assert "private-marker-value" not in result.stdout + result.stderr
    diagnostics = tmp_path / "out" / "root" / "diagnostics"
    kept = diagnostics / "base-plan.log"
    assert str(kept) in result.stderr
    assert "private-marker-value" in kept.read_text(encoding="utf-8")
    assert kept.stat().st_mode & 0o777 == 0o600
    assert diagnostics.stat().st_mode & 0o777 == 0o700


@requires_terraform_tooling
def test_compare_plans_is_not_run_without_state(tmp_path: Path) -> None:
    repo = _terraform_repo(tmp_path, with_state=False)

    result = _run_compare(tmp_path, repo)

    assert result.returncode == 3
    assert "not run" in result.stderr
    assert not (tmp_path / "terraform.log").exists()


@requires_terraform_tooling
def test_compare_plans_cleans_up_when_its_process_group_is_terminated(tmp_path: Path) -> None:
    repo = _terraform_repo(tmp_path)
    command, env = _compare_command(tmp_path, sleep_seconds=30)
    log = tmp_path / "terraform.log"
    process = subprocess.Popen(
        command, cwd=repo, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not (log.exists() and "\tplan " in log.read_text(encoding="utf-8")):
            time.sleep(0.1)
        time.sleep(0.3)
        assert any((tmp_path / "scratch").iterdir()), "the run should hold a temporary directory mid-plan"

        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=20)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)

    assert process.returncode == 143
    assert list((tmp_path / "scratch").iterdir()) == []
