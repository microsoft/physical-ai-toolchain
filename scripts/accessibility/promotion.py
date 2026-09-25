"""Acquire inert reviewer evidence and promote an immutable documentation build."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from scripts.accessibility import evidence_gate

_METADATA = Path("artifacts/accessibility/docusaurus")
_HVE_REF = "56c30bcbbba1a8235970c44f5e79a9d83e296f54"
_HVE_TREE = "c6875ad36ceeca2acec665195c385792f4b1f32f"
_HVE_PATH = ".github/skills/accessibility/accessibility"
_HVE_HASHES = {
    "scripts/runtime_a11y/probe-criteria-map.json": "ef1de7239be999bb271184337e9edc15eb48fb66a69ca5dcb9628182350893a4",
    "scripts/runtime_a11y/package-lock.json": "dd7689d6b058e42facba4539f8414d6090d95ea82853c665e05026857a09026f",
    "uv.lock": "f0265e2d836fb9765be0a6f15e4c21fefc72a46651e97490dea27535172b6377",
}
_PROMOTION_WORKFLOW = ".github/workflows/docusaurus-accessibility-promotion.yml"
_MAX_REVIEW_FILES = 1000
_MAX_REVIEW_BYTES = 16 * 1024 * 1024


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _sha(value: str, length: int = 40) -> str:
    _require(
        isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None,
        "An exact lowercase digest or revision is required",
    )
    return value


def _identifier(value: str) -> str:
    _require(
        isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) is not None,
        "A positive immutable Actions identifier is required",
    )
    return value


def _time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Evidence time must be an ISO timestamp") from error
    _require(parsed.tzinfo is not None, "Evidence time must include a UTC offset")
    return parsed


def _unique_json(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        _require(key not in result, "Duplicate JSON keys are not allowed")
        result[key] = value
    return result


def read_json(path: Path) -> dict[str, Any]:
    """Read data without permitting ambiguous duplicate JSON keys."""
    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_json)
    _require(isinstance(result, dict), "Expected a JSON object")
    return result


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_dispatch(ref: str, sha: str, current_main: str) -> None:
    """Reject manual dispatch outside the still-current main revision."""
    _sha(sha)
    _sha(current_main)
    _require(ref == "refs/heads/main" and sha == current_main, "Dispatch must use current main")


def validate_run(
    run: dict[str, Any],
    *,
    repository: str,
    run_id: str,
    current_main: str,
    promotion: bool = False,
) -> None:
    """Bind a completed Actions run to this repository, workflow and current main."""
    _identifier(run_id)
    _sha(current_main)
    expected = {
        "id": int(run_id),
        "head_sha": current_main,
        "head_branch": "main",
        "event": "workflow_dispatch" if promotion else "push",
        "path": _PROMOTION_WORKFLOW if promotion else ".github/workflows/main.yml",
        "name": "Docusaurus Accessibility Promotion" if promotion else "CI",
        "status": "completed",
        "conclusion": "success",
    }
    _require(all(run.get(key) == value for key, value in expected.items()), "Actions run identity is not eligible")
    for key in ("repository", "head_repository"):
        _require((run.get(key) or {}).get("full_name") == repository, "Foreign repository run is not eligible")


def validate_artifact(
    artifact: dict[str, Any],
    *,
    run_id: str,
    artifact_id: str,
    source_sha: str,
    now: datetime,
    promotion: bool = False,
) -> None:
    """Require the immutable artifact to belong to the exact eligible run."""
    _identifier(run_id)
    _identifier(artifact_id)
    prefix = "docusaurus-accessibility-promoted" if promotion else "docusaurus-accessibility-evidence-release"
    _require(
        artifact.get("id") == int(artifact_id) and artifact.get("name") == f"{prefix}-{run_id}",
        "Artifact identity does not match the eligible run",
    )
    _require(
        artifact.get("expired") is False and _time(artifact.get("expires_at")) > now,
        "Artifact has expired or has unknown retention",
    )
    binding = artifact.get("workflow_run") or {}
    _require(
        binding.get("id") == int(run_id)
        and binding.get("head_sha") == source_sha
        and binding.get("head_branch") == "main",
        "Artifact run binding does not match",
    )


def validate_branch_name(name: str) -> None:
    """Accept only an explicit protected branch name, never a revision expression."""
    _require(
        isinstance(name, str)
        and bool(name)
        and not name.startswith(("-", "refs/"))
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", name) is not None
        and all(part and not part.startswith(".") and not part.endswith((".", ".lock")) for part in name.split("/"))
        and ".." not in name,
        "Protected reviewer-evidence branch must be explicitly configured",
    )


def validate_review_ref(
    branch: dict[str, Any],
    comparison: dict[str, Any],
    *,
    branch_name: str,
    revision: str,
) -> None:
    """Require the candidate to be an ancestor of the protected branch head."""
    validate_branch_name(branch_name)
    _sha(revision)
    _require(
        branch.get("name") == branch_name and branch.get("protected") is True,
        "Reviewer-evidence branch protection is unavailable or disabled",
    )
    _sha((branch.get("commit") or {}).get("sha"))
    _require(
        comparison.get("status") in {"ahead", "identical"}
        and (comparison.get("merge_base_commit") or {}).get("sha") == revision,
        "Reviewer commit is not reachable from the protected branch",
    )


def _safe_path(name: str) -> PurePosixPath:
    _require(
        isinstance(name, str)
        and bool(name)
        and "\\" not in name
        and ":" not in name
        and all(part not in {"", ".", ".."} for part in name.split("/")),
        "Unsafe evidence path",
    )
    path = PurePosixPath(name)
    _require(not path.is_absolute(), "Evidence path must be relative")
    return path


def validate_review_entry(entry: dict[str, Any]) -> None:
    """Permit only ordinary JSON blobs in the privacy-minimized reviewer package."""
    path = _safe_path(entry.get("path", ""))
    permitted = str(path) in {"review-registry.json", "package-manifest.json"} or (
        len(path.parts) == 2
        and path.parts[0] == "supplements"
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*\.json", path.name) is not None
    )
    _require(
        permitted and entry.get("mode") == "100644" and entry.get("type") == "blob",
        "Reviewer evidence must contain only allowlisted, non-executable JSON blobs",
    )


def _inventory(root: Path) -> list[dict[str, Any]]:
    _require(
        root.is_dir() and not root.is_symlink() and not root.is_junction(), "Package root must be an ordinary directory"
    )
    files = []
    for path in sorted(root.rglob("*")):
        _require(not path.is_symlink() and not path.is_junction(), "Package symlinks and junctions are forbidden")
        if path.is_dir():
            continue
        _require(path.is_file(), "Package entries must be regular files")
        data = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sizeBytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return files


def verify_review_package(root: Path, source_sha: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Verify exact manifest closure before interpreting registry or supplements."""
    _sha(source_sha)
    actual = _inventory(root)
    _require(
        all(path.relative_to(root).as_posix() == "supplements" for path in root.rglob("*") if path.is_dir()),
        "Unexpected reviewer evidence directory",
    )
    _require(
        len(actual) <= _MAX_REVIEW_FILES and sum(item["sizeBytes"] for item in actual) <= _MAX_REVIEW_BYTES,
        "Reviewer package exceeds the bounded data budget",
    )
    for item in actual:
        validate_review_entry({**item, "mode": "100644", "type": "blob"})
        _require(
            not (root / item["path"]).stat().st_mode & 0o111 if os.name != "nt" else True,
            "Executable reviewer files are forbidden",
        )
    manifest = read_json(root / "package-manifest.json")
    _require(
        set(manifest) == {"schemaVersion", "sourceRevision", "files"}
        and manifest["schemaVersion"] == "1.0.0"
        and manifest["sourceRevision"] == source_sha,
        "Reviewer manifest source binding is invalid",
    )
    expected = manifest["files"]
    _require(isinstance(expected, list), "Reviewer manifest files must be a list")
    for item in expected:
        _require(
            isinstance(item, dict) and set(item) == {"path", "sha256", "sizeBytes"},
            "Reviewer manifest entry is invalid",
        )
        validate_review_entry({**item, "mode": "100644", "type": "blob"})
    _require(
        sorted(expected, key=lambda item: item["path"])
        == [item for item in actual if item["path"] != "package-manifest.json"],
        "Reviewer package inventory, sizes or digests do not match",
    )
    supplements = [read_json(root / item["path"]) for item in actual if item["path"].startswith("supplements/")]
    _require(bool(supplements), "Completed reviewer supplements are required")
    return read_json(root / "review-registry.json"), supplements


