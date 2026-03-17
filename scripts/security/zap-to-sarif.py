#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Convert ZAP JSON report to SARIF 2.1.0 format for GitHub Code Scanning."""

import json
import sys
from pathlib import Path

SARIF_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"

LEVEL_MAP = {
    "0": "none",  # Informational
    "1": "note",  # Low
    "2": "warning",  # Medium
    "3": "error",  # High
}


def convert(zap_json: dict) -> dict:
    rules = []
    results = []
    rule_ids_seen = set()

    for site in zap_json.get("site", []):
        for alert in site.get("alerts", []):
            rule_id = f"ZAP-{alert['pluginid']}"
            if rule_id not in rule_ids_seen:
                rule_ids_seen.add(rule_id)
                rules.append(
                    {
                        "id": rule_id,
                        "name": alert.get("name", ""),
                        "shortDescription": {"text": alert.get("name", "")},
                        "fullDescription": {"text": alert.get("desc", "").strip()},
                        "helpUri": alert.get("reference", ""),
                        "properties": {"tags": ["security", "DAST"]},
                    }
                )

            for instance in alert.get("instances", []):
                results.append(
                    {
                        "ruleId": rule_id,
                        "level": LEVEL_MAP.get(str(alert.get("riskcode", "0")), "warning"),
                        "message": {"text": alert.get("solution", alert.get("name", ""))},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": instance.get("uri", "")},
                                }
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


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <zap-report.json> <output.sarif>", file=sys.stderr)
        sys.exit(2)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    try:
        zap_data = json.loads(input_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Error reading ZAP report '{input_path}': {exc}", file=sys.stderr)
        sys.exit(1)

    sarif_data = convert(zap_data)
    output_path.write_text(json.dumps(sarif_data, indent=2))


if __name__ == "__main__":
    main()
