"""Run Dependabot verification checks by category.

The category manifest (``categories.json``) maps every ecosystem and directory in
``.github/dependabot.yml`` to the checks that prove an update safe. CPU checks run
anywhere; environment checks submit Azure ML jobs (or read deployed Terraform state)
against a named environment resolved from an untracked bundle. Results go to a
gitignored run directory.

Run from the repository root:

    npm run verify:dependabot -- --list
    npm run verify:dependabot -- --changed-from origin/main --dry-run
    npm run verify:dependabot -- --category docs
    npm run verify:dependabot -- --tier environment --environment <name> --category rl

Exit codes: 0 every selected required check passed, 1 a check failed, 2 no failures but a
required check did not run, 64 usage error.

A check that declares ``timeout_minutes`` is interrupted when it runs past that limit, so a
hung cloud call can't stall the run. Its processes get SIGINT first, which lets e2e tests
cancel their jobs, and anything still running after a grace period is killed.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from tests.e2e._environment import (
    BUNDLE_DIR_VAR,
    ENVIRONMENT_VAR,
    LOCAL_ENV_FILE,
    EnvironmentBundleError,
    LocalEnvError,
    load_environment_bundle,
    read_local_env,
    resolve_instance_type,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = Path(__file__).with_name("categories.json")
DEFAULT_OUTPUT_ROOT = Path("logs") / "dependabot"

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_INCOMPLETE = 2
EXIT_USAGE = 64

STOP_GRACE_SECONDS = 15 * 60
STOP_POLL_SECONDS = 0.5

TIERS = ("cpu", "environment")
STATUSES = ("passed", "failed", "not-run", "skipped")
PLACEHOLDERS = ("base_ref", "run_dir")
_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_CHECK_KEYS = {
    "id",
    "tier",
    "gpu",
    "optional",
    "description",
    "command",
    "pytest",
    "cwd",
    "requires",
    "ci_lanes",
    "container",
    "snapshot",
    "env",
    "set_env",
    "min_versions",
    "not_run_exit_codes",
    "platforms",
    "notes",
    "timeout_minutes",
}


class ManifestError(ValueError):
    """Raised when the category manifest is malformed."""


class UsageError(ValueError):
    """Raised for invalid command-line usage."""


class CheckTimeoutError(RuntimeError):
    """Raised after a check ran past its time limit and was stopped."""


@dataclass(frozen=True)
class Check:
    id: str
    category: str
    tier: str
    gpu: bool
    optional: bool
    description: str
    command: tuple[str, ...] = ()
    pytest: str = ""
    cwd: str = "."
    requires: tuple[str, ...] = ()
    ci_lanes: tuple[str, ...] = ()
    container: Mapping[str, str] | None = None
    snapshot: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    set_env: Mapping[str, str] = field(default_factory=dict)
    min_versions: Mapping[str, str] = field(default_factory=dict)
    not_run_exit_codes: tuple[int, ...] = ()
    platforms: tuple[str, ...] = ()
    notes: str = ""
    timeout_minutes: int | None = None


@dataclass(frozen=True)
class SetupStep:
    command: tuple[str, ...]
    cwd: str


@dataclass(frozen=True)
class Category:
    id: str
    title: str
    summary: str
    always: bool
    dependabot: tuple[tuple[str, str], ...]
    paths: tuple[str, ...]
    setup: tuple[SetupStep, ...]
    notes: tuple[str, ...]
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    base_ref: str
    root_routes: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...]
    categories: tuple[Category, ...]

    def category(self, category_id: str) -> Category:
        for category in self.categories:
            if category.id == category_id:
                return category
        raise UsageError(f"Unknown category: {category_id} (run --list to see categories)")


@dataclass(frozen=True)
class ExecutionRequest:
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    log_path: Path
    timeout_seconds: float | None = None


Executor = Callable[[ExecutionRequest], int]


@dataclass
class CheckResult:
    id: str
    category: str
    tier: str
    gpu: bool
    optional: bool
    status: str
    duration_seconds: float = 0.0
    log: str | None = None
    reason: str | None = None


# ----------------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------------


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ManifestError(f"{where} must be a list of non-empty strings")
    return tuple(value)


def _string_map(value: object, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ManifestError(f"{where} must map strings to strings")
    return dict(value)


def _parse_check(raw: object, category_id: str) -> Check:
    if not isinstance(raw, dict):
        raise ManifestError(f"Category {category_id}: each check must be an object")
    check_id = raw.get("id")
    if not isinstance(check_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", check_id):
        raise ManifestError(f"Category {category_id}: invalid check id {check_id!r}")
    where = f"Check {check_id}"
    unknown = set(raw) - _CHECK_KEYS
    if unknown:
        raise ManifestError(f"{where}: unknown keys {sorted(unknown)}")
    if raw.get("tier") not in TIERS:
        raise ManifestError(f"{where}: tier must be one of {TIERS}")
    for flag in ("gpu", "optional"):
        if not isinstance(raw.get(flag), bool):
            raise ManifestError(f"{where}: {flag} must be a boolean")
    if not isinstance(raw.get("description"), str) or not raw["description"]:
        raise ManifestError(f"{where}: description is required")
    has_command = "command" in raw
    has_pytest = "pytest" in raw
    if has_command == has_pytest:
        raise ManifestError(f"{where}: define exactly one of command or pytest")
    command = _string_list(raw["command"], f"{where} command") if has_command else ()
    pytest_node = raw.get("pytest", "")
    if has_pytest and (not isinstance(pytest_node, str) or "::" not in pytest_node):
        raise ManifestError(f"{where}: pytest must be a node id such as path.py::test_name")
    container = raw.get("container")
    if container is not None:
        container = _string_map(container, f"{where} container")
        if set(container) != {"image", "platform"}:
            raise ManifestError(f"{where}: container needs exactly image and platform")
    exit_codes = raw.get("not_run_exit_codes", [])
    if not isinstance(exit_codes, list) or not all(isinstance(code, int) and code > 0 for code in exit_codes):
        raise ManifestError(f"{where}: not_run_exit_codes must be positive integers")
    for key in ("notes",):
        if key in raw and not isinstance(raw[key], str):
            raise ManifestError(f"{where}: {key} must be a string")
    timeout_minutes = raw.get("timeout_minutes")
    if timeout_minutes is not None and (
        isinstance(timeout_minutes, bool) or not isinstance(timeout_minutes, int) or timeout_minutes <= 0
    ):
        raise ManifestError(f"{where}: timeout_minutes must be a positive integer")
    return Check(
        id=check_id,
        category=category_id,
        tier=raw["tier"],
        gpu=raw["gpu"],
        optional=raw["optional"],
        description=raw["description"],
        command=command,
        pytest=pytest_node,
        cwd=raw.get("cwd", "."),
        requires=_string_list(raw.get("requires", []), f"{where} requires") if raw.get("requires") else (),
        ci_lanes=_string_list(raw.get("ci_lanes", []), f"{where} ci_lanes") if raw.get("ci_lanes") else (),
        container=container,
        snapshot=_string_list(raw["snapshot"], f"{where} snapshot") if "snapshot" in raw else (),
        env=_string_list(raw["env"], f"{where} env") if "env" in raw else (),
        set_env=_string_map(raw.get("set_env", {}), f"{where} set_env"),
        min_versions=_string_map(raw.get("min_versions", {}), f"{where} min_versions"),
        not_run_exit_codes=tuple(exit_codes),
        platforms=_string_list(raw["platforms"], f"{where} platforms") if "platforms" in raw else (),
        notes=raw.get("notes", ""),
        timeout_minutes=timeout_minutes,
    )


def parse_manifest(data: object) -> Manifest:
    """Validate raw manifest data and return a typed manifest."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ManifestError("Manifest must be an object with schema_version 1")
    base_ref = data.get("base_ref")
    if not isinstance(base_ref, str) or not base_ref:
        raise ManifestError("Manifest base_ref is required")

    categories: list[Category] = []
    definitions: dict[str, dict[str, object]] = {}
    for raw in data.get("categories", []):
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            raise ManifestError("Each category must be an object with an id")
        category_id = raw["id"]
        for key in ("title", "summary"):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise ManifestError(f"Category {category_id}: {key} is required")
        dependabot: list[tuple[str, str]] = []
        for entry in raw.get("dependabot", []):
            if not isinstance(entry, dict) or not all(
                isinstance(entry.get(k), str) for k in ("ecosystem", "directory")
            ):
                raise ManifestError(f"Category {category_id}: dependabot entries need ecosystem and directory")
            dependabot.append((entry["ecosystem"], entry["directory"]))
        setup = tuple(
            SetupStep(_string_list(step.get("command"), f"Category {category_id} setup"), step.get("cwd", "."))
            for step in raw.get("setup", [])
        )
        checks = tuple(_parse_check(check, category_id) for check in raw.get("checks", []))
        for check_raw in raw.get("checks", []):
            previous = definitions.setdefault(check_raw["id"], check_raw)
            if previous != check_raw:
                raise ManifestError(f"Check {check_raw['id']} has conflicting definitions")
        categories.append(
            Category(
                id=category_id,
                title=raw["title"],
                summary=raw["summary"],
                always=bool(raw.get("always", False)),
                dependabot=tuple(dependabot),
                paths=_string_list(raw.get("paths", []), f"Category {category_id} paths") if raw.get("paths") else (),
                setup=setup,
                notes=tuple(raw.get("notes", [])),
                checks=checks,
            )
        )
    ids = [category.id for category in categories]
    if len(set(ids)) != len(ids):
        raise ManifestError("Category ids must be unique")

    routes: list[tuple[re.Pattern[str], tuple[str, ...]]] = []
    for route in data.get("root_routes", []):
        try:
            pattern = re.compile(route["pattern"])
        except (KeyError, TypeError, re.error) as error:
            raise ManifestError(f"Invalid root route {route!r}: {error}") from error
        targets = _string_list(route.get("categories"), "root route categories")
        unknown = set(targets) - set(ids)
        if unknown:
            raise ManifestError(f"Root route {route['pattern']} names unknown categories {sorted(unknown)}")
        routes.append((pattern, targets))
    return Manifest(schema_version=1, base_ref=base_ref, root_routes=tuple(routes), categories=tuple(categories))