def validate_anchors(
    prior: dict[str, Any],
    registry: dict[str, Any],
    prior_digest: str,
    registry_digest: str,
) -> None:
    """Check both independently supplied HVE canonical digest anchors."""
    for document, field, expected, domain in (
        (prior, "bundleDigest", prior_digest, "hve-a11y:evidence-bundle:v1"),
        (registry, "digest", registry_digest, "hve-a11y:review-registry:v1"),
    ):
        _sha(expected, 64)
        computed = evidence_gate._hve_canonical_digest(
            {key: value for key, value in document.items() if key != field},
            domain,
        )
        _require(document.get(field) == computed == expected, "Independent evidence digest anchor does not match")


def evaluation_context(original: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Advance evaluation time without altering collection identity or observations."""
    _require(
        now.tzinfo is not None and _time(original.get("composedAt")) <= now,
        "Collection evaluation time is in the future",
    )
    result = deepcopy(original)
    result["composedAt"] = now.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return result


def _same_run_bindings(original: dict[str, Any], evaluated: dict[str, Any]) -> bool:
    return evidence_gate._hve_canonicalize(
        {key: value for key, value in original.items() if key != "composedAt"},
    ) == evidence_gate._hve_canonicalize(
        {key: value for key, value in evaluated.items() if key != "composedAt"},
    )


def require_release(bundle: dict[str, Any]) -> None:
    """Preserve upstream release incompleteness, including informing CANT_TELL results."""
    _require(
        (bundle.get("scopeCompleteness") or {}).get("releaseEvidence") == "complete",
        "Pinned HVE release evidence is incomplete; publication is blocked",
    )


def _command(arguments: list[str]) -> str:
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        env={**os.environ, "NODE_DISABLE_COMPILE_CACHE": "1"},
    )
    _require(result.returncode == 0, f"Trusted {Path(arguments[0]).name} operation failed (exit {result.returncode})")
    return result.stdout.strip()


class GitHub:
    """Read-only, same-repository GitHub API client using the runner's gh credential."""

    def __init__(self, repository: str) -> None:
        _require(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository) is not None
            and repository.split("/")[-1] not in {".", ".."},
            "Expected a repository owner/name",
        )
        self.repository = repository

    def get(self, route: str) -> dict[str, Any]:
        """Read repository metadata without printing response or credentials."""
        result = json.loads(
            _command(["gh", "api", "--method", "GET", f"repos/{self.repository}/{route}"]),
            object_pairs_hook=_unique_json,
        )
        _require(isinstance(result, dict), "GitHub returned unexpected metadata")
        return result

    def current_main(self) -> str:
        """Return the current main commit from the authoritative repository."""
        return _sha(self.get("git/ref/heads/main")["object"]["sha"])


