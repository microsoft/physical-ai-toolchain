"""Idempotent expiring operator preflight resources."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ..operator.models import PreflightLifecycle, PreflightRequest, PreflightResult
from ..operator.preflight import OperatorPreflightRunner
from ..operator.profiles import OperatorProfile


class OperatorPreflightConflictError(RuntimeError):
    pass


class OperatorPreflightNotFoundError(KeyError):
    pass


class OperatorPreflightService:
    def __init__(
        self,
        *,
        profiles: dict[str, OperatorProfile],
        runner: OperatorPreflightRunner,
        now=None,
        ttl=timedelta(seconds=30),
    ) -> None:
        self.profiles = profiles
        self.runner = runner
        self.now = now or (lambda: datetime.now(UTC))
        self.ttl = ttl
        self._results: dict[str, PreflightResult] = {}
        self._commands: dict[str, tuple[PreflightRequest, str]] = {}
        self._cancel_commands: dict[str, str] = {}

    def create(self, request: PreflightRequest) -> PreflightResult:
        if previous := self._commands.get(request.command_id):
            if previous[0] != request:
                raise OperatorPreflightConflictError(
                    "command_id was already used with another payload"
                )
            return self.get(previous[1])
        profile = self.profiles.get(request.profile)
        if profile is None:
            raise OperatorPreflightNotFoundError(request.profile)
        run = self.runner.run(
            profile, mode=request.mode, upload_requested=request.upload_requested
        )
        created_at = self.now()
        result = PreflightResult(
            preflight_id=str(uuid4()),
            lifecycle=PreflightLifecycle.COMPLETED,
            profile=profile.name,
            mode=request.mode,
            profile_fingerprint=profile.fingerprint,
            resource_fingerprint=run.resource_fingerprint,
            created_at=created_at,
            expires_at=created_at + self.ttl,
            checks=run.checks,
            ownership_complete=run.ownership_complete,
            start_eligible=run.start_eligible,
        )
        self._results[result.preflight_id] = result
        self._commands[request.command_id] = (request, result.preflight_id)
        self._trim()
        return result

    def get(self, preflight_id: str) -> PreflightResult:
        result = self._results.get(preflight_id)
        if result is None:
            raise OperatorPreflightNotFoundError(preflight_id)
        if (
            result.lifecycle is PreflightLifecycle.COMPLETED
            and self.now() >= result.expires_at
        ):
            result = result.model_copy(
                update={
                    "lifecycle": PreflightLifecycle.EXPIRED,
                    "start_eligible": False,
                }
            )
            self._results[preflight_id] = result
        return result

    def cancel(self, preflight_id: str, *, command_id: str) -> PreflightResult:
        if previous := self._cancel_commands.get(command_id):
            if previous != preflight_id:
                raise OperatorPreflightConflictError(
                    "command_id was already used with another payload"
                )
            return self.get(preflight_id)
        result = self.get(preflight_id)
        if result.lifecycle is PreflightLifecycle.CANCELLED:
            self._cancel_commands[command_id] = preflight_id
            return result
        result = result.model_copy(
            update={"lifecycle": PreflightLifecycle.CANCELLED, "start_eligible": False}
        )
        self._results[preflight_id] = result
        self._cancel_commands[command_id] = preflight_id
        return result

    def consume(self, preflight_id: str) -> PreflightResult:
        """Atomically consume one current eligible preflight for session start."""
        result = self.get(preflight_id)
        if (
            result.lifecycle is not PreflightLifecycle.COMPLETED
            or not result.start_eligible
        ):
            raise OperatorPreflightConflictError(
                "Preflight is not eligible for consumption"
            )
        result = result.model_copy(
            update={"lifecycle": PreflightLifecycle.CONSUMED, "start_eligible": False}
        )
        self._results[preflight_id] = result
        return result

    def _trim(self) -> None:
        while len(self._results) > 128:
            oldest_id = next(iter(self._results))
            self._results.pop(oldest_id, None)
            self._commands = {
                command_id: value
                for command_id, value in self._commands.items()
                if value[1] != oldest_id
            }
            self._cancel_commands = {
                command_id: preflight_id
                for command_id, preflight_id in self._cancel_commands.items()
                if preflight_id != oldest_id
            }
