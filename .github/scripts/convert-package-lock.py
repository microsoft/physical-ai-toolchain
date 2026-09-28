#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import math
import os
import re
import stat
import tempfile
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Optional, TypeVar

_DEFAULT_SOURCE_URL_PATTERN = (
    r"\Ahttps://(?:packagefeedproxy\.microsoft\.io|"
    r"(?:[A-Za-z0-9-]+\.)?pkgs\.visualstudio\.com)/"
)
_DEFAULT_PUBLIC_REGISTRY = "https://registry.npmjs.org"
_DEFAULT_MIRROR_REGISTRY = "https://packagefeedproxy.microsoft.io/npm"
_DEFAULT_REQUEST_TIMEOUT = 10.0
_PRIVATE_HOST_PATTERN = re.compile(
    r"packagefeedproxy\.microsoft\.io|pkgs\.visualstudio\.com|ms-feed-"
)

NpmMetadata = tuple[str, Optional[str], str]
LockEntry = tuple[str, str, str, str]
_Key = TypeVar("_Key")
_Value = TypeVar("_Value")


def _positive_jobs(value: str) -> int:
    try:
        jobs = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if jobs < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return jobs


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "must be a finite positive number of seconds"
        ) from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError(
            "must be a finite positive number of seconds"
        )
    return timeout


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert package-lock.json locations between a package mirror and "
            "verified public npm artifact metadata."
        )
    )
    parser.add_argument(
        "locks", nargs="+", type=Path, help="package-lock.json files to convert"
    )
    parser.add_argument(
        "--target",
        choices=("public", "mirror"),
        default="public",
        help="registry metadata to write (default: public)",
    )
    parser.add_argument(
        "--source-url-pattern",
        default=_DEFAULT_SOURCE_URL_PATTERN,
        help="regular expression matching mirrored artifact URLs from their beginning",
    )
    parser.add_argument(
        "--mirror-registry",
        default=_DEFAULT_MIRROR_REGISTRY,
        help=f"package mirror registry URL (default: {_DEFAULT_MIRROR_REGISTRY})",
    )
    parser.add_argument(
        "--public-registry",
        default=_DEFAULT_PUBLIC_REGISTRY,
        help=f"public npm registry URL (default: {_DEFAULT_PUBLIC_REGISTRY})",
    )
    parser.add_argument(
        "--jobs",
        type=_positive_jobs,
        default=12,
        help="maximum concurrent metadata requests (default: 12)",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=_DEFAULT_REQUEST_TIMEOUT,
        metavar="SECONDS",
        help="per-request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--normalize",
        action="store_true",
        help="also verify and normalize entries already at the target registry",
    )
    parser.add_argument("--dry-run", action="store_true", help="validate without writing")
    return parser.parse_args()


def _package_name_from_url(url: str) -> str:
    path = urllib.parse.urlsplit(url).path
    prefix, separator, _ = path.rpartition("/-/")
    if not separator:
        raise RuntimeError(f"npm artifact URL lacks /-/: {url}")
    if "/registry/" in prefix:
        prefix = prefix.rsplit("/registry/", 1)[1]
    else:
        prefix = prefix.lstrip("/")
    name = urllib.parse.unquote(prefix)
    if not name or name.count("/") > 1:
        raise RuntimeError(f"cannot determine npm package name from {url}")
    return name


def _matches_url_pattern(url: str, pattern: re.Pattern[str]) -> bool:
    return pattern.match(url) is not None


