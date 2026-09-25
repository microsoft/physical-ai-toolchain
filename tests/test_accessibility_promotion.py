"""Behavior contracts for protected Docusaurus evidence promotion."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.accessibility import promotion
from scripts.accessibility.evidence_gate import _hve_canonical_digest

_ROOT = Path(__file__).resolve().parents[1]
_SHA = "a" * 40
_REVIEW_SHA = "b" * 40
_DIGEST = "c" * 64
_NOW = datetime(2026, 9, 25, tzinfo=UTC)
_REPO = "owner/project"


def _run() -> dict[str, Any]:
    return {
        "id": 123,
        "repository": {"full_name": _REPO},
        "head_repository": {"full_name": _REPO},
        "path": ".github/workflows/main.yml",
        "name": "CI",
        "event": "push",
        "head_branch": "main",
        "head_sha": _SHA,
        "status": "completed",
        "conclusion": "success",
    }


def _artifact() -> dict[str, Any]:
    return {
        "id": 456,
        "name": "docusaurus-accessibility-evidence-release-123",
        "expired": False,
        "expires_at": "2026-12-24T00:00:00Z",
        "workflow_run": {"id": 123, "head_sha": _SHA, "head_branch": "main"},
    }


def _write(path: Path, value: Any) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(data)
    return data


def _review_package(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    registry = {"schemaVersion": "1.0.0", "records": []}
    registry["digest"] = _hve_canonical_digest(registry, "hve-a11y:review-registry:v1")
    supplement = {
        "sourceRevision": _SHA,
        "buildDigest": _DIGEST,
        "configDigest": _DIGEST,
        "campaignId": "docusaurus-accessibility",
        "supplementId": "review-1",
    }
    files = []
    for name, value in (("review-registry.json", registry), ("supplements/review-1.json", supplement)):
        data = _write(root / name, value)
        files.append({"path": name, "sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    _write(root / "package-manifest.json", {"schemaVersion": "1.0.0", "sourceRevision": _SHA, "files": files})
    return registry, supplement


class TestRunIdentity:
    def test_subprocesses_disable_inherited_relative_node_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        environments = []
        monkeypatch.setenv("NODE_COMPILE_CACHE", "node-compile-cache")
        monkeypatch.setenv("NODE_DISABLE_COMPILE_CACHE", "0")

        def fake_run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            environments.append(kwargs.get("env", {}))
            return subprocess.CompletedProcess(arguments, 0, "verified", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert promotion._command(["git", "--version"]) == "verified"
        assert environments[0]["NODE_DISABLE_COMPILE_CACHE"] == "1"

    def test_accepts_only_current_main_dispatch(self) -> None:
        promotion.validate_dispatch("refs/heads/main", _SHA, _SHA)

    @pytest.mark.parametrize("ref,sha", [("refs/heads/topic", _SHA), ("refs/tags/main", _SHA),
                                         ("refs/heads/main", _REVIEW_SHA)])
    def test_rejects_untrusted_dispatch(self, ref: str, sha: str) -> None:
        with pytest.raises(ValueError):
            promotion.validate_dispatch(ref, sha, _SHA)

    def test_accepts_successful_collection(self) -> None:
        promotion.validate_run(_run(), repository=_REPO, run_id="123", current_main=_SHA)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("id", 124), ("repository", {"full_name": "fork/project"}),
            ("head_repository", {"full_name": "fork/project"}), ("path", ".github/workflows/other.yml"),
            ("name", "Untrusted collection"),
            ("event", "pull_request"), ("event", "workflow_dispatch"), ("head_branch", "topic"),
            ("head_sha", _REVIEW_SHA), ("conclusion", "failure"), ("status", "in_progress"),
        ],
    )
    def test_rejects_wrong_collection(self, field: str, value: Any) -> None:
        run = _run()
        run[field] = value
        with pytest.raises(ValueError):
            promotion.validate_run(run, repository=_REPO, run_id="123", current_main=_SHA)

    @pytest.mark.parametrize("run_id", ["../123", "-1", "00123", "0", "1\n", ""])
    def test_rejects_id_injection(self, run_id: str) -> None:
        with pytest.raises(ValueError):
            promotion.validate_run(_run(), repository=_REPO, run_id=run_id, current_main=_SHA)

    def test_accepts_immutable_artifact(self) -> None:
        promotion.validate_artifact(_artifact(), run_id="123", artifact_id="456", source_sha=_SHA, now=_NOW)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("id", 457), ("name", "docusaurus-accessibility-evidence-release-124"), ("expired", True),
            ("expires_at", "2026-09-24T00:00:00Z"), ("expires_at", "2026-09-25T00:00:00"),
            ("workflow_run", {"id": 124, "head_sha": _SHA, "head_branch": "main"}),
            ("workflow_run", {"id": 123, "head_sha": _REVIEW_SHA, "head_branch": "main"}),
        ],
    )
    def test_rejects_wrong_artifact(self, field: str, value: Any) -> None:
        artifact = _artifact()
        artifact[field] = value
        with pytest.raises(ValueError):
            promotion.validate_artifact(artifact, run_id="123", artifact_id="456", source_sha=_SHA, now=_NOW)

    @pytest.mark.parametrize("field,value", [("path", ".github/workflows/other.yml"), ("name", "Not CI")])
    def test_collection_artifact_lookup_requires_expected_producer(
        self, monkeypatch: pytest.MonkeyPatch, field: str, value: str,
    ) -> None:
        run = _run()
        run[field] = value
        calls = []

        def fake_get(_self: promotion.GitHub, route: str) -> dict[str, Any]:
            calls.append(route)
            if route == "git/ref/heads/main":
                return {"object": {"sha": _SHA}}
            if route == "actions/runs/123":
                return run
            raise AssertionError("Artifact lookup must not precede producer validation")

        monkeypatch.setattr(promotion.GitHub, "get", fake_get)
        result = promotion.main([
            "check-collection", "--repository", _REPO, "--run-id", "123", "--artifact-id", "456",
            "--dispatch-ref", "refs/heads/main", "--dispatch-sha", _SHA,
        ])
        assert result == 1
        assert not any(route.startswith("actions/artifacts/") for route in calls)


class TestReviewerBoundary:
    @pytest.mark.parametrize("changed", [False, True])
    def test_current_registry_must_match_promoted_anchor(
        self, monkeypatch: pytest.MonkeyPatch, changed: bool,
    ) -> None:
        registry = {"schemaVersion": "1.0.0", "records": [{"status": "current"}]}
        registry["digest"] = _hve_canonical_digest(registry, "hve-a11y:review-registry:v1")
        expected = registry["digest"]
        if changed:
            registry["records"][0]["status"] = "revoked"
            registry["digest"] = _hve_canonical_digest(
                {key: value for key, value in registry.items() if key != "digest"},
                "hve-a11y:review-registry:v1",
            )
        data = json.dumps(registry).encode()
        blob_sha = hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()
        responses = {
            "branches/review-evidence": {"name": "review-evidence", "protected": True, "commit": {"sha": _SHA}},
            f"compare/{_REVIEW_SHA}...{_SHA}": {"status": "ahead", "merge_base_commit": {"sha": _REVIEW_SHA}},
            f"git/commits/{_SHA}": {"sha": _SHA, "tree": {"sha": _SHA}},
            f"git/trees/{_SHA}?recursive=1": {
                "truncated": False,
                "tree": [{"path": f"reviewer-evidence/docusaurus/{_SHA}/review-registry.json",
                          "mode": "100644", "type": "blob", "sha": blob_sha, "size": len(data)}],
            },
            f"git/blobs/{blob_sha}": {
                "sha": blob_sha, "encoding": "base64", "content": promotion.base64.b64encode(data).decode(),
            },
        }
        monkeypatch.setattr(promotion.GitHub, "get", lambda _self, route: responses[route])
        arguments = {
            "branch_name": "review-evidence", "reviewer_revision": _REVIEW_SHA, "source_sha": _SHA,
            "promoted_digest": expected, "expected_digest": expected,
        }
        if changed:
            with pytest.raises(ValueError, match="registry"):
                promotion.verify_current_registry(promotion.GitHub(_REPO), **arguments)
        else:
            promotion.verify_current_registry(promotion.GitHub(_REPO), **arguments)

    def test_missing_current_registry_anchor_fails_before_remote_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(promotion.GitHub, "get", lambda *_: pytest.fail("Must reject missing anchor first"))
        with pytest.raises(ValueError):
            promotion.verify_current_registry(
                promotion.GitHub(_REPO), branch_name="review-evidence", reviewer_revision=_REVIEW_SHA,
                source_sha=_SHA, promoted_digest=_DIGEST, expected_digest="",
            )

    @pytest.mark.parametrize("status", ["ahead", "identical"])
    def test_candidate_is_ancestor_of_protected_head(self, status: str) -> None:
        promotion.validate_review_ref(
            {"name": "review-evidence", "protected": True, "commit": {"sha": _SHA}},
            {"status": status, "merge_base_commit": {"sha": _REVIEW_SHA}},
            branch_name="review-evidence", revision=_REVIEW_SHA,
        )

    @pytest.mark.parametrize(
        "protected,status,base",
        [(False, "ahead", _REVIEW_SHA), (True, "behind", _REVIEW_SHA),
         (True, "diverged", _REVIEW_SHA), (True, "ahead", _SHA)],
    )
    def test_rejects_unprotected_unknown_or_reverse_ancestry(
        self, protected: bool, status: str, base: str,
    ) -> None:
        with pytest.raises(ValueError):
            promotion.validate_review_ref(
                {"name": "review-evidence", "protected": protected, "commit": {"sha": _SHA}},
                {"status": status, "merge_base_commit": {"sha": base}},
                branch_name="review-evidence", revision=_REVIEW_SHA,
            )

    @pytest.mark.parametrize("name", ["", "../main", "refs/heads/main", "review~1", "review@{1}", "-main"])
    def test_rejects_unsafe_branch(self, name: str) -> None:
        with pytest.raises(ValueError):
            promotion.validate_branch_name(name)

    @pytest.mark.parametrize(
        "path,mode,kind",
        [("supplements/../review.json", "100644", "blob"), (".secret.json", "100644", "blob"),
         ("supplements/nested/review.json", "100644", "blob"), ("run.py", "100644", "blob"),
         ("supplements/link.json", "120000", "blob"), ("supplements/exec.json", "100755", "blob"),
         ("supplements/module.json", "160000", "commit"), ("supplements\\review.json", "100644", "blob")],
    )
    def test_rejects_unsafe_git_entry(self, path: str, mode: str, kind: str) -> None:
        with pytest.raises(ValueError):
            promotion.validate_review_entry({"path": path, "mode": mode, "type": kind})

    def test_accepts_inert_allowlist(self) -> None:
        for name in ("review-registry.json", "package-manifest.json", "supplements/review-1.json"):
            promotion.validate_review_entry({"path": name, "mode": "100644", "type": "blob"})

    def test_verifies_closed_reviewer_manifest(self, tmp_path: Path) -> None:
        registry, supplement = _review_package(tmp_path)
        actual_registry, supplements = promotion.verify_review_package(tmp_path, _SHA)
        assert actual_registry == registry
        assert supplements == [supplement]

    @pytest.mark.parametrize("mutation", ["extra", "missing", "digest", "size", "duplicate", "traversal", "source"])
    def test_rejects_review_manifest_tampering(self, tmp_path: Path, mutation: str) -> None:
        _review_package(tmp_path)
        manifest_path = tmp_path / "package-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        if mutation == "extra":
            _write(tmp_path / "extra.json", {})
        elif mutation == "missing":
            (tmp_path / "supplements" / "review-1.json").unlink()
        elif mutation == "digest":
            manifest["files"][0]["sha256"] = "0" * 64
        elif mutation == "size":
            manifest["files"][0]["sizeBytes"] += 1
        elif mutation == "duplicate":
            manifest["files"].append(deepcopy(manifest["files"][0]))
        elif mutation == "traversal":
            manifest["files"][0]["path"] = "../review-registry.json"
        else:
            manifest["sourceRevision"] = _REVIEW_SHA
        _write(manifest_path, manifest)
        with pytest.raises(ValueError):
            promotion.verify_review_package(tmp_path, _SHA)

    def test_rejects_duplicate_json_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text('{"digest":"one","digest":"two"}')
        with pytest.raises(ValueError):
            promotion.read_json(path)

    def test_rejects_missing_supplements(self, tmp_path: Path) -> None:
        _review_package(tmp_path)
        (tmp_path / "supplements" / "review-1.json").unlink()
        manifest = promotion.read_json(tmp_path / "package-manifest.json")
        manifest["files"] = manifest["files"][:1]
        _write(tmp_path / "package-manifest.json", manifest)
        with pytest.raises(ValueError):
            promotion.verify_review_package(tmp_path, _SHA)

    def test_rejects_symlink_even_when_bytes_match(self, tmp_path: Path) -> None:
        root = tmp_path / "review"
        _review_package(root)
        target = tmp_path / "registry.json"
        registry = root / "review-registry.json"
        registry.rename(target)
        registry.symlink_to(target)
        with pytest.raises(ValueError, match="symlink"):
            promotion.verify_review_package(root, _SHA)

    def test_does_not_fetch_blobs_from_truncated_or_unprotected_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = []

        def fake_get(_self: promotion.GitHub, route: str) -> dict[str, Any]:
            calls.append(route)
            return {"protected": False}

        monkeypatch.setattr(promotion.GitHub, "get", fake_get)
        with pytest.raises(ValueError, match="protection"):
            promotion.acquire_review(promotion.GitHub(_REPO), revision=_REVIEW_SHA,
                                     branch_name="review-evidence", source_sha=_SHA, output=tmp_path / "review")
        assert calls == ["branches/review-evidence"]
        assert not (tmp_path / "review").exists()

    @pytest.mark.parametrize("truncated,extra_mode", [(True, None), (False, "120000"), (False, "100755")])
    def test_rejects_git_tree_before_reading_reviewer_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, truncated: bool, extra_mode: str | None,
    ) -> None:
        responses = {
            "branches/review-evidence": {"name": "review-evidence", "protected": True, "commit": {"sha": _SHA}},
            f"compare/{_REVIEW_SHA}...{_SHA}": {"status": "ahead", "merge_base_commit": {"sha": _REVIEW_SHA}},
            f"git/commits/{_REVIEW_SHA}": {"sha": _REVIEW_SHA, "tree": {"sha": _SHA}},
            f"git/trees/{_SHA}?recursive=1": {
                "truncated": truncated,
                "tree": [{"path": f"reviewer-evidence/docusaurus/{_SHA}/review-registry.json",
                          "mode": extra_mode, "type": "blob", "sha": _SHA, "size": 2}],
            },
        }
        calls = []

        def fake_get(_self: promotion.GitHub, route: str) -> dict[str, Any]:
            calls.append(route)
            return responses[route]

        monkeypatch.setattr(promotion.GitHub, "get", fake_get)
        with pytest.raises(ValueError):
            promotion.acquire_review(promotion.GitHub(_REPO), revision=_REVIEW_SHA,
                                     branch_name="review-evidence", source_sha=_SHA, output=tmp_path / "review")
        assert not any(route.startswith("git/blobs/") for route in calls)
        assert not (tmp_path / "review").exists()


class TestDigestAnchors:
    def test_anchors_use_hve_domains_not_file_hashes(self, tmp_path: Path) -> None:
        registry, _ = _review_package(tmp_path)
        prior = {"schemaVersion": "1.0.0", "runManifest": {"sourceRevision": _SHA}}
        prior["bundleDigest"] = _hve_canonical_digest(prior, "hve-a11y:evidence-bundle:v1")
        promotion.validate_anchors(prior, registry, prior["bundleDigest"], registry["digest"])
        raw_digest = hashlib.sha256((tmp_path / "review-registry.json").read_bytes()).hexdigest()
        with pytest.raises(ValueError):
            promotion.validate_anchors(prior, registry, prior["bundleDigest"], raw_digest)

    @pytest.mark.parametrize("anchor", ["", "not-a-digest", "0" * 64])
    def test_missing_or_mismatched_anchor_blocks_before_rewrite(self, tmp_path: Path, anchor: str) -> None:
        registry, _ = _review_package(tmp_path)
        prior = {"bundleDigest": _DIGEST}
        with pytest.raises(ValueError):
            promotion.validate_anchors(prior, registry, anchor, registry["digest"])

    def test_release_must_be_complete(self) -> None:
        for state in ("pending", "incomplete", None):
            with pytest.raises(ValueError):
                promotion.require_release({"scopeCompleteness": {"releaseEvidence": state}})

    def test_refreshes_evaluation_not_observations(self) -> None:
        original = {
            "sourceRevision": _SHA, "composedAt": "2026-09-24T00:00:00Z", "buildDigest": _DIGEST,
            "harnessDigest": "d" * 64, "toolDigest": "e" * 64, "mappingDigest": "f" * 64,
            "lockfileDigest": "0" * 64, "runId": "original-run",
            "environment": {"operatingSystem": "collection-host", "inputModes": ["keyboard", "pointer"]},
        }
        updated = promotion.evaluation_context(original, now=_NOW)
        assert updated["composedAt"] == "2026-09-25T00:00:00Z"
        assert original["composedAt"] == "2026-09-24T00:00:00Z"
        assert updated == {**original, "composedAt": "2026-09-25T00:00:00Z"}

    def test_future_collection_time_rejected(self) -> None:
        with pytest.raises(ValueError):
            promotion.evaluation_context({"composedAt": "2026-09-26T00:00:00Z"}, now=_NOW)

    def test_recomposition_preserves_sources_and_prior_and_keeps_upstream_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        collection = tmp_path / "collection"
        metadata = collection / "artifacts/accessibility/docusaurus"
        review = tmp_path / "review"
        registry, _ = _review_package(review)
        context = {
            "sourceRevision": _SHA, "buildDigest": _DIGEST, "configDigest": _DIGEST,
            "fixtureDigest": _DIGEST, "campaignId": "docusaurus-accessibility",
            "composedAt": "2026-09-24T00:00:00Z",
        }
        prior = {"runManifest": context}
        prior["bundleDigest"] = _hve_canonical_digest(prior, "hve-a11y:evidence-bundle:v1")
        original = _write(metadata / "evidence-bundle.json", prior)
        _write(metadata / "inputs/run-context.json", context)
        observation = _write(metadata / "inputs/evidence-source.json", {"observedAt": context["composedAt"]})
        calls = []

        def failing_hve(arguments: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append(arguments)
            return subprocess.CompletedProcess(arguments, 7, "", "release incomplete")

        monkeypatch.setattr(subprocess, "run", failing_hve)
        with pytest.raises(ValueError, match="exit 7"):
            promotion._compose(
                collection, review, tmp_path / "output", harness_root=tmp_path / "hve",
                prior_digest=prior["bundleDigest"], registry_digest=registry["digest"], now=_NOW,
            )
        command = calls[0]
        assert command[command.index("--require-completeness") + 1] == "release"
        assert command[command.index("--prior-bundle") + 1] == str(metadata / "evidence-bundle.json")
        assert command[command.index("--prior-bundle-digest") + 1] == prior["bundleDigest"]
        assert command[command.index("--expected-review-registry-digest") + 1] == registry["digest"]
        assert (metadata / "evidence-bundle.json").read_bytes() == original
        assert (metadata / "inputs/evidence-source.json").read_bytes() == observation
        assert promotion.read_json(tmp_path / "output/run-context.json")["composedAt"] == "2026-09-25T00:00:00Z"
        assert not (tmp_path / "output/validation-manifest.json").exists()

    def test_forged_prior_anchor_creates_no_composition_output(self, tmp_path: Path) -> None:
        collection = tmp_path / "collection"
        review = tmp_path / "review"
        registry, _ = _review_package(review)
        _write(collection / "artifacts/accessibility/docusaurus/evidence-bundle.json",
               {"runManifest": {"sourceRevision": _SHA}, "bundleDigest": _DIGEST})
        with pytest.raises(ValueError, match="anchor"):
            promotion._compose(collection, review, tmp_path / "output", harness_root=tmp_path / "hve",
                               prior_digest=_DIGEST, registry_digest=registry["digest"], now=_NOW)
        assert not (tmp_path / "output").exists()


class TestDeploymentMetadata:
    @pytest.fixture()
    def documents(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        identity = {
            "sourceRevision": _SHA, "buildDigest": _DIGEST, "configDigest": _DIGEST,
            "fixtureDigest": _DIGEST, "campaignId": "docusaurus-accessibility",
            "harnessDigest": _DIGEST, "toolDigest": _DIGEST, "mappingDigest": _DIGEST,
            "lockfileDigest": _DIGEST, "runId": "original-run", "schemaVersion": "1.0.0",
            "environment": {"operatingSystem": "collection-host", "inputModes": ["keyboard", "pointer"]},
            "composedAt": "2026-09-24T00:00:00Z",
        }
        prior = {"bundleDigest": _DIGEST, "runManifest": identity}
        bundle = {
            "bundleDigest": "d" * 64,
            "runManifest": {**deepcopy(identity), "composedAt": "2026-09-25T00:00:00Z"},
            "composedAt": "2026-09-25T00:00:00Z", "scopeCompleteness": {"releaseEvidence": "complete"},
        }
        manifest = {
            "schemaVersion": "1.0.0", "sourceRevision": _SHA, "buildDigest": _DIGEST,
            "bundleDigest": bundle["bundleDigest"], "priorBundleDigest": _DIGEST,
            "reviewRegistryDigest": _DIGEST, "reviewerRevision": _REVIEW_SHA,
            "collectionRunId": "123", "collectionArtifactId": "456", "promotionRunId": "789",
            "hveRevision": promotion._HVE_REF, "hveTree": promotion._HVE_TREE,
            "releaseEvidence": "complete", "attestation": False, "evaluatedAt": bundle["composedAt"],
        }
        return manifest, prior, bundle

    def test_valid_metadata_binding_is_not_a_release_attestation(
        self, documents: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    ) -> None:
        promotion.validate_promotion_manifest(*documents, source_sha=_SHA, promotion_run_id="789")
        assert documents[0]["attestation"] is False

    @pytest.mark.parametrize(
        "field,value",
        [
            ("sourceRevision", _REVIEW_SHA), ("promotionRunId", "790"), ("buildDigest", "0" * 64),
            ("bundleDigest", "0" * 64), ("priorBundleDigest", "0" * 64), ("hveRevision", _REVIEW_SHA),
            ("hveTree", _REVIEW_SHA), ("releaseEvidence", "incomplete"), ("attestation", True),
            ("evaluatedAt", "2026-09-24T00:00:00Z"), ("collectionArtifactId", "../456"),
        ],
    )
    def test_rejects_forged_manifest_identity(
        self, documents: tuple[dict[str, Any], dict[str, Any], dict[str, Any]], field: str, value: Any,
    ) -> None:
        documents[0][field] = value
        with pytest.raises(ValueError):
            promotion.validate_promotion_manifest(*documents, source_sha=_SHA, promotion_run_id="789")

    @pytest.mark.parametrize(
        "binding", [
            "sourceRevision", "buildDigest", "configDigest", "fixtureDigest", "campaignId",
            "harnessDigest", "toolDigest", "mappingDigest", "lockfileDigest", "runId", "environment",
        ],
    )
    def test_rejects_promoted_bundle_binding_drift(
        self, documents: tuple[dict[str, Any], dict[str, Any], dict[str, Any]], binding: str,
    ) -> None:
        documents[2]["runManifest"][binding] = "unexpected"
        with pytest.raises(ValueError):
            promotion.validate_promotion_manifest(*documents, source_sha=_SHA, promotion_run_id="789")

    def test_rejects_unexpected_new_run_binding(
        self, documents: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    ) -> None:
        documents[2]["runManifest"]["unexpectedBinding"] = _DIGEST
        with pytest.raises(ValueError):
            promotion.validate_promotion_manifest(*documents, source_sha=_SHA, promotion_run_id="789")

    def test_canonical_environment_order_preserves_run_binding(
        self, documents: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    ) -> None:
        documents[2]["runManifest"]["environment"]["inputModes"].reverse()
        promotion.validate_promotion_manifest(*documents, source_sha=_SHA, promotion_run_id="789")

    def test_rejects_claimed_success_for_incomplete_bundle(
        self, documents: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
    ) -> None:
        documents[2]["scopeCompleteness"]["releaseEvidence"] = "incomplete"
        with pytest.raises(ValueError, match="incomplete"):
            promotion.validate_promotion_manifest(*documents, source_sha=_SHA, promotion_run_id="789")

    def test_diagnostic_package_cannot_enter_promotion(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "package"
        promotion.evidence_gate.stage_docusaurus_package(source, target)
        with pytest.raises(ValueError):
            promotion._validate_collection(target, tmp_path / "not-a-harness", _SHA)

    @pytest.fixture()
    def deadline_documents(self) -> tuple[dict[str, Any], dict[str, Any]]:
        registry = {"records": [
            {"recordId": "qualification", "validUntil": "2026-10-01T00:00:00Z"},
            {"recordId": "approval", "validUntil": "2026-09-28T00:00:00Z"},
            {"recordId": "unused", "validUntil": "2026-09-01T00:00:00Z"},
        ]}
        bundle = {
            "scopeCompleteness": {"releaseEvidence": "complete"},
            "expectedCells": [{"cellId": "human", "human": True}],
            "evidenceResults": [{"cellId": "human", "resultId": "current"}],
            "supplements": [
                {"supplementId": "historical", "validUntil": "2026-09-01T00:00:00Z"},
                {"supplementId": "current", "qualificationRecordId": "qualification",
                 "approvalRecordId": "approval", "validUntil": "2026-09-27T00:00:00Z"},
            ],
        }
        return registry, bundle

    def test_deadline_uses_only_current_evidence(
        self, deadline_documents: tuple[dict[str, Any], dict[str, Any]],
    ) -> None:
        assert promotion.release_valid_until(*deadline_documents, now=_NOW) == "2026-09-27T00:00:00Z"

    @pytest.mark.parametrize("record", ["qualification", "approval", "supplement", "automated"])
    def test_deadline_rejects_expired_current_evidence(
        self, deadline_documents: tuple[dict[str, Any], dict[str, Any]], record: str,
    ) -> None:
        registry, bundle = deadline_documents
        expired = "2026-09-24T00:00:00Z"
        if record == "supplement":
            bundle["supplements"][1]["validUntil"] = expired
        elif record == "automated":
            bundle["evidenceResults"].append({"resultId": "auto", "cellId": "auto", "validUntil": expired})
        else:
            next(item for item in registry["records"] if item["recordId"] == record)["validUntil"] = expired
        with pytest.raises(ValueError, match="expired"):
            promotion.release_valid_until(registry, bundle, now=_NOW)

    def test_deadline_ignores_superseded_automated_evidence(
        self, deadline_documents: tuple[dict[str, Any], dict[str, Any]],
    ) -> None:
        deadline_documents[1]["evidenceResults"].append(
            {"resultId": "old", "cellId": "auto", "current": False, "validUntil": "2026-09-01T00:00:00Z"},
        )
        assert promotion.release_valid_until(*deadline_documents, now=_NOW) == "2026-09-27T00:00:00Z"


class TestWorkflowBoundary:
    def test_promotion_has_protected_manual_entry_and_read_permissions(self) -> None:
        path = _ROOT / ".github" / "workflows" / "docusaurus-accessibility-promotion.yml"
        workflow = yaml.safe_load(path.read_text())
        triggers = workflow.get("on", workflow.get(True))
        assert set(triggers) == {"workflow_dispatch"}
        assert workflow["permissions"] == {"contents": "read", "actions": "read"}
        job = workflow["jobs"]["promote"]
        assert job["permissions"] == {"contents": "read", "actions": "read"}
        assert job["environment"] == "accessibility-release"
        text = path.read_text()
        assert "ACCESSIBILITY_REVIEWER_EVIDENCE_BRANCH" in text
        assert "ACCESSIBILITY_REVIEW_REGISTRY_DIGEST" in text
        assert "artifact-ids:" in text and "run-id:" in text and "github-token:" in text
        assert "--require-completeness release" in text

    def test_deployment_consumes_retained_build_without_rebuilding(self) -> None:
        path = _ROOT / ".github" / "workflows" / "deploy-docs.yml"
        workflow = yaml.safe_load(path.read_text())
        triggers = workflow.get("on", workflow.get(True))
        assert set(triggers) == {"workflow_run"}
        assert triggers["workflow_run"]["workflows"] == ["Docusaurus Accessibility Promotion"]
        text = path.read_text()
        assert "github.event.workflow_run.head_sha" in text
        assert "verify-promoted" in text and 'check-current --source-sha "$SOURCE_SHA"' in text
        assert "collection/docs/docusaurus/build" in text
        assert "npm run build" not in text and "docusaurus-tests.yml" not in text
        assert workflow["jobs"]["deploy"]["environment"]["name"] == "github-pages"
        assert workflow["jobs"]["deploy"]["permissions"] == {
            "contents": "read", "actions": "read", "pages": "write", "id-token": "write",
        }
        deploy_steps = workflow["jobs"]["deploy"]["steps"]
        download = next(step for step in deploy_steps if "download-artifact@" in step.get("uses", ""))
        assert download["with"]["artifact-ids"] == "${{ needs.build.outputs.artifact-id }}"
        assert download["with"]["run-id"] == "${{ github.event.workflow_run.id }}"
        assert "check-promotion" in deploy_steps[-3]["run"]
        assert "verify-promoted" in deploy_steps[-3]["run"]
        assert "--refresh-review-registry" in deploy_steps[-3]["run"]
        assert workflow["jobs"]["deploy"]["env"]["ACCESSIBILITY_REVIEWER_EVIDENCE_BRANCH"] == (
            "${{ vars.ACCESSIBILITY_REVIEWER_EVIDENCE_BRANCH }}"
        )
        assert workflow["jobs"]["deploy"]["env"]["ACCESSIBILITY_REVIEW_REGISTRY_DIGEST"] == (
            "${{ secrets.ACCESSIBILITY_REVIEW_REGISTRY_DIGEST }}"
        )
        assert 'check-current --source-sha "$SOURCE_SHA"' in deploy_steps[-2]["run"]
        assert deploy_steps[-1]["uses"].startswith("actions/deploy-pages@")

    def test_all_actions_are_pinned_and_checkout_is_trusted_main(self) -> None:
        for name in ("docusaurus-accessibility-promotion.yml", "deploy-docs.yml"):
            text = (_ROOT / ".github/workflows" / name).read_text()
            assert "scripts/accessibility/promotion.py" not in text
            assert "-m scripts.accessibility.promotion" in text
            workflow = yaml.safe_load(text)
            for job in workflow["jobs"].values():
                for step in job["steps"]:
                    action = step.get("uses")
                    if action:
                        assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", action)
                    if action and action.startswith("actions/checkout@"):
                        assert step["with"]["persist-credentials"] is False
                        assert step["with"]["ref"] == "refs/heads/main"
