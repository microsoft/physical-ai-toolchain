"""Hermetic tests for the Dependabot verification runner.

A fake executor stands in for every command, and temporary git repositories stand in
for the workspace, so routing, selection, preflight, container wrapping, snapshots,
result mapping, and summaries are tested without network, Docker, or Azure.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.dependabot import verify
from tests.dependabot.verify import (
    EXIT_FAILED,
    EXIT_INCOMPLETE,
    EXIT_PASSED,
    EXIT_USAGE,
    Check,
    CheckResult,
    CheckTimeoutError,
    ExecutionRequest,
    UsageError,
    default_executor,
    junit_outcome,
    load_manifest,
    overall_result,
    resolve_output_dir,
    route_paths,
    run_check,
    select_checks,
)

MANIFEST = load_manifest()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "infra" / "nested").mkdir(parents=True)
    (repo / "infra" / "nested" / "main.tf").write_text("committed\n", encoding="utf-8")
    (repo / "other.txt").write_text("not in the snapshot\n", encoding="utf-8")
    (repo / ".gitignore").write_text("logs/\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "init")
    return repo


class FakeExecutor:
    """Record execution requests and return scripted exit codes."""

    def __init__(self, codes: dict[str, int] | None = None, default: int = 0) -> None:
        self.requests: list[ExecutionRequest] = []
        self.codes = codes or {}
        self.default = default

    def __call__(self, request: ExecutionRequest) -> int:
        self.requests.append(request)
        return self.codes.get(request.argv[0], self.default)


def _check(**overrides: object) -> Check:
    values: dict[str, object] = {
        "id": "sample",
        "category": "sample",
        "tier": "cpu",
        "gpu": False,
        "optional": False,
        "description": "Sample check",
        "command": ("tool", "--flag"),
    }
    values.update(overrides)
    return Check(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("training/rl/uv.lock", ["rl"]),
        ("package-lock.json", ["tooling", "dataviewer"]),
        (".github/workflows/x.yml", ["tooling"]),
        ("infrastructure/terraform/modules/platform/versions.tf", ["infrastructure"]),
        ("docs/docusaurus/package.json", ["docs"]),
        ("training/smoke/uv.lock", ["gpu-smoke"]),
        ("workflows/azureml/osmo-proxy/uv.lock", ["workflows"]),
        ("evaluation/vlm_judge/uv.lock", ["evaluation"]),
    ],
)
def test_paths_route_to_their_categories(path: str, expected: list[str]) -> None:
    routed, unmapped = route_paths([path], MANIFEST)

    assert routed == expected
    assert unmapped == []


def test_unclaimed_paths_are_reported() -> None:
    routed, unmapped = route_paths(["docs/contributing/README.md", "training/rl/uv.lock"], MANIFEST)

    assert routed == ["rl"]
    assert unmapped == ["docs/contributing/README.md"]


def test_selection_adds_baseline_and_deduplicates_shared_checks() -> None:
    selected, _, categories = select_checks(
        MANIFEST, ["rl", "il"], tier="cpu", include_optional=False, include_baseline=True
    )
    ids = [check.id for check in selected]

    assert [category.id for category in categories] == ["baseline", "rl", "il"]
    assert ids.count("training-tests") == 1
    assert "uv-lock-consistency" in ids
    assert all(check.tier == "cpu" for check in selected)


def test_selection_skips_optional_checks_unless_requested() -> None:
    selected, skipped, _ = select_checks(MANIFEST, ["rl"], tier="cpu", include_optional=False, include_baseline=False)
    selected_all, skipped_none, _ = select_checks(
        MANIFEST, ["rl"], tier="cpu", include_optional=True, include_baseline=False
    )

    assert "rl-image-smoke" in {check.id for check in skipped}
    assert "rl-image-smoke" not in {check.id for check in selected}
    assert "rl-image-smoke" in {check.id for check in selected_all}
    assert skipped_none == []


def test_environment_selection_holds_only_environment_checks() -> None:
    selected, _, _ = select_checks(
        MANIFEST, ["gpu-smoke", "workflows"], tier="environment", include_optional=False, include_baseline=True
    )

    assert [check.id for check in selected] == ["aml-gpu-smoke", "aml-il-pipeline-register"]


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([("passed", False), ("skipped", True)], ("passed", EXIT_PASSED)),
        ([("passed", False), ("failed", True)], ("failed", EXIT_FAILED)),
        ([("passed", False), ("not-run", False)], ("incomplete", EXIT_INCOMPLETE)),
        ([("passed", False), ("not-run", True)], ("passed", EXIT_PASSED)),
        ([("failed", False), ("not-run", False)], ("failed", EXIT_FAILED)),
    ],
)
def test_overall_result_maps_statuses_to_exit_codes(
    statuses: list[tuple[str, bool]], expected: tuple[str, int]
) -> None:
    results = [
        CheckResult(f"c{index}", "x", "cpu", False, optional, status)
        for index, (status, optional) in enumerate(statuses)
    ]

    assert overall_result(results) == expected


def _junit(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "report.xml"
    path.write_text(f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>', encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        ('<testcase name="a"/>', "passed"),
        ('<testcase name="a"><failure message="boom"/></testcase>', "failed"),
        ('<testcase name="a"><skipped message="workspace unreachable"/></testcase>', "not-run"),
        ("", "failed"),
    ],
    ids=["passed", "failed", "skipped-is-not-run", "no-tests"],
)
def test_junit_outcome_treats_skips_as_not_run(tmp_path: Path, body: str, expected_status: str) -> None:
    status, reason = junit_outcome(_junit(tmp_path, body))

    assert status == expected_status
    if expected_status == "not-run":
        assert reason == "workspace unreachable"


def test_missing_junit_report_is_a_failure(tmp_path: Path) -> None:
    assert junit_outcome(tmp_path / "missing.xml")[0] == "failed"


def test_a_failure_and_teardown_error_count_as_one_failed_test(tmp_path: Path) -> None:
    body = (
        '<testcase classname="tests.e2e.test_x" name="test_x[a]">'
        '<failure message="AssertionError: job never started&#10;details"/></testcase>'
        '<testcase classname="tests.e2e.test_x" name="test_x[a]">'
        '<error message="failed on teardown with &quot;AssertionError: cleanup timed out&quot;"/></testcase>'
    )

    assert junit_outcome(_junit(tmp_path, body)) == ("failed", "1 test(s) failed: AssertionError: job never started")


def _run(check: Check, repo: Path, executor: FakeExecutor) -> CheckResult:
    return run_check(
        check,
        repo_root=repo,
        run_dir=repo / "logs" / "run",
        env={},
        values={"base_ref": "origin/main", "run_dir": str(repo / "logs" / "run")},
        executor=executor,
    )


@pytest.mark.parametrize(
    ("code", "status"),
    [(0, "passed"), (1, "failed"), (3, "not-run"), (4, "failed")],
)
def test_command_exit_codes_map_to_statuses(git_repo: Path, code: int, status: str) -> None:
    executor = FakeExecutor(default=code)

    result = _run(_check(not_run_exit_codes=(3,)), git_repo, executor)

    assert result.status == status
    assert result.log == "logs/run/sample.log"


def test_placeholders_are_substituted(git_repo: Path) -> None:
    executor = FakeExecutor()

    _run(_check(command=("tool", "--base", "{base_ref}", "--out", "{run_dir}/x")), git_repo, executor)

    assert executor.requests[0].argv == ("tool", "--base", "origin/main", "--out", f"{git_repo}/logs/run/x")


def test_container_checks_wrap_docker_off_linux_amd64(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify, "host_is_linux_amd64", lambda: False)
    executor = FakeExecutor()
    check = _check(
        command=("bash", "-c", "uv run pytest"),
        cwd="infra",
        container={"image": "example/uv:tag", "platform": "linux/amd64"},
        set_env={"FOO": "bar"},
    )

    _run(check, git_repo, executor)
    request = executor.requests[0]

    assert request.argv[:6] == ("docker", "run", "--rm", "--platform", "linux/amd64", "--entrypoint")
    assert request.argv[6] == "bash"
    assert request.argv[7:9] == ("-v", f"{git_repo}:/workspace")
    assert request.argv[9:11] == ("-w", "/workspace/infra")
    assert request.argv[11:13] == ("-e", "FOO")
    assert request.argv[13:] == ("example/uv:tag", "-c", "uv run pytest")
    assert request.env["FOO"] == "bar"


def test_container_checks_run_natively_on_linux_amd64(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify, "host_is_linux_amd64", lambda: True)
    executor = FakeExecutor()
    check = _check(cwd="infra", container={"image": "example/uv:tag", "platform": "linux/amd64"})

    _run(check, git_repo, executor)

    assert executor.requests[0].argv == ("tool", "--flag")
    assert executor.requests[0].cwd == git_repo / "infra"


def test_snapshot_checks_use_committed_files_and_clean_up(git_repo: Path) -> None:
    (git_repo / "infra" / "nested" / "main.tf").write_text("uncommitted edit\n", encoding="utf-8")
    seen: list[tuple[Path, str, bool]] = []

    def executor(request: ExecutionRequest) -> int:
        seen.append(
            (
                request.cwd,
                (request.cwd / "infra" / "nested" / "main.tf").read_text(encoding="utf-8"),
                (request.cwd / "other.txt").exists(),
            )
        )
        return 0

    result = run_check(
        _check(snapshot=("infra",)),
        repo_root=git_repo,
        run_dir=git_repo / "logs" / "run",
        env={},
        values={"base_ref": "HEAD", "run_dir": "x"},
        executor=executor,
    )

    snapshot_root, content, has_other = seen[0]
    assert result.status == "passed"
    assert snapshot_root != git_repo
    assert content == "committed\n"
    assert has_other is False
    assert not snapshot_root.exists()


def test_a_snapshot_is_its_own_repository_root(git_repo: Path) -> None:
    roots: list[bool] = []

    def executor(request: ExecutionRequest) -> int:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=request.cwd / "infra",
            capture_output=True,
            text=True,
            check=False,
        )
        roots.append(str(Path(top.stdout.strip()).resolve()) == str(request.cwd.resolve()))
        return top.returncode

    result = run_check(
        _check(snapshot=("infra",)),
        repo_root=git_repo,
        run_dir=git_repo / "logs" / "run",
        env={},
        values={"base_ref": "HEAD", "run_dir": "x"},
        executor=executor,
    )

    assert result.status == "passed"
    assert roots == [True]


def test_snapshot_is_removed_when_the_command_errors(git_repo: Path) -> None:
    roots: list[Path] = []

    def executor(request: ExecutionRequest) -> int:
        roots.append(request.cwd)
        raise OSError("tool crashed")

    result = run_check(
        _check(snapshot=("infra",)),
        repo_root=git_repo,
        run_dir=git_repo / "logs" / "run",
        env={},
        values={"base_ref": "HEAD", "run_dir": "x"},
        executor=executor,
    )

    assert result.status == "failed"
    assert result.reason == "tool crashed"
    assert not roots[0].exists()


def test_pytest_checks_read_junit_and_skip_means_not_run(git_repo: Path) -> None:
    def executor(request: ExecutionRequest) -> int:
        junit = Path(request.argv[-1].split("=", 1)[1])
        junit.parent.mkdir(parents=True, exist_ok=True)
        junit.write_text(
            '<testsuite><testcase name="t"><skipped message="AzureML workspace is unreachable"/>'
            "</testcase></testsuite>",
            encoding="utf-8",
        )
        return 0

    check = _check(tier="environment", gpu=True, command=(), pytest="tests/e2e/test_e2e_aml_x.py::test_x")
    result = run_check(
        check,
        repo_root=git_repo,
        run_dir=git_repo / "logs" / "run",
        env={},
        values={"base_ref": "HEAD", "run_dir": "x"},
        executor=executor,
    )

    assert result.status == "not-run"
    assert "unreachable" in (result.reason or "")


_PASSING_JUNIT = '<testsuite><testcase name="t"/></testsuite>'


def _run_pytest_check(git_repo: Path, executor: Callable[[ExecutionRequest], int]) -> CheckResult:
    check = _check(tier="environment", gpu=True, command=(), pytest="tests/e2e/test_e2e_aml_x.py::test_x")
    return run_check(
        check,
        repo_root=git_repo,
        run_dir=git_repo / "logs" / "run",
        env={},
        values={"base_ref": "HEAD", "run_dir": "x"},
        executor=executor,
    )


def _writes_passing_junit(code: int) -> Callable[[ExecutionRequest], int]:
    def executor(request: ExecutionRequest) -> int:
        junit = Path(request.argv[-1].split("=", 1)[1])
        junit.parent.mkdir(parents=True, exist_ok=True)
        junit.write_text(_PASSING_JUNIT, encoding="utf-8")
        return code

    return executor


def test_a_pytest_check_passes_with_a_fresh_report_and_exit_zero(git_repo: Path) -> None:
    result = _run_pytest_check(git_repo, _writes_passing_junit(0))

    assert result.status == "passed"


def test_a_stale_report_cannot_pass_a_failed_launch(git_repo: Path) -> None:
    stale = git_repo / "logs" / "run" / "sample.xml"
    stale.parent.mkdir(parents=True)
    stale.write_text(_PASSING_JUNIT, encoding="utf-8")

    result = _run_pytest_check(git_repo, lambda request: 1)

    assert result.status == "failed"
    assert result.reason == "pytest exited 1 without writing a junit report"
    assert not stale.exists()


def test_a_nonzero_pytest_exit_fails_a_passing_report(git_repo: Path) -> None:
    result = _run_pytest_check(git_repo, _writes_passing_junit(3))

    assert result.status == "failed"
    assert result.reason == "pytest exited 3"


@pytest.mark.parametrize(
    ("overrides", "expected_seconds"),
    [
        ({"timeout_minutes": 2}, 120),
        ({"tier": "environment", "command": (), "pytest": "tests/e2e/x.py::test_x", "timeout_minutes": 3}, 180),
    ],
    ids=["command", "pytest"],
)
def test_time_limits_reach_the_executor_and_a_timeout_fails_the_check(
    git_repo: Path, overrides: dict[str, object], expected_seconds: int
) -> None:
    requests: list[ExecutionRequest] = []

    def executor(request: ExecutionRequest) -> int:
        requests.append(request)
        raise CheckTimeoutError("timed out after 2 minutes")

    result = run_check(
        _check(**overrides),
        repo_root=git_repo,
        run_dir=git_repo / "logs" / "run",
        env={},
        values={"base_ref": "HEAD", "run_dir": "x"},
        executor=executor,
    )

    assert requests[0].timeout_seconds == expected_seconds
    assert result.status == "failed"
    assert result.reason == "timed out after 2 minutes"


def test_checks_without_a_time_limit_wait_indefinitely(git_repo: Path) -> None:
    executor = FakeExecutor()

    _run(_check(), git_repo, executor)

    assert executor.requests[0].timeout_seconds is None


def _wait_until_gone(pid: int, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _nested_scripts(tmp_path: Path, inner_body: str) -> tuple[str, ...]:
    inner = tmp_path / "inner.sh"
    inner.write_text(inner_body, encoding="utf-8")
    outer = tmp_path / "outer.sh"
    outer.write_text(f'bash "{inner}"\n', encoding="utf-8")
    return ("bash", str(outer))


def test_time_limit_interrupts_every_process_so_cleanup_runs(tmp_path: Path) -> None:
    marker = tmp_path / "cleaned"
    argv = _nested_scripts(tmp_path, "trap 'echo cleaned > \"$MARKER\"; exit 130' INT\nsleep 30\n")
    log_path = tmp_path / "check.log"
    request = ExecutionRequest(argv, tmp_path, {**os.environ, "MARKER": str(marker)}, log_path, timeout_seconds=1)

    started = time.monotonic()
    with pytest.raises(CheckTimeoutError, match="timed out after"):
        default_executor(request)

    assert time.monotonic() - started < 15
    assert marker.read_text(encoding="utf-8").strip() == "cleaned"
    assert "Time limit" in log_path.read_text(encoding="utf-8")


def test_time_limit_kills_processes_that_ignore_the_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify, "STOP_GRACE_SECONDS", 1)
    pid_file = tmp_path / "inner.pid"
    argv = _nested_scripts(tmp_path, "trap '' INT\necho $$ > \"$PID_FILE\"\nsleep 30\n")
    request = ExecutionRequest(
        argv, tmp_path, {**os.environ, "PID_FILE": str(pid_file)}, tmp_path / "check.log", timeout_seconds=1
    )

    started = time.monotonic()
    with pytest.raises(CheckTimeoutError):
        default_executor(request)

    assert time.monotonic() - started < 15
    assert _wait_until_gone(int(pid_file.read_text(encoding="utf-8")))


def test_time_limit_stops_descendants_that_outlive_their_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify, "STOP_GRACE_SECONDS", 1)
    pid_file = tmp_path / "inner.pid"
    inner = tmp_path / "inner.sh"
    inner.write_text("trap '' INT\necho $$ > \"$PID_FILE\"\nsleep 30\n", encoding="utf-8")
    outer = tmp_path / "outer.sh"
    # The wrapper exits on the interrupt at once, so its child is reparented while still running.
    outer.write_text(f"trap 'exit 130' INT\nbash \"{inner}\" &\nwait\n", encoding="utf-8")
    request = ExecutionRequest(
        ("bash", str(outer)), tmp_path, {**os.environ, "PID_FILE": str(pid_file)}, tmp_path / "check.log", 1
    )

    with pytest.raises(CheckTimeoutError):
        default_executor(request)

    assert _wait_until_gone(int(pid_file.read_text(encoding="utf-8")), timeout_seconds=0.5)


def test_check_logs_and_run_directories_are_private(tmp_path: Path) -> None:
    log_path = tmp_path / "run" / "check.log"

    assert default_executor(ExecutionRequest(("true",), tmp_path, dict(os.environ), log_path)) == 0

    assert log_path.stat().st_mode & 0o777 == 0o600
    assert log_path.parent.stat().st_mode & 0o777 == 0o700


def test_preflight_reports_missing_tools_platforms_and_variables() -> None:
    def which(tool: str) -> str | None:
        return None if tool == "pwsh" else f"/usr/bin/{tool}"

    assert verify.preflight_reason(_check(requires=("pwsh",)), {}, which=which) == "missing tools: pwsh"
    assert verify.preflight_reason(_check(platforms=("plan9",)), {}, which=which) == "runs only on plan9"
    assert verify.preflight_reason(_check(env=("HF_TOKEN",)), {"HF_TOKEN": " "}, which=which) == (
        "set HF_TOKEN in .env.local or the environment to run this check"
    )
    problem = "Cannot read .env.local: Permission denied"
    assert verify.preflight_reason(_check(env=("HF_TOKEN",)), {}, which=which, local_env_problem=problem) == problem
    assert (
        verify.preflight_reason(_check(env=("HF_TOKEN",)), {"HF_TOKEN": "x"}, which=which, local_env_problem=problem)
        == problem
    )
    assert verify.preflight_reason(_check(), {}, which=which, local_env_problem=problem) is None
    assert verify.preflight_reason(_check(), {}, which=which) is None


_FAKE_SECRET = "fake-secret-0123456789"


def _env_manifest(tmp_path: Path) -> Path:
    def category(category_id: str, check: dict[str, object], **extra: object) -> dict[str, object]:
        return {
            "id": category_id,
            "title": category_id.title(),
            "summary": f"{category_id} checks.",
            "dependabot": [],
            "paths": [],
            "setup": [],
            "notes": [],
            "checks": [check],
            **extra,
        }

    def check(check_id: str, tool: str, **extra: object) -> dict[str, object]:
        return {
            "id": check_id,
            "tier": "cpu",
            "gpu": False,
            "optional": False,
            "description": f"Runs {tool}.",
            "command": [tool],
            "requires": [],
            **extra,
        }

    manifest = {
        "schema_version": 1,
        "base_ref": "HEAD",
        "root_routes": [],
        "categories": [
            category("baseline", check("always-ok", "ok-tool"), always=True),
            category(
                "secrets",
                check("token-check", "token-tool", env=["SAMPLE_TOKEN"]),
                dependabot=[{"ecosystem": "uv", "directory": "/secrets"}],
                paths=["secrets/"],
            ),
        ],
    }
    path = tmp_path / "env-categories.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _run_secrets(
    git_repo: Path, tmp_path: Path, environ: dict[str, str]
) -> tuple[int, FakeExecutor, dict[str, object]]:
    executor = FakeExecutor()
    code = verify.main(
        ["--category", "secrets", "--output-dir", str(git_repo / "logs" / "run")],
        executor=executor,
        repo_root=git_repo,
        manifest_path=_env_manifest(tmp_path),
        environ=environ,
    )
    summary = json.loads((git_repo / "logs" / "run" / "summary.json").read_text(encoding="utf-8"))
    return code, executor, summary


def test_declared_variables_come_from_local_env_for_their_check_only(
    git_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (git_repo / ".env.local").write_text(f"SAMPLE_TOKEN={_FAKE_SECRET}\nOTHER_SECRET=other-fake\n", encoding="utf-8")

    code, executor, _ = _run_secrets(git_repo, tmp_path, {})
    environments = {request.argv[0]: request.env for request in executor.requests}

    assert code == EXIT_PASSED
    assert environments["token-tool"]["SAMPLE_TOKEN"] == _FAKE_SECRET
    assert "SAMPLE_TOKEN" not in environments["ok-tool"]
    assert all("OTHER_SECRET" not in env for env in environments.values())
    assert _FAKE_SECRET not in capsys.readouterr().out
    for name in ("summary.json", "summary.md"):
        assert _FAKE_SECRET not in (git_repo / "logs" / "run" / name).read_text(encoding="utf-8")


def test_local_env_overrides_exported_variables_like_the_submission_scripts(git_repo: Path, tmp_path: Path) -> None:
    (git_repo / ".env.local").write_text(f"SAMPLE_TOKEN={_FAKE_SECRET}\n", encoding="utf-8")

    _, executor, _ = _run_secrets(git_repo, tmp_path, {"SAMPLE_TOKEN": "exported"})

    assert {request.argv[0]: request.env.get("SAMPLE_TOKEN") for request in executor.requests}["token-tool"] == (
        _FAKE_SECRET
    )


def test_exported_variable_is_used_when_local_env_does_not_set_it(git_repo: Path, tmp_path: Path) -> None:
    (git_repo / ".env.local").write_text("OTHER_SETTING=1\n", encoding="utf-8")

    _, executor, _ = _run_secrets(git_repo, tmp_path, {"SAMPLE_TOKEN": "exported"})

    assert {request.argv[0]: request.env.get("SAMPLE_TOKEN") for request in executor.requests}["token-tool"] == (
        "exported"
    )


def test_non_blank_local_env_values_pass_through_unchanged(git_repo: Path, tmp_path: Path) -> None:
    (git_repo / ".env.local").write_text('SAMPLE_TOKEN=" spaced value "\n', encoding="utf-8")

    _, executor, _ = _run_secrets(git_repo, tmp_path, {})

    assert {request.argv[0]: request.env.get("SAMPLE_TOKEN") for request in executor.requests}["token-tool"] == (
        " spaced value "
    )


@pytest.mark.parametrize("assignment", ["SAMPLE_TOKEN=", 'SAMPLE_TOKEN="   "'], ids=["empty", "blank"])
def test_empty_local_env_assignment_blocks_the_check(git_repo: Path, tmp_path: Path, assignment: str) -> None:
    (git_repo / ".env.local").write_text(f"{assignment}\n", encoding="utf-8")

    code, executor, summary = _run_secrets(git_repo, tmp_path, {"SAMPLE_TOKEN": _FAKE_SECRET})
    results = {check["id"]: check for check in summary["checks"]}  # type: ignore[union-attr]

    assert code == EXIT_INCOMPLETE
    assert [request.argv[0] for request in executor.requests] == ["ok-tool"]
    assert results["token-check"]["status"] == "not-run"
    assert results["token-check"]["reason"] == "SAMPLE_TOKEN is empty in .env.local, which overrides the environment"


def test_missing_declared_variable_points_to_local_env(git_repo: Path, tmp_path: Path) -> None:
    code, executor, summary = _run_secrets(git_repo, tmp_path, {})
    results = {check["id"]: check for check in summary["checks"]}  # type: ignore[union-attr]

    assert code == EXIT_INCOMPLETE
    assert [request.argv[0] for request in executor.requests] == ["ok-tool"]
    assert results["token-check"]["status"] == "not-run"
    assert results["token-check"]["reason"] == "set SAMPLE_TOKEN in .env.local or the environment to run this check"


def test_unreadable_local_env_is_reported_as_not_run(git_repo: Path, tmp_path: Path) -> None:
    (git_repo / ".env.local").mkdir()

    code, _, summary = _run_secrets(git_repo, tmp_path, {"SAMPLE_TOKEN": "exported"})
    results = {check["id"]: check for check in summary["checks"]}  # type: ignore[union-attr]

    assert code == EXIT_INCOMPLETE
    assert results["always-ok"]["status"] == "passed"
    assert results["token-check"]["status"] == "not-run"
    assert ".env.local" in results["token-check"]["reason"]


def test_output_dir_must_be_ignored_inside_the_repository(git_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="gitignored"):
        resolve_output_dir(str(git_repo / "tracked-results"), "run", git_repo)

    assert resolve_output_dir(str(git_repo / "logs" / "mine"), "run", git_repo) == git_repo / "logs" / "mine"
    assert resolve_output_dir(str(tmp_path / "elsewhere"), "run", git_repo) == tmp_path / "elsewhere"
    assert resolve_output_dir(None, "run", git_repo) == git_repo / "logs" / "dependabot" / "run"


@pytest.fixture
def tiny_manifest(tmp_path: Path) -> Iterator[Path]:
    manifest = {
        "schema_version": 1,
        "base_ref": "HEAD",
        "root_routes": [],
        "categories": [
            {
                "id": "baseline",
                "title": "Baseline",
                "summary": "Always runs.",
                "always": True,
                "dependabot": [],
                "paths": [],
                "setup": [],
                "notes": [],
                "checks": [
                    {
                        "id": "always-ok",
                        "tier": "cpu",
                        "gpu": False,
                        "optional": False,
                        "description": "Passes.",
                        "command": ["ok-tool"],
                        "requires": [],
                    }
                ],
            },
            {
                "id": "infra",
                "title": "Infra",
                "summary": "Infra checks.",
                "dependabot": [{"ecosystem": "terraform", "directory": "/infra"}],
                "paths": ["infra/"],
                "setup": [{"command": ["setup-tool"], "cwd": "."}],
                "notes": [],
                "checks": [
                    {
                        "id": "infra-check",
                        "tier": "cpu",
                        "gpu": False,
                        "optional": False,
                        "description": "Scripted outcome.",
                        "command": ["infra-tool"],
                        "requires": [],
                    },
                    {
                        "id": "infra-live",
                        "tier": "environment",
                        "gpu": True,
                        "optional": False,
                        "description": "Environment check.",
                        "pytest": "tests/e2e/test_e2e_aml_x.py::test_x",
                        "requires": [],
                    },
                ],
            },
        ],
    }
    path = tmp_path / "categories.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    yield path


def test_main_runs_setup_and_checks_and_writes_the_summary(git_repo: Path, tiny_manifest: Path) -> None:
    executor = FakeExecutor(codes={"infra-tool": 3})

    code = verify.main(
        ["--category", "infra", "--output-dir", str(git_repo / "logs" / "run")],
        executor=executor,
        repo_root=git_repo,
        manifest_path=tiny_manifest,
        environ={},
    )
    summary = json.loads((git_repo / "logs" / "run" / "summary.json").read_text(encoding="utf-8"))

    assert code == EXIT_FAILED
    assert [request.argv[0] for request in executor.requests] == ["setup-tool", "ok-tool", "infra-tool"]
    assert summary["result"] == "failed"
    assert summary["categories"] == ["baseline", "infra"]
    assert summary["dirty"] is False
    assert {check["id"]: check["status"] for check in summary["checks"]} == {
        "always-ok": "passed",
        "infra-check": "failed",
    }
    assert (git_repo / "logs" / "run" / "summary.md").is_file()
    for path in (git_repo / "logs" / "run").iterdir():
        assert path.stat().st_mode & 0o777 == 0o600, path.name
    assert (git_repo / "logs" / "run").stat().st_mode & 0o777 == 0o700


def _shared_setup_manifest(tiny_manifest: Path) -> Path:
    manifest = json.loads(tiny_manifest.read_text(encoding="utf-8"))
    infra = manifest["categories"][1]
    manifest["categories"].append(
        {
            **infra,
            "id": "infra2",
            "title": "Infra 2",
            "dependabot": [{"ecosystem": "terraform", "directory": "/infra2"}],
            "paths": ["infra2/"],
            "checks": [{**infra["checks"][0], "id": "infra2-check", "command": ["infra2-tool"]}],
        }
    )
    tiny_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    return tiny_manifest


def test_a_failed_shared_setup_fails_every_category_that_needs_it(git_repo: Path, tiny_manifest: Path) -> None:
    executor = FakeExecutor(codes={"setup-tool": 1})

    code = verify.main(
        ["--category", "infra", "--category", "infra2", "--output-dir", str(git_repo / "logs" / "run")],
        executor=executor,
        repo_root=git_repo,
        manifest_path=_shared_setup_manifest(tiny_manifest),
        environ={},
    )
    summary = json.loads((git_repo / "logs" / "run" / "summary.json").read_text(encoding="utf-8"))
    results = {check["id"]: (check["status"], check["reason"]) for check in summary["checks"]}

    assert code == EXIT_FAILED
    assert [request.argv[0] for request in executor.requests] == ["setup-tool", "ok-tool"]
    assert results["infra-check"] == ("failed", "category setup failed")
    assert results["infra2-check"] == ("failed", "category setup failed")


def test_the_real_manifest_blocks_every_category_sharing_the_root_npm_install(git_repo: Path) -> None:
    executor = FakeExecutor(codes={"npm": 1})

    code = verify.main(
        ["--category", "tooling", "--category", "dataviewer", "--output-dir", str(git_repo / "logs" / "run")],
        executor=executor,
        repo_root=git_repo,
        environ={},
    )
    summary = json.loads((git_repo / "logs" / "run" / "summary.json").read_text(encoding="utf-8"))

    assert code == EXIT_FAILED
    shared = [
        check
        for check in summary["checks"]
        if check["category"] in {"tooling", "dataviewer"} and check["status"] != "skipped"
    ]
    assert shared
    assert {(check["status"], check["reason"]) for check in shared} == {("failed", "category setup failed")}


def test_main_dry_run_never_executes(git_repo: Path, tiny_manifest: Path, capsys: pytest.CaptureFixture[str]) -> None:
    executor = FakeExecutor()

    code = verify.main(
        ["--category", "infra", "--tier", "all", "--environment", "sample", "--dry-run"],
        executor=executor,
        repo_root=git_repo,
        manifest_path=tiny_manifest,
        environ={},
    )

    assert code == EXIT_PASSED
    assert executor.requests == []
    output = capsys.readouterr().out
    assert "infra-live" in output
    assert "-m e2e" in output


@pytest.mark.parametrize(
    ("local_env", "environ", "expected"),
    [
        pytest.param(None, {}, "script default", id="nothing-set"),
        pytest.param(
            "E2E_AML_INSTANCE_TYPE=gpu-sample\n", {}, "gpu-sample (E2E_AML_INSTANCE_TYPE in .env.local)", id="file"
        ),
        pytest.param(
            "E2E_AML_INSTANCE_TYPE=gpu-sample\n",
            {"E2E_AML_INSTANCE_TYPE_INFRA": "gpu-other"},
            "gpu-other (E2E_AML_INSTANCE_TYPE_INFRA in environment)",
            id="category-variable",
        ),
    ],
)
def test_dry_run_shows_the_instance_type_of_each_gpu_check(
    git_repo: Path,
    tiny_manifest: Path,
    capsys: pytest.CaptureFixture[str],
    local_env: str | None,
    environ: dict[str, str],
    expected: str,
) -> None:
    if local_env is not None:
        (git_repo / ".env.local").write_text(local_env, encoding="utf-8")

    code = verify.main(
        ["--category", "infra", "--tier", "all", "--environment", "sample", "--dry-run"],
        executor=FakeExecutor(),
        repo_root=git_repo,
        manifest_path=tiny_manifest,
        environ=environ,
    )

    output = capsys.readouterr().out
    assert code == EXIT_PASSED
    assert f"infra-live: instance type {expected}" in output
    assert "infra-check: instance type" not in output


def _run_environment_tier(git_repo: Path, tiny_manifest: Path, executor: FakeExecutor) -> dict[str, dict[str, str]]:
    verify.main(
        [
            *("--category", "infra", "--tier", "environment", "--environment", "sample"),
            *("--output-dir", str(git_repo / "logs" / "run")),
        ],
        executor=executor,
        repo_root=git_repo,
        manifest_path=tiny_manifest,
        environ={},
    )
    summary = json.loads((git_repo / "logs" / "run" / "summary.json").read_text(encoding="utf-8"))
    return {check["id"]: check for check in summary["checks"]}


def test_an_empty_instance_type_in_local_env_stops_the_gpu_check(
    git_repo: Path, tiny_manifest: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify, "environment_preflight", lambda env, environment, repo_root: None)
    (git_repo / ".env.local").write_text("E2E_AML_INSTANCE_TYPE_INFRA=\n", encoding="utf-8")
    executor = FakeExecutor()

    checks = _run_environment_tier(git_repo, tiny_manifest, executor)

    assert checks["infra-live"]["status"] == "not-run"
    assert "E2E_AML_INSTANCE_TYPE_INFRA is empty in .env.local" in checks["infra-live"]["reason"]
    assert all("pytest" not in request.argv for request in executor.requests)


def test_a_gpu_check_announces_its_instance_type_when_it_runs(
    git_repo: Path, tiny_manifest: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(verify, "environment_preflight", lambda env, environment, repo_root: None)
    (git_repo / ".env.local").write_text("E2E_AML_INSTANCE_TYPE=gpu-sample\n", encoding="utf-8")
    executor = FakeExecutor()

    _run_environment_tier(git_repo, tiny_manifest, executor)

    assert any("pytest" in request.argv for request in executor.requests)
    assert "infra-live: instance type gpu-sample (E2E_AML_INSTANCE_TYPE in .env.local)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["--category", "nope"],
        ["--category", "infra", "--tier", "environment"],
        ["--category", "infra", "--output-dir", "{repo}/tracked"],
    ],
    ids=["unknown-category", "environment-without-environment", "tracked-output-dir"],
)
def test_usage_errors_exit_64(git_repo: Path, tiny_manifest: Path, argv: list[str]) -> None:
    code = verify.main(
        [arg.replace("{repo}", str(git_repo)) for arg in argv],
        executor=FakeExecutor(),
        repo_root=git_repo,
        manifest_path=tiny_manifest,
        environ={},
    )

    assert code == EXIT_USAGE


def test_main_lists_categories(capsys: pytest.CaptureFixture[str]) -> None:
    assert verify.main(["--list"]) == EXIT_PASSED
    output = capsys.readouterr().out
    assert "gpu-smoke: Azure ML GPU smoke runtime" in output
    assert "      note: Always trains: the runner clears E2E_AML_ISAAC_EVAL_MODEL" in output
