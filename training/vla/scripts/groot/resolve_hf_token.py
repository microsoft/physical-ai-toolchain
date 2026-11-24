"""Resolve a Hugging Face token from Azure Key Vault inside an Azure ML job.

Azure ML jobs expose the compute identity through a per-job MSI endpoint rather
than VM IMDS. AmlCompute sets ``MSI_ENDPOINT`` and ``MSI_SECRET``; Kubernetes
compute sets only ``MSI_ENDPOINT``, served by the job's identity sidecar.

The token is never printed or written to disk: the script replaces itself with
the given command and passes the token only through that command's ``HF_TOKEN``
environment variable.
"""

# cspell:ignore clientid

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence

KEY_VAULT_RESOURCE = "https://vault.azure.net"
MSI_API_VERSION = "2017-09-01"
KEY_VAULT_API_VERSION = "7.4"
REQUEST_TIMEOUT_SECONDS = 30


def _append_query(url: str, query: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{query}"


def build_identity_request(environ: Mapping[str, str]) -> urllib.request.Request:
    """Build the Key Vault access-token request for the Azure ML job identity."""
    msi_endpoint = environ.get("MSI_ENDPOINT")
    if not msi_endpoint:
        raise RuntimeError(
            "MSI_ENDPOINT is required to read HF_TOKEN_SECRET_URL; submit the job with a managed identity"
        )
    query = {"api-version": MSI_API_VERSION, "resource": KEY_VAULT_RESOURCE}
    client_id = environ.get("AZURE_CLIENT_ID") or environ.get("DEFAULT_IDENTITY_CLIENT_ID")
    if client_id:
        query["clientid"] = client_id
    msi_secret = environ.get("MSI_SECRET")
    headers = {"secret": msi_secret} if msi_secret else {}
    return urllib.request.Request(_append_query(msi_endpoint, urllib.parse.urlencode(query)), headers=headers)


def build_secret_request(secret_url: str, access_token: str) -> urllib.request.Request:
    """Build the Key Vault get-secret request."""
    return urllib.request.Request(
        _append_query(secret_url, f"api-version={KEY_VAULT_API_VERSION}"),
        headers={"Authorization": f"Bearer {access_token}"},
    )


def _read_json(opener: urllib.request.OpenerDirector, request: urllib.request.Request) -> dict[str, object]:
    with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected response from {urllib.parse.urlsplit(request.full_url).netloc}")
    return payload


def resolve_token(
    environ: Mapping[str, str],
    *,
    identity_opener: urllib.request.OpenerDirector | None = None,
    key_vault_opener: urllib.request.OpenerDirector | None = None,
) -> str:
    """Return the Hugging Face token stored at ``HF_TOKEN_SECRET_URL``."""
    secret_url = environ.get("HF_TOKEN_SECRET_URL")
    if not secret_url:
        raise RuntimeError("HF_TOKEN_SECRET_URL is required")
    # The identity endpoint is local to the job and must bypass any configured proxy.
    identity_opener = identity_opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    key_vault_opener = key_vault_opener or urllib.request.build_opener()

    access_token = _read_json(identity_opener, build_identity_request(environ)).get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("Azure ML job identity returned no access token")

    secret_value = _read_json(key_vault_opener, build_secret_request(secret_url, access_token)).get("value")
    if not isinstance(secret_value, str) or not secret_value:
        raise RuntimeError("Hugging Face token secret is empty")
    return secret_value


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run with HF_TOKEN set, after --")
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command to run is required after --")
    return args


def main(
    argv: Sequence[str] | None = None,
    *,
    execute: Callable[[str, list[str], dict[str, str]], object] = os.execvpe,
) -> int:
    args = _parse_args(argv)
    try:
        token = resolve_token(os.environ)
    except (RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("[azureml] loaded Hugging Face token from Key Vault", flush=True)
    sys.stderr.flush()
    execute(args.command[0], args.command, {**os.environ, "HF_TOKEN": token})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