def _read_git_blob(api: GitHub, entry: dict[str, Any]) -> bytes:
    blob = api.get(f"git/blobs/{_sha(entry['sha'])}")
    _require(blob.get("encoding") == "base64" and blob.get("sha") == entry["sha"], "Reviewer blob identity is invalid")
    data = base64.b64decode("".join(blob["content"].split()), validate=True)
    _require(len(data) == entry["size"] and len(data) <= _MAX_REVIEW_BYTES, "Reviewer blob size differs from its tree")
    actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()
    _require(actual == entry["sha"], "Reviewer Git blob digest mismatch")
    return data


def verify_current_registry(
    api: GitHub,
    *,
    branch_name: str,
    reviewer_revision: str,
    source_sha: str,
    promoted_digest: str,
    expected_digest: str,
) -> None:
    """Reject registry revocations or changes after promotion and environment approval."""
    _sha(expected_digest, 64)
    _sha(promoted_digest, 64)
    _sha(reviewer_revision)
    _sha(source_sha)
    validate_branch_name(branch_name)
    _require(expected_digest == promoted_digest, "Current protected registry anchor invalidates this promotion")
    branch = api.get(f"branches/{quote(branch_name, safe='')}")
    _require(branch.get("protected") is True, "Reviewer branch protection cannot be verified")
    head = _sha((branch.get("commit") or {}).get("sha"))
    comparison = api.get(f"compare/{reviewer_revision}...{head}")
    validate_review_ref(branch, comparison, branch_name=branch_name, revision=reviewer_revision)
    commit = api.get(f"git/commits/{head}")
    _require(commit.get("sha") == head, "Reviewer branch commit identity changed")
    tree = api.get(f"git/trees/{_sha(commit['tree']['sha'])}?recursive=1")
    _require(tree.get("truncated") is False, "Current reviewer tree is incomplete")
    path = f"reviewer-evidence/docusaurus/{source_sha}/review-registry.json"
    entries = [item for item in tree.get("tree", []) if item.get("path") == path]
    _require(len(entries) == 1, "Current reviewer registry is missing or ambiguous")
    entry = entries[0]
    validate_review_entry({**entry, "path": "review-registry.json"})
    _require(
        isinstance(entry.get("size"), int) and 0 <= entry["size"] <= _MAX_REVIEW_BYTES,
        "Current reviewer registry exceeds the bounded data budget",
    )
    registry = json.loads(_read_git_blob(api, entry), object_pairs_hook=_unique_json)
    _require(isinstance(registry, dict), "Current reviewer registry must be a JSON object")
    digest = evidence_gate._hve_canonical_digest(
        {key: value for key, value in registry.items() if key != "digest"},
        "hve-a11y:review-registry:v1",
    )
    _require(
        registry.get("digest") == digest == expected_digest,
        "Current reviewer registry differs from the approved promotion; collect fresh approval",
    )