def _collect_releases(
    texts: dict[Path, str], source_pattern: re.Pattern[str]
) -> tuple[set[tuple[str, str]], dict[Path, list[LockEntry]]]:
    releases: set[tuple[str, str]] = set()
    entries: dict[Path, list[LockEntry]] = {}
    for path, text in texts.items():
        try:
            lock = json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{path}: invalid JSON: {error}") from error
        packages = lock.get("packages")
        if not isinstance(lock.get("lockfileVersion"), int) or not isinstance(
            packages, dict
        ):
            raise RuntimeError(f"{path}: not a supported package-lock.json file")
        path_entries: list[LockEntry] = []
        for package_path, package in packages.items():
            if not isinstance(package, dict):
                continue
            version = package.get("version")
            resolved = package.get("resolved")
            integrity = package.get("integrity")
            if not (
                isinstance(version, str)
                and isinstance(resolved, str)
                and isinstance(integrity, str)
                and _matches_url_pattern(resolved, source_pattern)
            ):
                continue
            name = _package_name_from_url(resolved)
            releases.add((name, version))
            path_entries.append((package_path, version, resolved, integrity))
        entries[path] = path_entries
    return releases, entries


def _release_metadata(name: str, version: str, release: object) -> NpmMetadata:
    if not isinstance(release, dict):
        raise RuntimeError(f"{name}@{version}: registry release not found")
    dist = release.get("dist")
    if not isinstance(dist, dict):
        raise RuntimeError(f"{name}@{version}: registry release lacks dist metadata")
    tarball = dist.get("tarball")
    integrity = dist.get("integrity")
    shasum = dist.get("shasum")
    if not (
        isinstance(tarball, str)
        and tarball
        and (integrity is None or isinstance(integrity, str))
        and isinstance(shasum, str)
        and shasum
    ):
        raise RuntimeError(f"{name}@{version}: registry release has incomplete dist metadata")
    return tarball, integrity, shasum