def load_manifest(path: Path = MANIFEST_PATH) -> Manifest:
    return parse_manifest(json.loads(path.read_text(encoding="utf-8")))


# ----------------------------------------------------------------------------
# Routing and selection
# ----------------------------------------------------------------------------


def route_paths(paths: Iterable[str], manifest: Manifest) -> tuple[list[str], list[str]]:
    """Return the categories the paths touch (manifest order) and the paths no category claims."""
    touched: set[str] = set()
    unmapped: list[str] = []
    for path in paths:
        matches = {
            category.id for category in manifest.categories if any(path.startswith(prefix) for prefix in category.paths)
        }
        for pattern, targets in manifest.root_routes:
            if pattern.search(path):
                matches.update(targets)
        if matches:
            touched.update(matches)
        else:
            unmapped.append(path)
    ordered = [category.id for category in manifest.categories if category.id in touched]
    return ordered, sorted(unmapped)


def changed_paths(ref: str, repo_root: Path = REPO_ROOT) -> list[str]:
    """Return tracked and untracked paths changed since the merge base of ``ref`` and HEAD."""

    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise UsageError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    merge_base = git("merge-base", ref, "HEAD").strip()
    tracked = git("diff", "--name-only", merge_base).splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({path for path in [*tracked, *untracked] if path})