def acquire_review(api: GitHub, *, revision: str, branch_name: str, source_sha: str, output: Path) -> None:
    """Fetch allowlisted Git blobs, never checking out or executing the evidence ref."""
    _sha(revision)
    _sha(source_sha)
    validate_branch_name(branch_name)
    _require(not output.exists(), "Reviewer acquisition requires a fresh directory")
    branch = api.get(f"branches/{quote(branch_name, safe='')}")
    _require(branch.get("protected") is True, "Reviewer branch protection cannot be verified")
    head = _sha((branch.get("commit") or {}).get("sha"))
    comparison = api.get(f"compare/{revision}...{head}")
    validate_review_ref(branch, comparison, branch_name=branch_name, revision=revision)
    commit = api.get(f"git/commits/{revision}")
    _require(commit.get("sha") == revision, "Reviewer commit lookup did not return the exact revision")
    tree = api.get(f"git/trees/{_sha(commit['tree']['sha'])}?recursive=1")
    _require(tree.get("truncated") is False, "Reviewer tree is incomplete")
    prefix = f"reviewer-evidence/docusaurus/{source_sha}/"
    selected = []
    for item in tree.get("tree", []):
        name = item.get("path", "")
        if not name.startswith(prefix):
            continue
        relative = name.removeprefix(prefix)
        if relative == "supplements" and item.get("type") == "tree" and item.get("mode") == "040000":
            continue
        entry = {**item, "path": relative}
        validate_review_entry(entry)
        _sha(entry.get("sha"))
        _require(
            isinstance(entry.get("size"), int) and 0 <= entry["size"] <= _MAX_REVIEW_BYTES,
            "Reviewer blob size is invalid",
        )
        selected.append(entry)
    _require(
        0 < len(selected) <= _MAX_REVIEW_FILES and sum(item["size"] for item in selected) <= _MAX_REVIEW_BYTES,
        "Reviewer evidence is missing or exceeds its bounded data budget",
    )
    _require(len({item["path"] for item in selected}) == len(selected), "Duplicate reviewer Git paths")
    output.mkdir(parents=True)
    for entry in selected:
        data = _read_git_blob(api, entry)
        path = output / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    verify_review_package(output, source_sha)


def verify_harness(harness_root: Path) -> None:
    """Check the same immutable HVE revision, skill tree and lock hashes as collection."""
    root = harness_root.resolve()
    checkout = root.parents[3]
    _require(
        _command(["git", "-C", str(checkout), "rev-parse", "HEAD"]) == _HVE_REF,
        "HVE revision differs from the reviewed collection pin",
    )
    _require(
        _command(["git", "-C", str(checkout), "rev-parse", f"HEAD:{_HVE_PATH}"]) == _HVE_TREE,
        "HVE skill tree differs from the reviewed collection pin",
    )
    _require(root == checkout / _HVE_PATH, "HVE skill path is not the pinned checkout")
    _require(
        not _command(["git", "-C", str(checkout), "diff", "HEAD", "--", _HVE_PATH]), "HVE tracked runtime was modified"
    )
    for relative, expected in _HVE_HASHES.items():
        _require(
            hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected,
            "HVE runtime input digest differs from the reviewed pin",
        )


def acquire_harness(checkout: Path) -> None:
    """Acquire only the reviewed upstream commit, validating before dependency installation."""
    _require(not checkout.exists(), "HVE acquisition requires a fresh directory")
    _command(["git", "init", "--quiet", str(checkout)])
    _command(["git", "-C", str(checkout), "remote", "add", "origin", "https://github.com/microsoft/hve-core.git"])
    _command(["git", "-C", str(checkout), "fetch", "--quiet", "--depth", "1", "origin", _HVE_REF])
    _command(["git", "-c", "core.autocrlf=false", "-C", str(checkout), "checkout", "--quiet", "--detach", _HVE_REF])
    verify_harness(checkout / _HVE_PATH)


