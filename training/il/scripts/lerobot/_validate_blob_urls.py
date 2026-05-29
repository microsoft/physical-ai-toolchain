"""Validate ``--blob-url`` arguments for LeRobot training submission scripts.

Invoked from ``submit-azureml-lerobot-training.sh`` and
``submit-osmo-lerobot-training.sh`` to enforce that every user-supplied blob
URL targets ``https://ACCOUNT.blob.core.windows.net/CONTAINER/PREFIX`` before
the job is created. Exits non-zero with a descriptive message on the first
invalid URL so misconfigured submissions fail at the laptop, not after
container start-up.

Usage:
    python3 path/to/_validate_blob_urls.py URL [URL ...]
"""

from __future__ import annotations

import sys
from urllib.parse import urlparse

_MAX_BLOB_URLS = 64
_BLOB_HOST_SUFFIX = ".blob.core.windows.net"


def validate(urls: list[str]) -> None:
    """Raise :class:`SystemExit` on the first malformed blob URL.

    Mirrors the constraints applied by
    :func:`training.il.scripts.lerobot.download_dataset.parse_blob_url` so a
    URL accepted here will also be accepted by the downloader at run-time.
    """
    if not urls:
        raise SystemExit("--blob-url: at least one Blob URL is required.")
    if len(urls) > _MAX_BLOB_URLS:
        raise SystemExit(f"--blob-url: too many Blob URLs ({len(urls)}); maximum is {_MAX_BLOB_URLS}.")

    for url in urls:
        if any(c < " " or c == "\x7f" for c in url):
            raise SystemExit(
                f"--blob-url must not contain control characters (CR, LF, NUL, …); "
                f"these enable log injection in downstream surfaces: {url!r}"
            )

        lowered = url.lower()
        if lowered.startswith(("azureml:", "azureml://")):
            raise SystemExit(
                f"--blob-url does not accept AzureML asset identifiers: {url}. "
                "Resolve the asset to a direct Azure Blob URL first."
            )
        if lowered.startswith(("wasbs://", "abfss://")):
            raise SystemExit(f"--blob-url supports HTTPS Azure Blob URLs only, not {url}.")

        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise SystemExit(f"--blob-url must be an HTTPS Azure Blob URL: {url}")
        if parsed.query:
            raise SystemExit(f"--blob-url must not include a query string: {url}")
        if parsed.fragment:
            raise SystemExit(f"--blob-url must not include a fragment: {url}")
        if parsed.port is not None:
            raise SystemExit(f"--blob-url must not include an explicit port: {url}")
        if "@" in (parsed.netloc or ""):
            raise SystemExit(
                f"--blob-url must not include userinfo (user:password@host); "
                f"such credentials are silently stripped by urlparse but propagate "
                f"verbatim into job manifests, pod env, and logs: {url}"
            )

        hostname = (parsed.hostname or "").lower()
        if not hostname.endswith(_BLOB_HOST_SUFFIX):
            raise SystemExit(f"--blob-url must target https://ACCOUNT{_BLOB_HOST_SUFFIX}/CONTAINER/PREFIX: {url}")

        account = hostname[: -len(_BLOB_HOST_SUFFIX)]
        path = parsed.path.lstrip("/")
        parts = path.split("/", 1)
        container = parts[0] if parts else ""
        prefix = parts[1].strip("/") if len(parts) > 1 else ""
        if not account or "." in account:
            raise SystemExit(f"--blob-url has an invalid storage account name: {url}")
        if not container:
            raise SystemExit(f"--blob-url must include a Blob container: {url}")
        if not prefix:
            raise SystemExit(f"--blob-url must include a non-empty Blob prefix under the container: {url}")


if __name__ == "__main__":
    validate(sys.argv[1:])