def select_checks(
    manifest: Manifest,
    category_ids: Sequence[str],
    *,
    tier: str,
    include_optional: bool,
    include_baseline: bool,
) -> tuple[list[Check], list[Check], list[Category]]:
    """Return (checks to run, optional checks skipped, categories involved), deduplicated by id."""
    wanted = list(category_ids)
    if include_baseline:
        wanted = [category.id for category in manifest.categories if category.always] + wanted
    categories: list[Category] = []
    for category_id in wanted:
        category = manifest.category(category_id)
        if category not in categories:
            categories.append(category)
    tiers = TIERS if tier == "all" else (tier,)
    selected: list[Check] = []
    skipped: list[Check] = []
    seen: set[str] = set()
    for category in categories:
        for check in category.checks:
            if check.id in seen or check.tier not in tiers:
                continue
            seen.add(check.id)
            (selected if include_optional or not check.optional else skipped).append(check)
    return selected, skipped, categories


# ----------------------------------------------------------------------------
# Preflight
# ----------------------------------------------------------------------------


def host_is_linux_amd64() -> bool:
    return platform.system() == "Linux" and platform.machine().lower() in {"x86_64", "amd64"}


def _version_tuple(text: str) -> tuple[int, ...] | None:
    match = _VERSION_PATTERN.search(text)
    return tuple(int(part) for part in match.groups()) if match else None


def instance_type_note(check: Check, repo_root: Path, env: Mapping[str, str]) -> tuple[str | None, str | None]:
    """Describe the instance type a GPU environment check will request, or why it can't choose one.

    Returns ``(description, problem)``; both are ``None`` for checks that run no GPU job.
    """
    if check.tier != "environment" or not check.gpu:
        return None, None
    try:
        return resolve_instance_type(repo_root, check.category, env).describe(), None
    except LocalEnvError as error:
        return None, str(error)