def _validate_collection(root: Path, harness_root: Path, source_sha: str) -> dict[str, Any]:
    evidence_gate.verify_docusaurus_package(root, require_complete=True, harness_root=harness_root)
    metadata = root / _METADATA
    prior = read_json(metadata / "evidence-bundle.json")
    _require(prior["runManifest"]["sourceRevision"] == source_sha, "Collection source is not current main")
    _require(
        read_json(metadata / "inputs/evidence-scope.json").get("cadenceClass") == "release",
        "Collection must cover release scope",
    )
    summary = evidence_gate.validate_composed_docusaurus_bundle(
        metadata / "evidence-bundle.json",
        root,
        harness_root=harness_root,
        required_completeness="automated",
    )
    _require(summary.get("verdict") == "PASS", "Collection automated evidence is incomplete")
    return prior


def _compose(
    collection: Path,
    review: Path,
    output: Path,
    *,
    harness_root: Path,
    prior_digest: str,
    registry_digest: str,
    now: datetime,
) -> Path:
    inputs = collection / _METADATA / "inputs"
    prior_path = collection / _METADATA / "evidence-bundle.json"
    prior = read_json(prior_path)
    registry, supplements = verify_review_package(review, prior["runManifest"]["sourceRevision"])
    validate_anchors(prior, registry, prior_digest, registry_digest)
    original = read_json(inputs / "run-context.json")
    _require(
        evidence_gate._hve_canonicalize(original) == evidence_gate._hve_canonicalize(prior["runManifest"]),
        "Collection run context differs from the original bundle",
    )
    for supplement in supplements:
        _require(
            all(
                supplement.get(key) == original.get(key)
                for key in ("sourceRevision", "buildDigest", "configDigest", "campaignId")
            ),
            "Reviewer supplement targets a different collection identity",
        )
    _require(not output.exists(), "Composition requires a fresh output directory")
    output.mkdir(parents=True)
    context_path = output / "run-context.json"
    evaluated = evaluation_context(original, now=now)
    _write_json(context_path, evaluated)
    bundle_path = output / "evidence-bundle.json"
    arguments = [
        sys.executable,
        str(harness_root / "scripts/runtime_a11y/__main__.py"),
        "compose-evidence",
        "--asset-catalog",
        str(inputs / "asset-journeys.json"),
        "--requirement-catalog",
        str(inputs / "requirement-methods.json"),
        "--scope",
        str(inputs / "evidence-scope.json"),
        "--run-context",
        str(context_path),
        "--source",
        str(inputs / "evidence-source.json"),
        "--source",
        str(inputs / "evidence-playwright.json"),
        "--state-proofs",
        str(inputs / "state-proofs.json"),
        "--artifact-root",
        str(collection),
        "--prior-bundle",
        str(prior_path),
        "--prior-bundle-digest",
        prior_digest,
        "--review-registry",
        str(review / "review-registry.json"),
        "--expected-review-registry-digest",
        registry_digest,
        "--out",
        str(bundle_path),
        "--require-completeness",
        "release",
    ]
    for path in sorted((review / "supplements").glob("*.json")):
        arguments.extend(["--supplement", str(path)])
    # The upstream completeness exit is authoritative, including its known informing-evidence blocker.
    _command(arguments)
    bundle = read_json(bundle_path)
    require_release(bundle)
    _require(
        evidence_gate._hve_canonicalize(bundle["runManifest"]) == evidence_gate._hve_canonicalize(evaluated),
        "Recomposition changed collection identity",
    )
    summary = evidence_gate.validate_composed_docusaurus_bundle(
        bundle_path,
        collection,
        harness_root=harness_root,
        required_completeness="release",
    )
    _require(summary.get("verdict") == "PASS", "Project release validation is incomplete")
    _write_json(output / "evidence-summary.json", summary)
    return bundle_path


