from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sysconfig
from pathlib import Path


def _normalize_package_name(name: str) -> str:
    return name.lower().replace("_", "-")


def _expected_versions(requirements_file: Path) -> dict[str, str]:
    expected = {}
    for line in requirements_file.read_text(encoding="utf-8").splitlines():
        requirement = line.strip()
        if not requirement or requirement.startswith(("#", "-")):
            continue
        name, separator, version = requirement.partition("==")
        if separator:
            expected[_normalize_package_name(name)] = version.split(";", 1)[0].strip()
    return expected


def _installed_versions(expected: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    actual = {}
    missing = []
    install_roots = {
        Path(path).resolve()
        for name in ("purelib", "platlib")
        if (path := sysconfig.get_path(name)) is not None
    }
    for name in expected:
        distributions = list(importlib.metadata.distributions(name=name))
        installed = next(
            (
                distribution
                for distribution in distributions
                if any(Path(distribution.locate_file("")).resolve().is_relative_to(root) for root in install_roots)
            ),
            distributions[0] if distributions else None,
        )
        if installed is None:
            missing.append(name)
        else:
            actual[name] = installed.version
    return actual, missing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("requirements_file", type=Path)
    args = parser.parse_args()

    expected = _expected_versions(args.requirements_file)
    actual, missing = _installed_versions(expected)
    provenance = {
        "actual": actual,
        "expected": expected,
        "install_mode": "reinstall_from_frozen_lock",
        "lock_sha256": hashlib.sha256((args.project_dir / "uv.lock").read_bytes()).hexdigest(),
        "missing": missing,
    }
    print("RUNTIME_PROVENANCE=" + json.dumps(provenance, sort_keys=True))


if __name__ == "__main__":
    main()
