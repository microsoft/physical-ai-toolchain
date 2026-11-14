"""Run one supervised release process under a fenced lease with heartbeat-driven stop."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from physical_ai_curation.release.jobs import AuthorityError, CancellationError
from physical_ai_curation.release.supervision import (
    DEFAULT_FILE_BYTES,
    DEFAULT_GRACE_SECONDS,
    DEFAULT_LIVENESS_SECONDS,
    MAX_LIVENESS_SECONDS,
    ProcessSupervisor,
    StopProof,
    SupervisedRuntime,
    bounded_seconds,
)

DEFAULT_HEARTBEAT_SECONDS = 5.0
MAX_HEARTBEAT_SECONDS = 3600.0
Reason = Literal["exited", "cancelled", "lease_lost", "timed_out"]


class _LeaseStore(Protocol):
    def heartbeat(self, job_id: str, fence: int) -> bool:
        raise NotImplementedError

    def confirm_stopped(self, job_id: str, fence: int) -> None:
        raise NotImplementedError

    def runtime_proof_path(
        self,
        job_id: str,
        fence: int,
        *,
        effect_path: str | None = None,
        effect_paths: Sequence[str] | None = None,
        reconciliation: bool = False,
    ) -> Path:
        raise NotImplementedError

    def register_runtime(
        self,
        job_id: str,
        fence: int,
        runtime: SupervisedRuntime,
        *,
        effect_path: str | None = None,
        effect_paths: Sequence[str] | None = None,
        reconciliation: bool = False,
    ) -> None:
        raise NotImplementedError

    def confirm_runtime_stopped(self, job_id: str, fence: int, proof: StopProof) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class SupervisedOutcome:
    """Fixed-category result with the guardian's stop proof; never carries argv, paths, or output."""

    reason: Reason
    returncode: int | None
    proof: StopProof


def run_supervised(
    store: _LeaseStore,
    job_id: str,
    fence: int,
    argv: Sequence[str],
    *,
    memory_bytes: int,
    file_bytes: int = DEFAULT_FILE_BYTES,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    cwd: Path | None = None,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
    liveness_seconds: float | None = None,
    effect_path: str | None = None,
    effect_paths: Sequence[str] | None = None,
    inherit_fds: Sequence[int] = (),
    proof_path: Path | None = None,
    register_runtime: Callable[[SupervisedRuntime], None] | None = None,
    confirm_runtime_stopped: Callable[[StopProof], None] | None = None,
    reconciliation: bool = False,
    timeout_seconds: float | None = None,
) -> SupervisedOutcome:
    """Run argv until exit, cancellation, or lease loss.

    The effect starts only after register_runtime returns. The store is confirmed stopped only
    after the guardian's proof is obtained; an unresolved stop raises and confirms nothing.
    """
    heartbeat = bounded_seconds(heartbeat_seconds, low=0.001, high=MAX_HEARTBEAT_SECONDS, name="heartbeat interval")
    deadline = (
        None
        if timeout_seconds is None
        else time.monotonic()
        + bounded_seconds(
            timeout_seconds,
            low=0.001,
            high=3600,
            name="runtime timeout",
        )
    )
    liveness = (
        min(max(DEFAULT_LIVENESS_SECONDS, 4 * heartbeat), MAX_LIVENESS_SECONDS)
        if liveness_seconds is None
        else bounded_seconds(liveness_seconds, low=4 * heartbeat, high=MAX_LIVENESS_SECONDS, name="guardian liveness")
    )

    scope: dict[str, Sequence[str]] = {} if effect_paths is None else {"effect_paths": effect_paths}

    def register(runtime: SupervisedRuntime) -> None:
        if reconciliation:
            store.register_runtime(job_id, fence, runtime, effect_path=effect_path, reconciliation=True, **scope)
        elif effect_paths is not None:
            store.register_runtime(job_id, fence, runtime, **scope)
        elif effect_path is None:
            store.register_runtime(job_id, fence, runtime)
        else:
            store.register_runtime(job_id, fence, runtime, effect_path=effect_path)
        if register_runtime is not None:
            register_runtime(runtime)

    if store.heartbeat(job_id, fence) and not reconciliation:
        store.confirm_stopped(job_id, fence)
        raise CancellationError("cancellation requested")
    supervisor = ProcessSupervisor(
        argv,
        memory_bytes=memory_bytes,
        file_bytes=file_bytes,
        cwd=cwd,
        grace_seconds=grace_seconds,
        liveness_seconds=liveness,
        inherit_fds=inherit_fds,
        proof_path=proof_path
        or (
            store.runtime_proof_path(job_id, fence, effect_path=effect_path, reconciliation=True, **scope)
            if reconciliation
            else store.runtime_proof_path(job_id, fence, effect_path=effect_path, **scope)
        ),
        on_started=register,
    )
    supervisor.start()
    reason: Reason = "exited"
    try:
        while True:
            remaining = heartbeat if deadline is None else min(heartbeat, deadline - time.monotonic())
            if remaining <= 0:
                reason = "timed_out"
                break
            if supervisor.wait_for_exit(remaining):
                break
            try:
                cancelled = store.heartbeat(job_id, fence)
            except AuthorityError:
                reason = "lease_lost"
                break
            if cancelled and not reconciliation:
                reason = "cancelled"
                break
            supervisor.ping()
    finally:
        proof = supervisor.stop()
        store.confirm_runtime_stopped(job_id, fence, proof)
        if confirm_runtime_stopped is not None:
            confirm_runtime_stopped(proof)
        store.confirm_stopped(job_id, fence)
    return SupervisedOutcome(reason=reason, returncode=proof.leader_exit_code, proof=proof)