def promote(
    collection: Path,
    review: Path,
    output: Path,
    *,
    harness_root: Path,
    source_sha: str,
    collection_run_id: str,
    collection_artifact_id: str,
    promotion_run_id: str,
    reviewer_revision: str,
    prior_digest: str,
    registry_digest: str,
    now: datetime,
) -> None:
    """Recompose without modifying any retained observation or prior bundle."""
    for identifier in (collection_run_id, collection_artifact_id, promotion_run_id):
        _identifier(identifier)
    _sha(reviewer_revision)
    verify_harness(harness_root)
    prior = _validate_collection(collection, harness_root, source_sha)
    registry, _ = verify_review_package(review, source_sha)
    validate_anchors(prior, registry, prior_digest, registry_digest)
    _require(not output.exists(), "Promotion requires a fresh output directory")
    output.mkdir(parents=True)
    shutil.copytree(collection, output / "collection")
    shutil.copytree(review, output / "review")
    bundle_path = _compose(
        (output / "collection").resolve(),
        (output / "review").resolve(),
        (output / "promotion").resolve(),
        harness_root=harness_root.resolve(),
        prior_digest=prior_digest,
        registry_digest=registry_digest,
        now=now,
    )
    promoted = read_json(bundle_path)
    validation = {
        "schemaVersion": "1.0.0",
        "sourceRevision": source_sha,
        "buildDigest": prior["runManifest"]["buildDigest"],
        "bundleDigest": promoted["bundleDigest"],
        "priorBundleDigest": prior_digest,
        "reviewRegistryDigest": registry_digest,
        "reviewerRevision": reviewer_revision,
        "collectionRunId": collection_run_id,
        "collectionArtifactId": collection_artifact_id,
        "promotionRunId": promotion_run_id,
        "hveRevision": _HVE_REF,
        "hveTree": _HVE_TREE,
        "releaseEvidence": "complete",
        "attestation": False,
        "evaluatedAt": promoted["composedAt"],
    }
    _write_json(output / "promotion/validation-manifest.json", validation)
    _write_json(output / "promotion-manifest.json", {"schemaVersion": "1.0.0", "files": _inventory(output)})


def validate_promotion_manifest(
    validation: dict[str, Any],
    prior: dict[str, Any],
    bundle: dict[str, Any],
    *,
    source_sha: str,
    promotion_run_id: str,
) -> None:
    """Bind deployment metadata to the original and promoted evidence identities."""
    fields = {
        "schemaVersion",
        "sourceRevision",
        "buildDigest",
        "bundleDigest",
        "priorBundleDigest",
        "reviewRegistryDigest",
        "reviewerRevision",
        "collectionRunId",
        "collectionArtifactId",
        "promotionRunId",
        "hveRevision",
        "hveTree",
        "releaseEvidence",
        "attestation",
        "evaluatedAt",
    }
    _require(
        set(validation) == fields and validation["schemaVersion"] == "1.0.0",
        "Promoted validation manifest fields are invalid",
    )
    for field in ("collectionRunId", "collectionArtifactId", "promotionRunId"):
        _identifier(validation[field])
    _sha(validation["reviewerRevision"])
    _sha(validation["reviewRegistryDigest"], 64)
    _require(
        validation["sourceRevision"] == _sha(source_sha) == prior["runManifest"]["sourceRevision"]
        and validation["promotionRunId"] == _identifier(promotion_run_id)
        and validation["hveRevision"] == _HVE_REF
        and validation["hveTree"] == _HVE_TREE
        and validation["releaseEvidence"] == "complete"
        and validation["attestation"] is False,
        "Promoted validation manifest does not identify this release",
    )
    require_release(bundle)
    _require(
        bundle.get("bundleDigest") == validation["bundleDigest"]
        and prior.get("bundleDigest") == validation["priorBundleDigest"]
        and bundle.get("composedAt") == validation["evaluatedAt"]
        and bundle["runManifest"].get("composedAt") == validation["evaluatedAt"]
        and validation["buildDigest"] == prior["runManifest"]["buildDigest"]
        and _same_run_bindings(prior["runManifest"], bundle["runManifest"]),
        "Promoted bundle does not match the original collection",
    )


