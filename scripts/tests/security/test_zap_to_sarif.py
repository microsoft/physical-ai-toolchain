# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
"""Regression tests for GitHub-compatible ZAP SARIF conversion."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts/security/zap-to-sarif.py"


def _load_converter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("zap_to_sarif", _SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONVERTER = _load_converter()


@pytest.fixture
def report() -> dict[str, Any]:
    return {
        "site": [
            {
                "alerts": [
                    {
                        "pluginid": "10038",
                        "name": "Missing CSP",
                        "riskcode": "2",
                        "desc": "<p>A policy is missing.</p>",
                        "solution": "<p>Set a policy.</p><p>Check &amp; verify.</p>",
                        "reference": "https://example.com/csp",
                        "instances": [{"uri": "http://localhost:5173/", "method": "GET", "param": ""}],
                    }
                ]
            }
        ]
    }


class TestLocations:
    @pytest.mark.parametrize("uri", ["http://localhost:5173/", "https://example.com/api/items?q=one%20two"])
    def test_given_endpoint_when_converted_then_uses_labeled_repository_context(
        self, report: dict[str, Any], uri: str
    ) -> None:
        report["site"][0]["alerts"][0]["instances"][0]["uri"] = uri

        result = _CONVERTER.convert(report)["runs"][0]["results"][0]

        location = result["locations"][0]
        physical = location["physicalLocation"]
        assert physical["artifactLocation"]["uri"] == ".github/workflows/dast-zap-scan.yml"
        lines = (_ROOT / physical["artifactLocation"]["uri"]).read_text(encoding="utf-8").splitlines()
        region = physical["region"]
        assert 1 <= region["startLine"] <= region["endLine"] <= len(lines)
        assert 1 <= region["startColumn"] < region["endColumn"] <= len(lines[region["endLine"] - 1]) + 1
        assert "scan context" in location["message"]["text"]
        assert "not this source line" in location["message"]["text"]
        assert uri in result["message"]["text"]
        assert result["properties"] == {"uri": uri, "method": "GET", "param": ""}

    @pytest.mark.parametrize("uri", [None, "", " \t", 42])
    def test_given_missing_endpoint_when_converted_then_rejects_empty_identity(
        self, report: dict[str, Any], uri: Any
    ) -> None:
        report["site"][0]["alerts"][0]["instances"][0]["uri"] = uri

        with pytest.raises(ValueError, match="nonempty URI"):
            _CONVERTER.convert(report)


class TestReferences:
    @pytest.mark.parametrize(
        "reference",
        [
            None,
            "",
            "   ",
            "<p>https://example.com/help</p>",
            '<a href="https://example.com/help">Help</a>',
            "<p>https://example.com/one</p><p>https://example.com/two</p>",
            "https://example.com/one https://example.com/two",
            "https://example.com/one\nhttps://example.com/two",
            "https://example.com/a\tb",
            "https://example.com/a\x00b",
            "/relative/help",
            "javascript:alert(1)",
            "https:///missing-host",
            "https://[invalid/",
            "https://example.com:bad/help",
            "https://example.com:65536/help",
            "https://user@example.com/help",
            "https://example.com/%ZZ",
            "https://example.com/a\\b",
            "https://example.com/{bad}",
            "https://example.com/café",
            "https://example.com/a[b]",
            "https://example.com/?q=[bad]",
            "https://example.com/#one#two",
            "https://[::1]extra/help",
        ],
    )
    def test_given_unsuitable_reference_when_converted_then_omits_help_uri(
        self, report: dict[str, Any], reference: str | None
    ) -> None:
        report["site"][0]["alerts"][0]["reference"] = reference

        rule = _CONVERTER.convert(report)["runs"][0]["tool"]["driver"]["rules"][0]

        assert "helpUri" not in rule
        assert rule["help"]["text"]

    @pytest.mark.parametrize(
        "reference",
        [
            "https://example.com/help",
            " http://example.com/help?q=a%20b#section ",
            "https://[::1]:443/help",
            "https://example.com/a%5Bb%5D",
        ],
    )
    def test_given_single_reference_when_converted_then_retains_uri(
        self, report: dict[str, Any], reference: str
    ) -> None:
        report["site"][0]["alerts"][0]["reference"] = reference

        rule = _CONVERTER.convert(report)["runs"][0]["tool"]["driver"]["rules"][0]

        assert rule["helpUri"] == reference.strip()


class TestFingerprints:
    def test_given_same_report_when_repeated_then_is_deterministic(self, report: dict[str, Any]) -> None:
        original = copy.deepcopy(report)

        first = _CONVERTER.convert(report)
        second = _CONVERTER.convert(report)

        assert first == second
        assert report == original
        fingerprint = first["runs"][0]["results"][0]["partialFingerprints"]["primaryLocationLineHash"]
        assert len(fingerprint) == 64
        assert int(fingerprint, 16) >= 0

    @pytest.mark.parametrize("field", ["uri", "method", "param", "pluginid"])
    def test_given_distinct_identity_when_converted_then_hashes_differ(
        self, report: dict[str, Any], field: str
    ) -> None:
        other = copy.deepcopy(report)
        alert = other["site"][0]["alerts"][0]
        target = alert if field == "pluginid" else alert["instances"][0]
        target[field] += "different"

        first = _CONVERTER.convert(report)["runs"][0]["results"][0]
        second = _CONVERTER.convert(other)["runs"][0]["results"][0]

        assert first["partialFingerprints"] != second["partialFingerprints"]

    def test_given_reordered_findings_when_converted_then_identities_remain_stable(
        self, report: dict[str, Any]
    ) -> None:
        alerts = report["site"][0]["alerts"]
        alerts[0]["instances"].append({"uri": "http://localhost:5173/other", "method": "GET"})
        alerts.append({**copy.deepcopy(alerts[0]), "pluginid": "10020"})
        original = _CONVERTER.convert(report)["runs"][0]["results"]
        alerts.reverse()
        for alert in alerts:
            alert["instances"].reverse()
            alert.update(name="Renamed", desc="Changed description", solution="Changed guidance")
        report["generated"] = "a different run time"

        reordered = _CONVERTER.convert(report)["runs"][0]["results"]

        before = {(r["ruleId"], r["properties"]["uri"]): r["partialFingerprints"] for r in original}
        after = {(r["ruleId"], r["properties"]["uri"]): r["partialFingerprints"] for r in reordered}
        assert before == after
        assert len({r["primaryLocationLineHash"] for r in before.values()}) == 4


class TestReportSemantics:
    @pytest.mark.parametrize("report", [{}, {"site": []}, {"site": [{"alerts": []}]}])
    def test_given_empty_report_when_converted_then_emits_empty_run(self, report: dict[str, Any]) -> None:
        sarif = _CONVERTER.convert(report)

        assert sarif["version"] == "2.1.0"
        assert sarif["runs"][0]["results"] == []
        assert sarif["runs"][0]["tool"]["driver"]["name"] == "OWASP ZAP"

    @pytest.mark.parametrize(
        ("risk", "level", "kind"),
        [
            ("0", "none", "informational"),
            (0, "none", "informational"),
            ("1", "note", "fail"),
            ("2", "warning", "fail"),
            ("3", "error", "fail"),
            ("unknown", "warning", "fail"),
        ],
    )
    def test_given_severity_when_converted_then_kind_and_level_agree(
        self, report: dict[str, Any], risk: str | int, level: str, kind: str
    ) -> None:
        report["site"][0]["alerts"][0]["riskcode"] = risk

        results = _CONVERTER.convert(report)["runs"][0]["results"]

        assert len(results) == 1
        assert (results[0]["level"], results[0]["kind"]) == (level, kind)

    def test_given_html_when_converted_then_emits_plain_text(self, report: dict[str, Any]) -> None:
        run = _CONVERTER.convert(report)["runs"][0]

        rule = run["tool"]["driver"]["rules"][0]
        assert rule["fullDescription"]["text"] == "A policy is missing."
        assert rule["help"]["text"] == "Set a policy. Check & verify."
        assert "<p>" not in run["results"][0]["message"]["text"]

    def test_given_blank_metadata_when_converted_then_uses_nonempty_fallbacks(self, report: dict[str, Any]) -> None:
        report["site"][0]["alerts"][0].update(name="", desc="<p></p>", solution=" ", reference="")

        rule = _CONVERTER.convert(report)["runs"][0]["tool"]["driver"]["rules"][0]

        assert rule["shortDescription"]["text"] == "ZAP-10038"
        assert rule["fullDescription"]["text"] == "ZAP-10038"
        assert rule["help"]["text"] == "ZAP-10038"

    def test_given_same_rule_across_sites_when_converted_then_keeps_all_results(self, report: dict[str, Any]) -> None:
        report["site"].append(copy.deepcopy(report["site"][0]))
        report["site"][1]["alerts"][0]["instances"][0]["uri"] = "https://example.com/"

        run = _CONVERTER.convert(report)["runs"][0]

        assert len(run["tool"]["driver"]["rules"]) == 1
        assert len(run["results"]) == 2
        assert run["results"][0]["partialFingerprints"] != run["results"][1]["partialFingerprints"]


class TestMain:
    def test_given_utf8_report_when_run_then_writes_sarif(
        self, report: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        report["site"][0]["alerts"][0]["name"] = "Café policy"
        source, output = tmp_path / "input.json", tmp_path / "output.sarif"
        source.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", [str(_SCRIPT), str(source), str(output)])

        status = _CONVERTER.main()

        assert status == 0
        assert json.loads(output.read_text(encoding="utf-8")) == _CONVERTER.convert(report)

    @pytest.mark.parametrize("content", [None, "{bad json", "[]", '{"site":[{"alerts":[{}]}]}'])
    def test_given_unreadable_report_when_run_then_fails_clearly(
        self, content: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source, output = tmp_path / "input.json", tmp_path / "output.sarif"
        if content is not None:
            source.write_text(content, encoding="utf-8")
        monkeypatch.setattr(sys, "argv", [str(_SCRIPT), str(source), str(output)])

        status = _CONVERTER.main()

        assert status == 1
        assert "Error converting ZAP report" in capsys.readouterr().err
        assert not output.exists()

    def test_given_output_directory_when_run_then_returns_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = tmp_path / "input.json"
        source.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(sys, "argv", [str(_SCRIPT), str(source), str(tmp_path)])

        assert _CONVERTER.main() == 1
        assert "Error converting ZAP report" in capsys.readouterr().err

    def test_given_wrong_arguments_when_run_then_returns_usage_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", [str(_SCRIPT)])

        assert _CONVERTER.main() == 2
        assert "Usage:" in capsys.readouterr().err
