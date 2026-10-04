"""Consistency tests that keep the Dependabot category manifest honest.

They fail fast when ``.github/dependabot.yml`` gains or loses an entry, when a referenced
script, npm script, pytest node, or CI lane disappears, when an environment check
points anywhere other than the allowlisted Azure ML tests and the read-only Terraform
comparison, or when the README and contributor guide drift from the category list.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from tests.dependabot.verify import MANIFEST_PATH, REPO_ROOT, ManifestError, load_manifest, parse_manifest, route_paths

MANIFEST = load_manifest()
RAW_MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
ALL_CHECKS = {check.id: check for category in MANIFEST.categories for check in category.checks}

ENVIRONMENT_ALLOWLIST = {
    "aml-gpu-smoke",
    "aml-rl-lifecycle",
    "aml-il-lifecycle",
    "aml-il-pipeline-diffusion",
    "aml-il-pipeline-register",
    "aml-vla-pi0",
    "aml-osmo-proxy",
    "terraform-plan-compare",
    "terraform-plan-compare-vpn",
    "terraform-plan-compare-automation",
    "terraform-plan-compare-dns",
}
OPTIONAL_ENVIRONMENT_CHECKS = {"aml-osmo-proxy"}
COMPARE_PLANS = "infrastructure/terraform/scripts/compare-plans.sh"
SCRIPT_SUFFIXES = {".sh", ".ps1", ".py", ".mjs"}
CATEGORY_DOCS = (
    REPO_ROOT / "tests" / "dependabot" / "README.md",
    REPO_ROOT / "docs" / "contributing" / "dependabot-verification.md",
)
CATEGORY_SECTION = re.compile(r"^## [^\n]*\bCategories\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
CATEGORY_ROW = re.compile(r"^\|\s*`([a-z0-9-]+)`\s*\|", re.MULTILINE)
CATEGORY_OPTION = re.compile(r"--category[ =]([a-z0-9-]+)")


def _dependabot_entries() -> set[tuple[str, str]]:
    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    return {(update["package-ecosystem"], update["directory"]) for update in config["updates"]}


def _npm_scripts(package_dir: Path) -> set[str]:
    return set(json.loads((package_dir / "package.json").read_text(encoding="utf-8")).get("scripts", {}))


def _workspace_dir(name: str) -> Path:
    root = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    for workspace in root.get("workspaces", []):
        manifest = json.loads((REPO_ROOT / workspace / "package.json").read_text(encoding="utf-8"))
        if manifest.get("name") == name:
            return REPO_ROOT / workspace
    raise AssertionError(f"npm workspace {name!r} not found")


def test_manifest_has_thirteen_unique_categories() -> None:
    ids = [category.id for category in MANIFEST.categories]

    assert len(ids) == 13
    assert len(set(ids)) == 13
    assert [category.id for category in MANIFEST.categories if category.always] == ["baseline"]


def test_every_dependabot_entry_maps_to_a_category() -> None:
    mapped = {entry for category in MANIFEST.categories for entry in category.dependabot}

    assert mapped == _dependabot_entries()


def test_category_paths_route_their_dependabot_directories() -> None:
    for category in MANIFEST.categories:
        for _, directory in category.dependabot:
            if directory == "/":
                continue
            relative = directory.strip("/") + "/"
            assert any(relative.startswith(prefix) for prefix in category.paths), (category.id, directory)
            routed, _ = route_paths([relative + "manifest"], MANIFEST)
            assert category.id in routed, (category.id, directory)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("package.json", {"tooling"}),
        ("package-lock.json", {"tooling", "dataviewer"}),
        ("pyproject.toml", {"tooling"}),
        ("uv.lock", {"tooling"}),
        (".github/workflows/smoke-cpu.yml", {"tooling"}),
    ],
)
def test_root_routes_cover_root_level_entries(path: str, expected: set[str]) -> None:
    routed, unmapped = route_paths([path], MANIFEST)

    assert set(routed) == expected
    assert unmapped == []


def test_shared_check_ids_must_repeat_identically() -> None:
    mutated = json.loads(json.dumps(RAW_MANIFEST))
    for category in mutated["categories"]:
        for check in category["checks"]:
            if check["id"] == "training-tests" and category["id"] == "il":
                check["description"] = "drifted"

    with pytest.raises(ManifestError, match="conflicting definitions"):
        parse_manifest(mutated)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda check: check.pop("description"), "description"),
        (lambda check: check.update(tier="gpu"), "tier"),
        (lambda check: check.update(pytest="tests/e2e/x.py::test_x"), "exactly one"),
        (lambda check: check.update(unknown=True), "unknown keys"),
    ],
    ids=["missing-description", "bad-tier", "command-and-pytest", "unknown-key"],
)
def test_malformed_checks_are_rejected(mutation: Callable[[dict[str, object]], object], message: str) -> None:
    mutated = json.loads(json.dumps(RAW_MANIFEST))
    check = mutated["categories"][0]["checks"][0]
    mutation(check)

    with pytest.raises(ManifestError, match=message):
        parse_manifest(mutated)


def test_command_scripts_and_npm_scripts_exist() -> None:
    for check in ALL_CHECKS.values():
        argv = list(check.command)
        if not argv:
            continue
        cwd = REPO_ROOT / check.cwd
        for arg in argv:
            candidate = REPO_ROOT / arg
            if "/" in arg and "{" not in arg and candidate.suffix in SCRIPT_SUFFIXES:
                assert candidate.is_file(), (check.id, arg)
        if argv[:2] == ["npm", "run"]:
            package_dir = _workspace_dir(argv[argv.index("--workspace") + 1]) if "--workspace" in argv else cwd
            assert argv[2] in _npm_scripts(package_dir), (check.id, argv[2])
        if argv[0] == "shared/ci/smoke-image.sh":
            smoke_image = (REPO_ROOT / "shared" / "ci" / "smoke-image.sh").read_text(encoding="utf-8")
            assert re.search(rf"\b{re.escape(argv[1])}\b", smoke_image), (check.id, argv[1])


def test_snapshot_and_setup_paths_exist() -> None:
    for check in ALL_CHECKS.values():
        for path in check.snapshot:
            assert (REPO_ROOT / path).exists(), (check.id, path)
        assert (REPO_ROOT / check.cwd).is_dir(), (check.id, check.cwd)
    for category in MANIFEST.categories:
        for step in category.setup:
            assert (REPO_ROOT / step.cwd).is_dir(), (category.id, step.cwd)


def _decorator_names(function: ast.FunctionDef) -> set[str]:
    return {ast.unparse(decorator) for decorator in function.decorator_list}


def test_pytest_nodes_exist_down_to_parameter_ids() -> None:
    for check in ALL_CHECKS.values():
        if not check.pytest:
            continue
        module_path, node = check.pytest.split("::", 1)
        function_name, _, parameter = node.partition("[")
        source = (REPO_ROOT / module_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {item.name: item for item in tree.body if isinstance(item, ast.FunctionDef)}
        assert function_name in functions, check.id
        assert "pytest.mark.e2e" in _decorator_names(functions[function_name]), check.id
        if parameter:
            parameter_id = parameter.rstrip("]")
            literals = {item.value for item in ast.walk(tree) if isinstance(item, ast.Constant)}
            assert parameter_id in literals, (check.id, parameter_id)


def test_ci_lanes_exist_in_the_ci_contract() -> None:
    contract = json.loads((REPO_ROOT / "scripts" / "ci" / "ci-contract.json").read_text(encoding="utf-8"))
    lanes = {lane["id"] for lane in contract["lanes"]}

    for check in ALL_CHECKS.values():
        assert set(check.ci_lanes) <= lanes, (check.id, set(check.ci_lanes) - lanes)


def test_environment_checks_are_allowlisted_azure_ml_or_terraform_plans() -> None:
    environment_checks = {check_id: check for check_id, check in ALL_CHECKS.items() if check.tier == "environment"}

    assert set(environment_checks) == ENVIRONMENT_ALLOWLIST
    for check_id, check in environment_checks.items():
        if check.pytest:
            assert check.pytest.startswith("tests/e2e/test_e2e_aml_"), check_id
        else:
            assert check.command[:2] == ("bash", COMPARE_PLANS), check_id
            assert check.gpu is False, check_id
    for check_id in OPTIONAL_ENVIRONMENT_CHECKS:
        assert environment_checks[check_id].optional, check_id


def test_cpu_checks_never_submit_jobs() -> None:
    for check in ALL_CHECKS.values():
        if check.tier == "cpu":
            assert not check.pytest, check.id
            assert check.gpu is False, check.id


@pytest.mark.parametrize("doc", CATEGORY_DOCS, ids=lambda path: path.name)
def test_docs_list_every_category_in_manifest_order(doc: Path) -> None:
    section = CATEGORY_SECTION.search(doc.read_text(encoding="utf-8"))

    assert section, f"{doc.name} has no Categories section"
    assert CATEGORY_ROW.findall(section.group(1)) == [category.id for category in MANIFEST.categories]


@pytest.mark.parametrize("doc", CATEGORY_DOCS, ids=lambda path: path.name)
def test_docs_name_only_manifest_categories(doc: Path) -> None:
    named = set(CATEGORY_OPTION.findall(doc.read_text(encoding="utf-8")))

    assert named, f"{doc.name} shows no --category example"
    assert named <= {category.id for category in MANIFEST.categories}, sorted(named)