def release_valid_until(registry: dict[str, Any], bundle: dict[str, Any], *, now: datetime) -> str:
    """Derive a publication deadline from the HVE-validated current evidence."""
    require_release(bundle)
    human_cells = {item["cellId"] for item in bundle["expectedCells"] if item.get("human")}
    supplements = {item["supplementId"]: item for item in bundle["supplements"]}
    records = {item["recordId"]: item for item in registry["records"]}
    deadlines = []
    reviewed_cells = set()
    for result in bundle["evidenceResults"]:
        if result.get("current") is False:
            continue
        if result.get("validUntil"):
            deadlines.append(_time(result["validUntil"]))
        if result["cellId"] not in human_cells:
            continue
        _require(result["cellId"] not in reviewed_cells, "Ambiguous current reviewer evidence")
        reviewed_cells.add(result["cellId"])
        supplement = supplements[result["resultId"]]
        if supplement.get("validUntil"):
            deadlines.append(_time(supplement["validUntil"]))
        for key in ("qualificationRecordId", "approvalRecordId"):
            deadlines.append(_time(records[supplement[key]]["validUntil"]))
    _require(reviewed_cells == human_cells and bool(deadlines), "Release validity deadline is unavailable")
    deadline = min(deadlines)
    _require(deadline > now, "Current release evidence has expired")
    return deadline.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def verify_promoted(
    root: Path,
    *,
    harness_root: Path,
    source_sha: str,
    promotion_run_id: str,
    scratch: Path,
    now: datetime,
) -> dict[str, Any]:
    """Verify the closed promoted envelope and reevaluate expiry before publication."""
    verify_harness(harness_root)
    manifest = read_json(root / "promotion-manifest.json")
    _require(
        set(manifest) == {"schemaVersion", "files"} and manifest["schemaVersion"] == "1.0.0",
        "Promoted package manifest is invalid",
    )
    actual = [item for item in _inventory(root) if item["path"] != "promotion-manifest.json"]
    _require(manifest["files"] == actual, "Promoted package has missing, extra or modified bytes")
    for item in actual:
        path = _safe_path(item["path"])
        _require(path.parts[0] in {"collection", "review", "promotion"}, "Unexpected promoted package path")
    validation = read_json(root / "promotion/validation-manifest.json")
    prior = _validate_collection(root / "collection", harness_root, source_sha)
    registry, _ = verify_review_package(root / "review", source_sha)
    validate_anchors(prior, registry, validation["priorBundleDigest"], validation["reviewRegistryDigest"])
    bundle = read_json(root / "promotion/evidence-bundle.json")
    validate_promotion_manifest(validation, prior, bundle, source_sha=source_sha, promotion_run_id=promotion_run_id)
    summary = evidence_gate.validate_composed_docusaurus_bundle(
        root / "promotion/evidence-bundle.json",
        root / "collection",
        harness_root=harness_root,
        required_completeness="release",
    )
    _require(summary.get("verdict") == "PASS", "Promoted release verdict is incomplete")
    build_root = root / "collection/docs/docusaurus/build"
    digest = evidence_gate._digest_paths(
        [path for path in build_root.rglob("*") if path.is_file()], root / "collection"
    )
    _require(
        digest == validation.get("buildDigest") == prior["runManifest"]["buildDigest"],
        "Retained documentation build digest does not match",
    )
    _require(
        not scratch.exists() and not scratch.resolve().is_relative_to(root.resolve()),
        "Expiry verification requires a fresh directory outside the package",
    )
    try:
        current_bundle_path = _compose(
            (root / "collection").resolve(),
            (root / "review").resolve(),
            scratch.resolve(),
            harness_root=harness_root.resolve(),
            prior_digest=validation["priorBundleDigest"],
            registry_digest=validation["reviewRegistryDigest"],
            now=now,
        )
        deadline = release_valid_until(registry, read_json(current_bundle_path), now=now)
        validation = {**validation, "validUntil": deadline}
    finally:
        if scratch.is_dir():
            shutil.rmtree(scratch)
    return validation