def preflight_reason(
    check: Check,
    env: Mapping[str, str],
    *,
    which: Callable[[str], str | None] = shutil.which,
    local_env_problem: str | None = None,
) -> str | None:
    """Return why a check can't run on this host, or None when it can."""
    if check.platforms and not any(sys.platform.startswith(name) for name in check.platforms):
        return f"runs only on {', '.join(check.platforms)}"
    tools = list(check.requires)
    if check.container and not host_is_linux_amd64():
        tools.append("docker")
    if check.pytest:
        tools.append("uv")
    missing = sorted({tool for tool in tools if which(tool) is None})
    if missing:
        return f"missing tools: {', '.join(missing)}"
    if check.env and local_env_problem:
        return local_env_problem
    unset = [name for name in check.env if not env.get(name, "").strip()]
    if unset:
        return f"set {', '.join(unset)} in {LOCAL_ENV_FILE} or the environment to run this check"
    for tool, minimum in check.min_versions.items():
        result = subprocess.run([tool, "--version"], capture_output=True, text=True, check=False)
        found = _version_tuple(result.stdout + result.stderr)
        required = _version_tuple(minimum)
        if found is None or required is None or found < required:
            return f"{tool} {minimum} or later is required"
    return None


# ----------------------------------------------------------------------------
# Execution
# ----------------------------------------------------------------------------


def display_path(path: Path, repo_root: Path) -> str:
    """Return ``path`` relative to the repository when it lies inside it."""
    return str(path.relative_to(repo_root)) if path.is_relative_to(repo_root) else str(path)


def substitute(argv: Iterable[str], values: Mapping[str, str]) -> tuple[str, ...]:
    result: list[str] = []
    for arg in argv:
        for name in PLACEHOLDERS:
            arg = arg.replace("{" + name + "}", values[name])
        result.append(arg)
    return tuple(result)


def container_argv(check: Check, argv: Sequence[str], repo_root: Path) -> tuple[str, ...]:
    """Wrap a command in docker run with the repository mounted at /workspace."""
    assert check.container is not None
    workdir = "/workspace" if check.cwd in ("", ".") else f"/workspace/{check.cwd}"
    env_args = [arg for name in sorted(check.set_env) for arg in ("-e", name)]
    return (
        "docker",
        "run",
        "--rm",
        "--platform",
        check.container["platform"],
        "--entrypoint",
        argv[0],
        "-v",
        f"{repo_root}:/workspace",
        "-w",
        workdir,
        *env_args,
        check.container["image"],
        *argv[1:],
    )


def pytest_argv(check: Check, junit_path: Path) -> tuple[str, ...]:
    return (
        "uv",
        "run",
        "--frozen",
        "pytest",
        "-o",
        "addopts=",
        "-p",
        "no:cacheprovider",
        "-m",
        "e2e",
        "-vv",
        "-s",
        check.pytest,
        f"--junitxml={junit_path}",
    )