def _fetch_release(
    key: tuple[str, str], registry: str, timeout: float
) -> tuple[tuple[str, str], NpmMetadata]:
    name, version = key
    url = (
        f"{registry.rstrip('/')}/{urllib.parse.quote(name, safe='@')}/"
        f"{urllib.parse.quote(version, safe='')}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "lock-registry-converter/1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    return key, _release_metadata(name, version, payload)


def _fetch_package_document(
    normalized_name: str,
    releases: set[tuple[str, str]],
    registry: str,
    timeout: float,
) -> dict[tuple[str, str], NpmMetadata]:
    url = f"{registry.rstrip('/')}/{urllib.parse.quote(normalized_name, safe='@')}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "lock-registry-converter/1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(
        versions := payload.get("versions"), dict
    ):
        raise RuntimeError(f"{normalized_name}: registry package document lacks versions")
    return {
        (name, version): _release_metadata(name, version, versions.get(version))
        for name, version in releases
    }


def _load_with_preflight(
    keys: list[_Key],
    fetch: Callable[[_Key], tuple[_Key, _Value]],
    jobs: int,
) -> dict[_Key, _Value]:
    if not keys:
        return {}

    first_key, first_value = fetch(keys[0])
    metadata = {first_key: first_value}
    remaining = keys[1:]
    next_index = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = set()
        while next_index < len(remaining) and len(futures) < jobs:
            futures.add(executor.submit(fetch, remaining[next_index]))
            next_index += 1
        while futures:
            completed, _ = concurrent.futures.wait(
                futures, return_when=concurrent.futures.FIRST_COMPLETED
            )
            try:
                results = [future.result() for future in completed]
            except Exception:
                for future in futures:
                    future.cancel()
                raise
            futures.difference_update(completed)
            metadata.update(results)
            while next_index < len(remaining) and len(futures) < jobs:
                futures.add(executor.submit(fetch, remaining[next_index]))
                next_index += 1
    return metadata


def _load_metadata(
    releases: set[tuple[str, str]], registry: str, jobs: int, timeout: float
) -> dict[tuple[str, str], NpmMetadata]:
    return _load_with_preflight(
        sorted(releases),
        lambda release: _fetch_release(release, registry, timeout),
        jobs,
    )


def _normalize_package_name(name: str) -> str:
    return name.lower()


def _load_mirror_metadata(
    releases: set[tuple[str, str]], registry: str, jobs: int, timeout: float
) -> dict[tuple[str, str], NpmMetadata]:
    releases_by_name: dict[str, set[tuple[str, str]]] = {}
    for release in releases:
        normalized_name = _normalize_package_name(release[0])
        releases_by_name.setdefault(normalized_name, set()).add(release)
    metadata_by_name = _load_with_preflight(
        sorted(releases_by_name),
        lambda name: (
            name,
            _fetch_package_document(
                name, releases_by_name[name], registry, timeout
            ),
        ),
        jobs,
    )
    return {
        release: metadata_by_name[normalized_name][release]
        for normalized_name, grouped_releases in releases_by_name.items()
        for release in grouped_releases
    }


def _sha1_sri(shasum: str) -> str:
    try:
        digest = bytes.fromhex(shasum)
    except ValueError as error:
        raise RuntimeError(f"invalid public npm SHA-1: {shasum}") from error
    if len(digest) != 20:
        raise RuntimeError(f"invalid public npm SHA-1: {shasum}")
    return f"sha1-{base64.b64encode(digest).decode('ascii')}"


def _strongest_integrity(integrity: Optional[str], shasum: str) -> str:
    return integrity or _sha1_sri(shasum)


def _rewrite_text(
    path: Path,
    text: str,
    entries: list[LockEntry],
    public_metadata: dict[tuple[str, str], NpmMetadata],
    target_metadata: dict[tuple[str, str], NpmMetadata],
    target: str,
    mirror_pattern: re.Pattern[str],
    public_registry: str = _DEFAULT_PUBLIC_REGISTRY,
) -> tuple[str, int]:
    replacements: Counter[tuple[str, str, str, str]] = Counter()
    for package_path, version, old_url, old_integrity in entries:
        name = _package_name_from_url(old_url)
        target_url, target_integrity, target_shasum = target_metadata[(name, version)]
        if target == "mirror":
            if not _matches_url_pattern(target_url, mirror_pattern):
                raise RuntimeError(
                    f"{path}: {package_path}: mirror returned a non-mirror URL "
                    f"for {name}@{version}"
                )
            mirror_sha1_integrity = _sha1_sri(target_shasum)
            if (
                target_integrity is not None
                and old_integrity not in {target_integrity, mirror_sha1_integrity}
            ):
                raise RuntimeError(
                    f"{path}: {package_path}: mirror integrity mismatch "
                    f"for {name}@{version}"
                )
            if old_integrity.startswith("sha1-") and old_integrity != mirror_sha1_integrity:
                raise RuntimeError(
                    f"{path}: {package_path}: mirror SHA-1 mismatch for {name}@{version}"
                )
            replacement_integrity = target_integrity or old_integrity
        else:
            _, published_integrity, public_shasum = public_metadata[(name, version)]
            public_integrity = _strongest_integrity(
                published_integrity, public_shasum
            )
            if old_integrity not in {public_integrity, _sha1_sri(public_shasum)}:
                raise RuntimeError(
                    f"{path}: {package_path}: integrity mismatch for {name}@{version}"
                )
            replacement_integrity = public_integrity
        old_filename = urllib.parse.unquote(urllib.parse.urlsplit(old_url).path.rsplit("/", 1)[-1])
        target_filename = urllib.parse.unquote(
            urllib.parse.urlsplit(target_url).path.rsplit("/", 1)[-1]
        )
        if old_filename != target_filename:
            raise RuntimeError(
                f"{path}: {package_path}: target release filename differs: "
                f"{old_filename} != {target_filename}"
            )
        replacements[
            (old_url, old_integrity, target_url, replacement_integrity)
        ] += 1

    rewritten = text
    replaced = 0
    for (old_url, old_integrity, public_url, public_integrity), expected in replacements.items():
        pattern = re.compile(
            rf'("resolved"\s*:\s*){re.escape(json.dumps(old_url))}'
            rf'(\s*,\s*"integrity"\s*:\s*){re.escape(json.dumps(old_integrity))}'
        )
        replacement = (
            rf"\g<1>{json.dumps(public_url)}"
            rf"\g<2>{json.dumps(public_integrity)}"
        )
        rewritten, count = pattern.subn(replacement, rewritten)
        if count != expected:
            raise RuntimeError(
                f"{path}: expected {expected} matching resolved/integrity pairs, "
                f"found {count}"
            )
        replaced += count

    if target == "public" and _PRIVATE_HOST_PATTERN.search(rewritten):
        raise RuntimeError(f"{path}: private npm mirror host remains after conversion")
    if target == "mirror" and re.search(
        rf'("resolved"\s*:\s*)"{re.escape(public_registry)}/',
        rewritten,
    ):
        raise RuntimeError(f"{path}: public npm registry URL remains after conversion")
    return rewritten, replaced


def _write_atomic(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        temporary.write(text)
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, mode)
    os.replace(temporary_path, path)


def _known_source_pattern(
    public_registry: str, mirror_pattern: re.Pattern[str]
) -> re.Pattern[str]:
    return re.compile(
        rf"(?:{re.escape(public_registry)}/|{mirror_pattern.pattern})",
        mirror_pattern.flags,
    )


def _convert_texts(
    texts: dict[Path, str],
    mirror_pattern: re.Pattern[str],
    public_registry: str,
    mirror_registry: str,
    target: str,
    jobs: int,
    timeout: float,
    normalize: bool,
) -> tuple[dict[Path, str], int, int]:
    known_pattern = _known_source_pattern(public_registry, mirror_pattern)
    source_pattern = (
        known_pattern
        if normalize
        else (
            mirror_pattern
            if target == "public"
            else re.compile(rf"{re.escape(public_registry)}/")
        )
    )
    releases, entries = _collect_releases(texts, source_pattern)
    if not releases:
        if _collect_releases(texts, known_pattern)[0]:
            return {}, 0, 0
        raise RuntimeError("no packages use the configured mirror or public registry")

    if target == "public":
        public_metadata = _load_metadata(releases, public_registry, jobs, timeout)
        target_metadata = public_metadata
    else:
        public_metadata = {}
        target_metadata = _load_mirror_metadata(
            releases, mirror_registry, jobs, timeout
        )
    rewritten: dict[Path, str] = {}
    replaced = 0
    for path, text in texts.items():
        updated, count = _rewrite_text(
            path,
            text,
            entries[path],
            public_metadata,
            target_metadata,
            target,
            mirror_pattern,
            public_registry,
        )
        rewritten[path] = updated
        replaced += count
    return rewritten, len(releases), replaced


def main() -> int:
    args = _parse_args()
    try:
        mirror_pattern = re.compile(args.source_url_pattern)
    except re.error as error:
        raise RuntimeError(f"invalid --source-url-pattern: {error}") from error
    locks = [path.resolve() for path in args.locks]
    for path in locks:
        if not path.is_file():
            raise RuntimeError(f"{path}: file not found")

    texts = {path: path.read_text(encoding="utf-8") for path in locks}
    public_registry = args.public_registry.rstrip("/")
    mirror_registry = args.mirror_registry.rstrip("/")
    rewritten, release_count, replaced = _convert_texts(
        texts,
        mirror_pattern,
        public_registry,
        mirror_registry,
        args.target,
        args.jobs,
        args.timeout,
        args.normalize,
    )
    if release_count == 0:
        print(
            f"No changes needed: {len(locks)} package-lock.json file(s) already "
            f"use {args.target} metadata"
        )
        return 0

    changed_lock_count = sum(
        rewritten[path] != texts[path] for path in rewritten
    )
    if not args.dry_run:
        for path, text in rewritten.items():
            if text != texts[path]:
                _write_atomic(path, text)

    action = "Validated" if args.dry_run or not changed_lock_count else "Converted"
    print(
        f"{action} {release_count} npm package releases and {replaced} artifact "
        f"entries to {args.target} metadata across {len(locks)} lockfiles"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