def _github_output(**values: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with Path(path).open("a", encoding="utf-8") as stream:
            for key, value in values.items():
                _require("\n" not in value and "\r" not in value, "Invalid workflow output")
                stream.write(f"{key}={value}\n")


def _check_run(api: GitHub, run_id: str, *, promotion: bool) -> str:
    _identifier(run_id)
    source_sha = api.current_main()
    validate_run(
        api.get(f"actions/runs/{run_id}"),
        repository=api.repository,
        run_id=run_id,
        current_main=source_sha,
        promotion=promotion,
    )
    return source_sha


def main(argv: list[str] | None = None) -> int:
    """Run read-only acquisition, promotion, or publication verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=[
            "check-collection",
            "acquire-review",
            "acquire-harness",
            "promote",
            "check-promotion",
            "verify-promoted",
            "check-current",
        ],
    )
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--run-id")
    parser.add_argument("--artifact-id")
    parser.add_argument("--source-sha")
    parser.add_argument("--dispatch-ref", default=os.environ.get("GITHUB_REF", ""))
    parser.add_argument("--dispatch-sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--reviewer-revision")
    parser.add_argument("--reviewer-branch", default=os.environ.get("ACCESSIBILITY_REVIEWER_EVIDENCE_BRANCH", ""))
    parser.add_argument("--registry-digest", default=os.environ.get("ACCESSIBILITY_REVIEW_REGISTRY_DIGEST", ""))
    parser.add_argument("--refresh-review-registry", action="store_true")
    parser.add_argument("--prior-bundle-digest")
    parser.add_argument("--promotion-run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument("--collection", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--harness-root", type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--require-completeness", choices=["release"], default="release")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    try:
        if args.operation == "acquire-harness":
            acquire_harness(args.output)
            return 0
        if args.operation == "verify-promoted":
            validation = verify_promoted(
                args.output,
                harness_root=args.harness_root,
                source_sha=args.source_sha,
                promotion_run_id=args.promotion_run_id,
                scratch=args.scratch,
                now=now,
            )
            if args.refresh_review_registry:
                verify_current_registry(
                    GitHub(args.repository),
                    branch_name=args.reviewer_branch,
                    reviewer_revision=validation["reviewerRevision"],
                    source_sha=validation["sourceRevision"],
                    promoted_digest=validation["reviewRegistryDigest"],
                    expected_digest=args.registry_digest,
                )
            _github_output(
                source_sha=validation["sourceRevision"],
                build_digest=validation["buildDigest"],
                valid_until=validation["validUntil"],
            )
            return 0
        api = GitHub(args.repository)
        if args.operation == "check-current":
            _require(api.current_main() == _sha(args.source_sha), "Main advanced; recollect and review the new build")
        elif args.operation == "check-collection":
            validate_dispatch(args.dispatch_ref, args.dispatch_sha, api.current_main())
            source = _check_run(api, args.run_id, promotion=False)
            _identifier(args.artifact_id)
            validate_artifact(
                api.get(f"actions/artifacts/{args.artifact_id}"),
                run_id=args.run_id,
                artifact_id=args.artifact_id,
                source_sha=source,
                now=now,
            )
            _github_output(source_sha=source)
        elif args.operation == "acquire-review":
            _sha(args.registry_digest, 64)
            acquire_review(
                api,
                revision=args.reviewer_revision,
                branch_name=args.reviewer_branch,
                source_sha=args.source_sha,
                output=args.output,
            )
        elif args.operation == "check-promotion":
            source = _check_run(api, args.run_id, promotion=True)
            artifacts = api.get(f"actions/runs/{args.run_id}/artifacts?per_page=100")
            _require(artifacts.get("total_count", 101) <= 100, "Promotion artifact listing is incomplete")
            matches = [
                item
                for item in artifacts.get("artifacts", [])
                if item.get("name") == f"docusaurus-accessibility-promoted-{args.run_id}"
            ]
            _require(len(matches) == 1, "Exactly one promoted package is required")
            artifact_id = str(matches[0]["id"])
            artifact = api.get(f"actions/artifacts/{artifact_id}")
            validate_artifact(
                artifact, run_id=args.run_id, artifact_id=artifact_id, source_sha=source, now=now, promotion=True
            )
            _github_output(source_sha=source, artifact_id=artifact_id, artifact_expires_at=artifact["expires_at"])
        elif args.operation == "promote":
            validate_dispatch(args.dispatch_ref, args.dispatch_sha, api.current_main())
            source = _check_run(api, args.run_id, promotion=False)
            _require(source == args.source_sha, "Collection source is no longer current")
            _identifier(args.artifact_id)
            validate_artifact(
                api.get(f"actions/artifacts/{args.artifact_id}"),
                run_id=args.run_id,
                artifact_id=args.artifact_id,
                source_sha=source,
                now=now,
            )
            promote(
                args.collection,
                args.review,
                args.output,
                harness_root=args.harness_root,
                source_sha=source,
                collection_run_id=args.run_id,
                collection_artifact_id=args.artifact_id,
                promotion_run_id=args.promotion_run_id,
                reviewer_revision=args.reviewer_revision,
                prior_digest=args.prior_bundle_digest,
                registry_digest=args.registry_digest,
                now=now,
            )
            _require(api.current_main() == source, "Main advanced during promotion")
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # Never echo reviewer documents, API responses, or credentials into Actions logs.
        print(f"Accessibility promotion blocked: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
