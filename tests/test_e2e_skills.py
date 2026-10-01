from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INFRASTRUCTURE_SKILL = REPO_ROOT / ".github" / "skills" / "e2e-infrastructure"
TESTS_SKILL = REPO_ROOT / ".github" / "skills" / "e2e-tests"
SCRIPTS = (
    INFRASTRUCTURE_SKILL / "scripts" / "manage-e2e-infrastructure.sh",
    TESTS_SKILL / "scripts" / "run-e2e-tests.sh",
)
INFRASTRUCTURE_TEMPLATE = INFRASTRUCTURE_SKILL / "templates" / "terraform.tfvars.example"


@pytest.mark.parametrize("script", SCRIPTS)
def test_e2e_skill_script_has_valid_bash_syntax(script: Path) -> None:
    subprocess.run(["bash", "-n", str(script)], check=True, cwd=REPO_ROOT)


@pytest.mark.parametrize("script", SCRIPTS)
def test_e2e_skill_script_exposes_help(script: Path) -> None:
    result = subprocess.run(
        ["bash", str(script), "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "Usage:" in result.stdout


@pytest.mark.parametrize("script", SCRIPTS)
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
