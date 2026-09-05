#!/usr/bin/env python3
"""Validate OpenVEX documents against the official schema and repository policy."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlsplit

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ModuleNotFoundError:
    print("OpenVEX validation requires the Python jsonschema package with format support.", file=sys.stderr)
    raise SystemExit(2) from None

_CONTEXT = "https://openvex.dev/ns/v0.2.0"
_DIGEST_PURL_PREFIX = "pkg:oci/"
_MAX_VERSION = 9_007_199_254_740_990


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_digest_purl(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PURL_PREFIX):
        return False
    parsed = urlsplit(value)
    if parsed.scheme != "pkg" or parsed.fragment or not parsed.path.startswith("oci/"):
        return False
    package, separator, digest = parsed.path.removeprefix("oci/").rpartition("@sha256:")
    if not package or not separator:
        return False
    try:
        qualifiers = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    repositories = qualifiers.get("repository_url", [])
    return (
        len(repositories) == 1
        and bool(repositories[0].strip())
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def _is_valid_datetime(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _repository_errors(document: dict[str, Any], image_digest: str | None) -> list[str]:
    errors = []
    if document.get("@context") != _CONTEXT:
        errors.append(f"@context must be {_CONTEXT}")
    if not _non_empty_string(document.get("@id")) or not urlparse(document["@id"]).scheme:
        errors.append("@id must be an absolute IRI")
    if not _non_empty_string(document.get("author")):
        errors.append("author must be a non-empty string")
    if not _non_empty_string(document.get("tooling")):
        errors.append("tooling must be a non-empty string")
    for field in ("timestamp", "last_updated"):
        if field in document and not _is_valid_datetime(document[field]):
            errors.append(f"{field} must be a valid RFC 3339 date-time")

    version = document.get("version")
    if isinstance(version, int) and version > _MAX_VERSION:
        errors.append("version cannot be incremented safely")

    pairs = set()
    matching_digest = image_digest is None
    for statement in document.get("statements", []):
        for field in ("timestamp", "last_updated", "action_statement_timestamp"):
            if field in statement and not _is_valid_datetime(statement[field]):
                errors.append(f"statement {field} must be a valid RFC 3339 date-time")

        products = statement.get("products")
        if not isinstance(products, list) or not products:
            errors.append("every statement must identify at least one product")
            continue

        vulnerability = statement.get("vulnerability", {})
        vulnerability_name = vulnerability.get("name")
        if not _non_empty_string(vulnerability_name):
            errors.append("every statement must identify its vulnerability by name")

        for product in products:
            product_id = product.get("@id") if isinstance(product, dict) else None
            if not _is_digest_purl(product_id):
                errors.append("every product must use a digest-pinned OCI package URL")
                continue
            pair = (vulnerability_name, product_id)
            if pair in pairs:
                errors.append(f"duplicate vulnerability/product pair: {vulnerability_name}, {product_id}")
            pairs.add(pair)
            if image_digest is not None and f"@{image_digest}?" in product_id:
                matching_digest = True

        status = statement.get("status")
        status_notes = statement.get("status_notes")
        if status == "not_affected" and not _non_empty_string(status_notes):
            errors.append("not_affected statements require status_notes")
        if status == "affected" and (
            not _non_empty_string(statement.get("action_statement")) or not _non_empty_string(status_notes)
        ):
            errors.append("affected statements require action_statement and status_notes")
        if status == "fixed" and not _non_empty_string(status_notes):
            errors.append("fixed statements require status_notes")

    if not matching_digest:
        errors.append(f"document does not identify image digest {image_digest}")
    return errors


def validate_document(document_path: Path, schema_path: Path, image_digest: str | None) -> list[str]:
    with schema_path.open(encoding="utf-8") as schema_file:
        schema = json.load(schema_file)
    with document_path.open(encoding="utf-8") as document_file:
        document = json.load(document_file)

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = [
        f"{'.'.join(str(part) for part in error.absolute_path) or '<document>'}: {error.message}"
        for error in sorted(
            validator.iter_errors(document),
            key=lambda error: ".".join(str(part) for part in error.absolute_path),
        )
    ]
    if isinstance(document, dict):
        errors.extend(_repository_errors(document, image_digest))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("document", type=Path)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--image-digest")
    args = parser.parse_args()

    try:
        errors = validate_document(args.document, args.schema, args.image_digest)
    except (OSError, json.JSONDecodeError) as error:
        print(f"OpenVEX validation failed: {error}", file=sys.stderr)
        return 1

    if errors:
        for error in errors:
            print(f"OpenVEX validation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
