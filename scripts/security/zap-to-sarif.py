#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Convert ZAP JSON report to SARIF 2.1.0 format for GitHub Code Scanning."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SARIF_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
_SCAN_CONTEXT_URI = ".github/workflows/dast-zap-scan.yml"

LEVEL_MAP = {
    "0": "none",  # Informational
    "1": "note",  # Low
    "2": "warning",  # Medium
    "3": "error",  # High
}


class _PlainTextParser(HTMLParser):
    """Extract text from ZAP's HTML report fields."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str) -> str:
    parser = _PlainTextParser()
    parser.feed(value)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def _help_uri(reference: str | None) -> str | None:
    """Accept a single HTTP(S) documentation URI, not HTML or reference lists."""
    if not isinstance(reference, str):
        return None
    uri = reference.strip()
    if not uri or re.search(r'[\s<>"\\{}|^`]|[^\x21-\x7e]|%(?![0-9a-fA-F]{2})', uri):
        return None
    try:
        parsed = urlsplit(uri)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
            return None
        if re.search(r"[\[\]#]", parsed.path + parsed.query + parsed.fragment):
            return None
        # Accessing port also validates its syntax and range.
        _ = parsed.port
    except ValueError:
        return None
    return uri


def convert(zap_json: dict[str, Any]) -> dict[str, Any]:
    """Preserve endpoint findings using a labeled repository scan-context location."""
    rules = []
    results = []
    rule_ids_seen = set()

    for site in zap_json.get("site", []):
        for alert in site.get("alerts", []):
            rule_id = f"ZAP-{alert['pluginid']}"
            name = _plain_text(alert.get("name", "")) or rule_id
            description = _plain_text(alert.get("desc", "")) or name
            solution = _plain_text(alert.get("solution", "")) or description
            if rule_id not in rule_ids_seen:
                rule_ids_seen.add(rule_id)
                rule = {
                    "id": rule_id,
                    "name": name,
                    "shortDescription": {"text": name},
                    "fullDescription": {"text": description},
                    "help": {"text": solution},
                    "properties": {"tags": ["security", "DAST"]},
                }
                help_uri = _help_uri(alert.get("reference", ""))
                if help_uri is not None:
                    rule["helpUri"] = help_uri
                rules.append(rule)

            for instance in alert.get("instances", []):
                uri = instance.get("uri", "")
                if not isinstance(uri, str) or not uri.strip():
                    raise ValueError("ZAP instance must contain a nonempty URI")
                method = instance.get("method", "")
                param = instance.get("param", "")
                identity = json.dumps([rule_id, uri, method, param], separators=(",", ":"))
                fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                level = LEVEL_MAP.get(str(alert.get("riskcode", "0")), "warning")
                results.append(
                    {
                        "ruleId": rule_id,
                        "level": level,
                        "kind": "informational" if level == "none" else "fail",
                        "message": {"text": f"{name} at {uri}\n\n{solution}"},
                        # GitHub only consumes this key; endpoint identity distinguishes the shared context location.
                        "partialFingerprints": {"primaryLocationLineHash": fingerprint},
                        "properties": {"uri": uri, "method": method, "param": param},
                        "locations": [
                            {
                                "message": {
                                    "text": "DAST scan context only; the finding applies to the HTTP endpoint, "
                                    "not this source line."
                                },
                                "physicalLocation": {
                                    "artifactLocation": {"uri": _SCAN_CONTEXT_URI},
                                    "region": {"startLine": 1, "startColumn": 1, "endLine": 1, "endColumn": 2},
                                },
                            }
                        ],
                    }
                )

    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "OWASP ZAP",
                        "informationUri": "https://www.zaproxy.org/",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <zap-report.json> <output.sarif>", file=sys.stderr)
        return 2

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    try:
        zap_data = json.loads(input_path.read_text(encoding="utf-8"))
        sarif_data = convert(zap_data)
        output_path.write_text(json.dumps(sarif_data, indent=2), encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"Error converting ZAP report '{input_path}': {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
