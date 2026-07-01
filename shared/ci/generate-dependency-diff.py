#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Generate a markdown dependency diff from current and previous SPDX SBOMs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_packages(path: Path) -> dict[str, str]:
    """Return package names and versions from an SPDX SBOM."""
    document = json.loads(path.read_text())
    packages = {}
    for package in document.get("packages", []):
        name = package.get("name", "UNKNOWN")
        version = package.get("versionInfo", "unknown")
        packages[name] = version
    return packages


def write_dependency_diff(current_path: Path, previous_path: Path, output_path: Path) -> tuple[int, int, int]:
    """Write a markdown dependency diff and return added, removed, and changed counts."""
    current = load_packages(current_path)
    previous = load_packages(previous_path)
    all_names = sorted(set(current) | set(previous))

    added = []
    removed = []
    changed = []
    for name in all_names:
        current_version = current.get(name)
        previous_version = previous.get(name)
        if current_version and not previous_version:
            added.append(f"| {name} | {current_version} |")
        elif previous_version and not current_version:
            removed.append(f"| {name} | {previous_version} |")
        elif current_version != previous_version:
            changed.append(f"| {name} | {previous_version} | {current_version} |")

    lines = ["# Dependency Diff\n"]
    if added:
        lines += ["\n## Added\n", "| Package | Version |", "| --- | --- |", *added]
    if removed:
        lines += ["\n## Removed\n", "| Package | Version |", "| --- | --- |", *removed]
    if changed:
        lines += ["\n## Changed\n", "| Package | Previous | Current |", "| --- | --- | --- |", *changed]
    if not (added or removed or changed):
        lines.append("\nNo dependency changes detected.")

    output_path.write_text("\n".join(lines) + "\n")
    return len(added), len(removed), len(changed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a markdown dependency diff from SPDX SBOMs.")
    parser.add_argument("current", type=Path, help="Current release SPDX SBOM path.")
    parser.add_argument("previous", type=Path, help="Previous release SPDX SBOM path.")
    parser.add_argument("output", type=Path, help="Markdown output path.")
    args = parser.parse_args()

    added, removed, changed = write_dependency_diff(args.current, args.previous, args.output)
    print(f"Diff: {added} added, {removed} removed, {changed} changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