def create_snapshot(repo_root: Path, ref: str, paths: Sequence[str], destination: Path) -> None:
    """Extract ``paths`` at ``ref`` into ``destination`` with git archive.

    The snapshot gets an empty git repository so scripts that find the repository root with
    ``git rev-parse --show-toplevel`` resolve to the snapshot rather than a parent directory.
    """
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref, "--", *paths], cwd=repo_root, capture_output=True, check=False
    )
    if archive.returncode != 0:
        raise RuntimeError(f"git archive failed: {archive.stderr.decode(errors='replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(destination, filter="data")
    initialized = subprocess.run(["git", "init", "-q"], cwd=destination, capture_output=True, check=False)
    if initialized.returncode != 0:
        raise RuntimeError(f"git init failed: {initialized.stderr.decode(errors='replace').strip()}")


def junit_outcome(junit_path: Path) -> tuple[str, str | None]:
    """Map a pytest junit report to a status, treating skips as not run."""
    if not junit_path.is_file():
        return "failed", "pytest wrote no junit report"
    root = ET.parse(junit_path).getroot()
    passed = 0
    # pytest reports a teardown error as a second testcase with the same name, so key by test.
    failures: dict[tuple[str, str], str] = {}
    skip_messages: list[str] = []
    for case in root.iter("testcase"):
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is not None:
            message = (problem.get("message") or "").strip()
            failures.setdefault(
                (case.get("classname", ""), case.get("name", "")), message.splitlines()[0] if message else ""
            )
        elif (skipped := case.find("skipped")) is not None:
            skip_messages.append(skipped.get("message", "skipped"))
        else:
            passed += 1
    if failures:
        first = next((message for message in failures.values() if message), "")
        return "failed", f"{len(failures)} test(s) failed" + (f": {first[:200]}" if first else "")
    if skip_messages:
        return "not-run", "; ".join(dict.fromkeys(skip_messages))[:300]
    if passed:
        return "passed", None
    for error in root.iter("error"):
        return "failed", error.get("message", "collection error")
    return "failed", "no tests ran"


# cspell:ignore lstart WRONLY CREAT fchmod
@dataclass(frozen=True)
class _ProcessInfo:
    parent: int
    state: str
    started: str


def _process_table() -> dict[int, _ProcessInfo]:
    """Return every process by PID; the start time tells a reused PID from the original process."""
    try:
        listing = subprocess.run(
            ["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "stat=", "-o", "lstart="],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return {}
    table: dict[int, _ProcessInfo] = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[0].isdigit() and fields[1].isdigit():
            table[int(fields[0])] = _ProcessInfo(int(fields[1]), fields[2], " ".join(fields[3:]))
    return table


def _process_tree(root: int, table: Mapping[int, _ProcessInfo]) -> list[int]:
    """Return ``root`` and its descendants in ``table``, parents before children."""
    children: dict[int, list[int]] = {}
    for pid, info in table.items():
        children.setdefault(info.parent, []).append(pid)
    tree = [root]
    pending = [root]
    while pending:
        for child in children.get(pending.pop(), []):
            tree.append(child)
            pending.append(child)
    return tree


def _signal_processes(process_ids: Iterable[int], signum: int) -> None:
    for pid in process_ids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signum)


def stop_process_tree(
    process: subprocess.Popen[bytes], grace_seconds: float, *, poll_seconds: float = STOP_POLL_SECONDS
) -> None:
    """Interrupt a process and its descendants, wait for all of them, then kill whatever outlives the grace period.

    Descendants are remembered with their start times while the root runs, so ones that outlive it under a new
    parent are still waited on and stopped, and a PID the system reuses is never signaled.
    """
    table = _process_table()
    tracked = {pid: table[pid].started for pid in _process_tree(process.pid, table) if pid in table}
    # One interrupt each: a second SIGINT makes pytest abandon its cleanup finalizers.
    _signal_processes({process.pid, *tracked}, signal.SIGINT)
    deadline = time.monotonic() + grace_seconds
    while True:
        table = _process_table()
        root_running = process.poll() is None
        # Keep adding descendants of the root and of every tracked process still alive, so work a survivor
        # starts during cleanup is stopped too.
        parents = [pid for pid, started in tracked.items() if pid in table and table[pid].started == started]
        if root_running:
            parents.append(process.pid)
        for parent in parents:
            for pid in _process_tree(parent, table):
                if pid in table:
                    tracked.setdefault(pid, table[pid].started)
        survivors = {
            pid
            for pid, started in tracked.items()
            if pid != process.pid
            and pid in table
            and table[pid].started == started
            and not table[pid].state.startswith("Z")
        }
        if not root_running and not survivors:
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_seconds)
    _signal_processes([*([process.pid] if root_running else []), *survivors], signal.SIGKILL)
    process.wait()


def _open_private(path: Path) -> TextIO:
    """Open a file for writing that only the current user can read."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "w", encoding="utf-8")


def make_private_dir(path: Path) -> None:
    """Create a directory only the current user can enter; logs can name environment resources."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _format_minutes(seconds: float) -> str:
    minutes = seconds / 60
    return f"{minutes:g} minute{'' if minutes == 1 else 's'}"


def default_executor(request: ExecutionRequest) -> int:
    """Run a command with its output written to the check log, stopping it at its time limit."""
    make_private_dir(request.log_path.parent)
    with _open_private(request.log_path) as log:
        log.write(f"$ (cd {request.cwd} && {' '.join(request.argv)})\n")
        log.flush()
        try:
            process = subprocess.Popen(
                list(request.argv), cwd=request.cwd, env=dict(request.env), stdout=log, stderr=subprocess.STDOUT
            )
        except FileNotFoundError as error:
            log.write(f"{error}\n")
            return 127
        try:
            return process.wait(timeout=request.timeout_seconds)
        except subprocess.TimeoutExpired:
            limit = _format_minutes(request.timeout_seconds or 0)
            log.write(f"\n[verify] Time limit of {limit} reached; interrupting the check so it can clean up\n")
            log.flush()
            stop_process_tree(process, STOP_GRACE_SECONDS)
            raise CheckTimeoutError(f"timed out after {limit}") from None


def run_check(
    check: Check,
    *,
    repo_root: Path,
    run_dir: Path,
    env: Mapping[str, str],
    values: Mapping[str, str],
    executor: Executor,
) -> CheckResult:
    """Execute one check and map its outcome to a result."""
    result = CheckResult(check.id, check.category, check.tier, check.gpu, check.optional, "failed")
    log_path = run_dir / f"{check.id}.log"
    result.log = display_path(log_path, repo_root)
    started = time.monotonic()
    check_env = {**env, **check.set_env}
    timeout_seconds = check.timeout_minutes * 60 if check.timeout_minutes else None
    snapshot_dir: Path | None = None
    try:
        if check.pytest:
            junit_path = run_dir / f"{check.id}.xml"
            # A report left in a reused run directory must never stand in for this execution's result.
            junit_path.unlink(missing_ok=True)
            code = executor(
                ExecutionRequest(pytest_argv(check, junit_path), repo_root, check_env, log_path, timeout_seconds)
            )
            report_written = junit_path.is_file()
            result.status, result.reason = junit_outcome(junit_path)
            if code != 0 and result.status != "failed":
                result.status, result.reason = "failed", f"pytest exited {code}"
            elif code != 0 and not report_written:
                result.reason = f"pytest exited {code} without writing a junit report"
            return result
        argv = substitute(check.command, values)
        base_dir = repo_root
        if check.snapshot:
            snapshot_dir = Path(tempfile.mkdtemp(prefix=f"dependabot-{check.id}-"))
            create_snapshot(repo_root, "HEAD", check.snapshot, snapshot_dir)
            base_dir = snapshot_dir
        if check.container and not host_is_linux_amd64():
            argv = container_argv(check, argv, base_dir)
            cwd = base_dir
        else:
            cwd = base_dir / check.cwd
        code = executor(ExecutionRequest(argv, cwd, check_env, log_path, timeout_seconds))
        if code == 0:
            result.status = "passed"
        elif code in check.not_run_exit_codes:
            result.status, result.reason = "not-run", f"exit {code}: see log"
        else:
            result.reason = f"exit {code}"
        return result
    except (OSError, RuntimeError, ET.ParseError) as error:
        result.reason = str(error)
        return result
    finally:
        if snapshot_dir is not None:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        result.duration_seconds = round(time.monotonic() - started, 1)


# ----------------------------------------------------------------------------
# Results
# ----------------------------------------------------------------------------


def overall_result(results: Sequence[CheckResult]) -> tuple[str, int]:
    """Return the run result and exit code for the given check results."""
    if any(item.status == "failed" for item in results):
        return "failed", EXIT_FAILED
    if any(item.status == "not-run" and not item.optional for item in results):
        return "incomplete", EXIT_INCOMPLETE
    return "passed", EXIT_PASSED


def git_state(repo_root: Path) -> tuple[str, bool]:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, check=False
    )
    return commit.stdout.strip(), bool(status.stdout.strip())


