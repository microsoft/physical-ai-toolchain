"""Durable transactions for judge state, independent of inference and HTTP."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Protocol


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "sequence": 0,
        "jobs": {},
        "generations": {},
        "resets": {},
        "approvals": {},
        "capacities": {},
    }


def decode_state(content: bytes) -> dict[str, Any]:
    state = json.loads(content)
    if not isinstance(state, dict) or state.get("schema_version") != 1 or not isinstance(state.get("jobs"), dict):
        raise ValueError("Invalid durable judge state")
    return state


class JobStore(Protocol):
    def transaction(self) -> Any: ...

    async def read(self) -> dict[str, Any]: ...


class LocalJobStore:
    """Use an OS-backed lock and fsynced atomic publication on a shared filesystem."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / "state.json"

    def _read(self) -> dict[str, Any]:
        self.path.resolve().relative_to(self.root)
        try:
            return decode_state(self.path.read_bytes())
        except FileNotFoundError:
            return empty_state()

    async def read(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._read)

    def _write(self, state: dict[str, Any]) -> None:
        descriptor, name = tempfile.mkstemp(dir=self.root, prefix="state-", suffix=".tmp")
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.root / "state.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        if os.fstat(descriptor).st_size == 0:
                            os.write(descriptor, b"\0")
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, PermissionError):
                    await asyncio.sleep(0.025)
            state = await self.read()
            yield state
            publication = asyncio.create_task(asyncio.to_thread(self._write, state))
            try:
                await asyncio.shield(publication)
            except asyncio.CancelledError:
                await publication
                raise
        finally:
            os.close(descriptor)


class BlobJobStore:
    """Serialize state changes with renewable Blob leases and matching ETags."""

    def __init__(self, blob_client: Any) -> None:
        self.client = blob_client

    async def read(self) -> dict[str, Any]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            download = await self.client.download_blob()
            return decode_state(await download.readall())
        except ResourceNotFoundError:
            return empty_state()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[dict[str, Any]]:
        from azure.core import MatchConditions
        from azure.core.exceptions import (
            HttpResponseError,
            ResourceExistsError,
            ResourceModifiedError,
            ResourceNotFoundError,
        )

        deadline = time.monotonic() + 30
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Durable judge state lease unavailable")
            try:
                lease = await self.client.acquire_lease(lease_duration=60)
                break
            except ResourceNotFoundError:
                with suppress(ResourceExistsError, ResourceModifiedError):
                    await self.client.upload_blob(json.dumps(empty_state()).encode(), overwrite=False)
            except HttpResponseError as error:
                if error.status_code != 409 or time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.1)
        renewal_error = []

        async def renew() -> None:
            try:
                while True:
                    await asyncio.sleep(20)
                    await lease.renew()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                renewal_error.append(type(error).__name__)

        renewal = asyncio.create_task(renew())
        try:
            download = await self.client.download_blob(lease=lease)
            revision = download.properties.etag
            if not revision:
                raise ValueError("Missing durable judge state revision")
            state = decode_state(await download.readall())
            yield state
            if renewal_error:
                raise RuntimeError("Durable judge state lease lost")
            await self.client.upload_blob(
                json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
                overwrite=True,
                lease=lease,
                etag=revision,
                match_condition=MatchConditions.IfNotModified,
            )
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal
            with suppress(HttpResponseError):
                await lease.release()
