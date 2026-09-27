#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from azure.identity import ManagedIdentityCredential
from huggingface_hub import HfApi, snapshot_download


def load_key_vault_secret(secret_url: str) -> str:
    credential = ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID"))
    access_token = credential.get_token("https://vault.azure.net/.default").token

    separator = "&" if "?" in secret_url else "?"
    request = urllib.request.Request(
        secret_url + separator + "api-version=7.4",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.load(response)["value"]
    if not value:
        raise RuntimeError("Key Vault secret is empty")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--model",
        action="append",
        nargs=3,
        metavar=("DIRECTORY", "REPOSITORY", "REVISION"),
        required=True,
    )
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN")
    if not token:
        secret_url = os.environ.get("HF_TOKEN_SECRET_URL")
        if not secret_url:
            raise RuntimeError("HF_TOKEN or HF_TOKEN_SECRET_URL is required")
        token = load_key_vault_secret(secret_url)
        print("[cache] loaded Hugging Face token from Key Vault")

    args.output.mkdir(parents=True, exist_ok=True)
    api = HfApi(token=token)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "huggingface_hub_version": __import__("huggingface_hub").__version__,
        "models": [],
    }

    for directory, repository, revision in args.model:
        if len(revision) != 40 or any(char not in "0123456789abcdefABCDEF" for char in revision):
            raise ValueError(f"revision must be an immutable 40-hex commit: {revision}")
        resolved = api.model_info(repository, revision=revision).sha
        if resolved != revision:
            raise RuntimeError(f"{repository} resolved to {resolved}, expected {revision}")

        target = args.output / directory
        snapshot_download(
            repo_id=repository,
            revision=revision,
            local_dir=target,
            token=token,
        )
        files = sorted(
            path
            for path in target.rglob("*")
            if path.is_file() and ".cache" not in path.parts
        )
        manifest["models"].append(
            {
                "directory": directory,
                "repository": repository,
                "revision": revision,
                "file_count": len(files),
                "total_bytes": sum(path.stat().st_size for path in files),
                "files": [
                    {
                        "path": path.relative_to(target).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                    for path in files
                ],
            }
        )
        print(f"[cache] verified {repository}@{revision}: {len(files)} files")

    manifest_path = args.output / "model-cache-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"MODEL_CACHE_RESULT=PASS manifest={manifest_path}")


if __name__ == "__main__":
    main()