def write_summary(path_root: Path, summary: dict[str, object]) -> None:
    make_private_dir(path_root)
    with _open_private(path_root / "summary.json") as handle:
        handle.write(json.dumps(summary, indent=2) + "\n")
    lines = [
        f"# Dependabot verification run {summary['run_id']}",
        "",
        f"* Result: {summary['result']}",
        f"* Commit: {summary['commit']}{' (uncommitted changes present)' if summary['dirty'] else ''}",
        f"* Tier: {summary['tier']}",
        f"* Categories: {', '.join(summary['categories']) or 'none'}",  # type: ignore[arg-type]
        "",
        "| Check | Category | Tier | Status | Seconds | Reason |",
        "|-------|----------|------|--------|---------|--------|",
    ]
    for check in summary["checks"]:  # type: ignore[union-attr]
        lines.append(
            f"| {check['id']} | {check['category']} | {check['tier']} | {check['status']} | "
            f"{check['duration_seconds']} | {check['reason'] or ''} |"
        )
    with _open_private(path_root / "summary.md") as handle:
        handle.write("\n".join(lines) + "\n")


def resolve_output_dir(requested: str | None, run_id: str, repo_root: Path) -> Path:
    """Return the run directory, refusing tracked locations inside the repository."""
    if not requested:
        return repo_root / DEFAULT_OUTPUT_ROOT / run_id
    path = Path(requested).expanduser().resolve()
    if path.is_relative_to(repo_root):
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", str(path / "summary.json")], cwd=repo_root, check=False
        ).returncode
        if ignored != 0:
            raise UsageError(f"--output-dir must be gitignored or outside the repository: {requested}")
    return path


# ----------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------


def format_listing(manifest: Manifest) -> str:
    lines: list[str] = []
    for category in manifest.categories:
        entries = ", ".join(f"{ecosystem} {directory}" for ecosystem, directory in category.dependabot) or "every PR"
        lines.append(f"{category.id}: {category.title}")
        lines.append(f"  Dependabot: {entries}")
        for tier in TIERS:
            checks = [check for check in category.checks if check.tier == tier]
            if not checks:
                continue
            lines.append(f"  {tier}:")
            for check in checks:
                flags = [flag for flag, on in (("gpu", check.gpu), ("optional", check.optional)) if on]
                suffix = f" [{', '.join(flags)}]" if flags else ""
                lines.append(f"    {check.id}{suffix}: {check.description}")
                if check.notes:
                    lines.append(f"      note: {check.notes}")
        for note in category.notes:
            lines.append(f"  note: {note}")
        lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify:dependabot",
        description="Run Dependabot verification checks by category.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit codes: 0 passed, 1 a check failed, 2 a required check did not run, 64 usage error.",
    )
    parser.add_argument("--list", action="store_true", help="list categories and checks, then exit")
    parser.add_argument("--category", action="append", default=[], help="category to verify (repeatable)")
    parser.add_argument(
        "--changed-from",
        metavar="REF",
        help="select categories from paths changed since REF (default without --category or --path: the base ref)",
    )
    parser.add_argument("--path", action="append", default=[], help="select categories from a path (repeatable)")
    parser.add_argument(
        "--tier", choices=("cpu", "environment", "all"), default="cpu", help="tier to run (default: cpu)"
    )
    parser.add_argument("--environment", metavar="NAME", help="environment bundle name for the environment tier")
    parser.add_argument("--bundle-dir", metavar="DIR", help="explicit environment bundle directory")
    parser.add_argument("--base", metavar="REF", help="base ref for comparisons (default: manifest base_ref)")
    parser.add_argument("--include-optional", action="store_true", help="also run optional checks")
    parser.add_argument("--skip-baseline", action="store_true", help="skip the baseline category")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without running or submitting anything")
    parser.add_argument("--output-dir", metavar="DIR", help="run directory (default: logs/dependabot/<run-id>)")
    parser.add_argument("--json", action="store_true", help="print the summary JSON at the end")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    executor: Executor = default_executor,
    repo_root: Path = REPO_ROOT,
    manifest_path: Path = MANIFEST_PATH,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    env = dict(os.environ if environ is None else environ)
    try:
        manifest = load_manifest(manifest_path)
        if args.list:
            print(format_listing(manifest))
            return EXIT_PASSED
        base_ref = args.base or manifest.base_ref

        unmapped: list[str] = []
        category_ids = list(args.category)
        paths = list(args.path)
        if args.changed_from or (not category_ids and not paths):
            paths += changed_paths(args.changed_from or base_ref, repo_root)
        routed, unmapped = route_paths(paths, manifest)
        category_ids += [category_id for category_id in routed if category_id not in category_ids]
        for category_id in category_ids:
            manifest.category(category_id)

        selected, skipped, categories = select_checks(
            manifest,
            category_ids,
            tier=args.tier,
            include_optional=args.include_optional,
            include_baseline=not args.skip_baseline,
        )
        needs_environment = any(check.tier == "environment" for check in selected)
        if (
            needs_environment
            and not args.environment
            and not (env.get("AZURE_RESOURCE_GROUP") and env.get("AZUREML_WORKSPACE_NAME"))
        ):
            raise UsageError("The environment tier needs --environment <name> or exported Azure ML variables")
        run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
        run_dir = resolve_output_dir(args.output_dir, run_id, repo_root) if not args.dry_run else Path("<run-dir>")
        if not args.dry_run:
            try:
                make_private_dir(run_dir)
            except OSError as error:
                raise UsageError(f"Cannot make the run directory private: {error}") from error
    except (ManifestError, UsageError) as error:
        print(f"verify:dependabot: {error}", file=sys.stderr)
        return EXIT_USAGE

    values = {"base_ref": base_ref, "run_dir": str(run_dir)}
    print(f"Categories: {', '.join(category.id for category in categories) or 'none'}")
    if unmapped:
        print(f"Paths outside every category: {', '.join(unmapped)}")
    available_environment = sum(
        1 for category in categories for check in category.checks if check.tier == "environment"
    )
    if args.tier == "cpu" and available_environment:
        print(f"{available_environment} environment check(s) available: add --tier environment --environment <name>")

    # A shared setup step runs once; every category that needs it owns its outcome.
    setup_steps: list[tuple[SetupStep, list[Category]]] = []
    for category in categories:
        if any(check.tier == "cpu" and check in selected for check in category.checks):
            for step in category.setup:
                owners = next(
                    (owners for seen, owners in setup_steps if (seen.command, seen.cwd) == (step.command, step.cwd)),
                    None,
                )
                if owners is None:
                    setup_steps.append((step, [category]))
                else:
                    owners.append(category)

    if args.dry_run:
        for step, owners in setup_steps:
            print(f"setup  {step.cwd}: {' '.join(step.command)} (for {', '.join(owner.id for owner in owners)})")
        for check in selected:
            argv_preview = (
                pytest_argv(check, run_dir / f"{check.id}.xml") if check.pytest else substitute(check.command, values)
            )
            if check.container and not host_is_linux_amd64() and not check.pytest:
                argv_preview = container_argv(check, argv_preview, repo_root)
            print(f"{check.tier:<11} {check.id}: {' '.join(argv_preview)}")
            instance_type, instance_type_problem = instance_type_note(check, repo_root, env)
            if instance_type or instance_type_problem:
                print(f"{'':<11} {check.id}: instance type {instance_type or instance_type_problem}")
        for check in skipped:
            print(f"optional {check.id}: skipped (add --include-optional)")
        return EXIT_PASSED

    if args.environment or args.bundle_dir:
        if args.bundle_dir:
            env[BUNDLE_DIR_VAR] = args.bundle_dir
        if args.environment:
            env[ENVIRONMENT_VAR] = args.environment
    environment_problem = environment_preflight(env, args.environment, repo_root) if needs_environment else None

    # Mirror scripts/lib/common.sh, which sources the untracked .env.local over the environment:
    # a declared variable the file assigns takes the file's value, and each check gets only its own.
    local_env: dict[str, str] = {}
    local_env_error: str | None = None
    try:
        local_env = read_local_env(repo_root, {name for check in selected for name in check.env})
    except LocalEnvError as error:
        local_env_error = str(error)

    commit, dirty = git_state(repo_root)
    results: list[CheckResult] = []
    failed_setup: set[str] = set()
    for step, owners in setup_steps:
        owner_ids = [owner.id for owner in owners]
        log_path = run_dir / f"setup-{owner_ids[0]}.log"
        code = executor(ExecutionRequest(step.command, repo_root / step.cwd, env, log_path))
        print(f"setup  {', '.join(owner_ids)}: {'ok' if code == 0 else f'failed (exit {code}, {log_path})'}")
        if code != 0:
            failed_setup.update(owner_ids)

    for check in selected:
        overrides = {name: local_env[name] for name in check.env if name in local_env}
        check_env = {**env, **overrides}
        empty = [name for name, value in overrides.items() if not value.strip()]
        problem = local_env_error or (
            f"{', '.join(empty)} {'is' if len(empty) == 1 else 'are'} empty in {LOCAL_ENV_FILE}, "
            "which overrides the environment"
            if empty
            else None
        )
        reason = preflight_reason(check, check_env, local_env_problem=problem)
        if check.tier == "environment" and environment_problem:
            reason = environment_problem
        instance_type, instance_type_problem = instance_type_note(check, repo_root, env)
        reason = reason or instance_type_problem
        if check.category in failed_setup:
            result = CheckResult(check.id, check.category, check.tier, check.gpu, check.optional, "failed")
            result.reason = "category setup failed"
        elif reason:
            result = CheckResult(
                check.id, check.category, check.tier, check.gpu, check.optional, "not-run", reason=reason
            )
        else:
            print(f"run    {check.id} (log: {display_path(run_dir / f'{check.id}.log', repo_root)})", flush=True)
            if instance_type:
                print(f"       {check.id}: instance type {instance_type}", flush=True)
            result = run_check(
                check, repo_root=repo_root, run_dir=run_dir, env=check_env, values=values, executor=executor
            )
        print(f"{result.status:<7} {check.id}{f' ({result.reason})' if result.reason else ''}", flush=True)
        results.append(result)
    for check in skipped:
        results.append(
            CheckResult(check.id, check.category, check.tier, check.gpu, check.optional, "skipped", reason="optional")
        )

    outcome, exit_code = overall_result(results)
    summary: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "commit": commit,
        "dirty": dirty,
        "tier": args.tier,
        "environment": args.environment,
        "categories": [category.id for category in categories],
        "unmapped_paths": unmapped,
        "checks": [result.__dict__ for result in results],
        "result": outcome,
    }
    write_summary(run_dir, summary)
    print(f"Result: {outcome} (summary: {run_dir / 'summary.md'})")
    if args.json:
        print(json.dumps(summary, indent=2))
    return exit_code


def environment_preflight(env: Mapping[str, str], environment: str | None, repo_root: Path) -> str | None:
    """Return why environment checks can't run, or None when the environment looks usable."""
    if shutil.which("az") is None:
        return "missing tools: az"
    expected = env.get("AZURE_SUBSCRIPTION_ID", "")
    if environment:
        try:
            bundle = load_environment_bundle(environment, repo_root, env)
        except EnvironmentBundleError as error:
            return str(error)
        expected = expected or bundle.values.get("subscription_id", "")
    active = subprocess.run(
        ["az", "account", "show", "--query", "id", "-o", "tsv"], capture_output=True, text=True, check=False
    )
    if active.returncode != 0:
        return "Azure CLI is not signed in (run az login)"
    if expected and active.stdout.strip() != expected:
        return "the active Azure CLI subscription does not match the environment"
    return None


if __name__ == "__main__":
    sys.exit(main())
