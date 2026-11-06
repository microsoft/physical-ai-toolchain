"""Durable lifecycle refusals retain ownership, effects, and admitted capacity."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
from physical_ai_curation.release.canonical import canonical_json_bytes
from physical_ai_curation.release.jobs import (
    AuthorityError,
    CancellationError,
    JobConflictError,
    ReleaseJobError,
    ReleaseJobStore,
)
from physical_ai_curation.release.models import MARKER_PATH
from physical_ai_curation.release.supervision import ProcessIdentity, SupervisedRuntime
from test_release_jobs import (
    ATTEMPT,
    BLOB,
    COMMIT_DIGEST,
    CONTROLS,
    DIGEST,
    OTHER_ATTEMPT,
    OWNER,
    SCOPE,
    FakeClock,
    _committed_job,
    _request,
    _sealed_job,
    _store,
)

PATH = "data/frames.parquet"


def _running(tmp_path: Path, *, blob: bool = False) -> tuple[ReleaseJobStore, str, int]:
    store = _store(tmp_path / "ledger", FakeClock())
    request = _request(tmp_path / "output")
    if blob:
        request = replace(request, destination=BLOB, route="blob")
    job_id = store.accept(request).job_id
    fence = store.claim(job_id, "worker")
    return store, job_id, fence


def _register(
    store: ReleaseJobStore,
    job_id: str,
    fence: int,
    *,
    effect_path: str | None = None,
    reconciliation: bool = False,
) -> None:
    identity = ProcessIdentity.of(os.getpid())
    runtime = SupervisedRuntime(
        identity, identity,
        store.runtime_proof_path(job_id, fence, effect_path=effect_path, reconciliation=reconciliation),
    )
    store.register_runtime(job_id, fence, runtime, effect_path=effect_path, reconciliation=reconciliation)


@pytest.mark.parametrize("digest", ["", "different-request"])
def test_accepted_request_replay_refuses_changed_client_identity(tmp_path: Path, digest: str) -> None:
    store, job_id, _ = _running(tmp_path)
    before = store.storage.snapshot()

    with pytest.raises(JobConflictError, match="different request"):
        store.accepted_request(scope=SCOPE, principal=OWNER, idempotency_key="key-1", client_request_digest=digest)

    assert store.storage.snapshot() == before
    assert store.accepted_request(scope=SCOPE, principal="other-user", idempotency_key="key-1") is None
    assert job_id in before


@pytest.mark.parametrize("state", ["cancelled", "committed", "needs_input"])
def test_recovery_does_not_reopen_terminal_or_input_refused_jobs(tmp_path: Path, state: str) -> None:
    if state == "committed":
        store = _store(tmp_path / "ledger", FakeClock())
        job_id, _ = _committed_job(store, tmp_path / "output")
    else:
        store, job_id, fence = _running(tmp_path)
        store.confirm_stopped(job_id, fence)
        if state == "cancelled":
            store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
            store.settle_cancellation(job_id, fence, output_removed=True)
        else:
            store.require_input(job_id, fence, "source_binding_changed")
    before = store.storage.snapshot()

    with pytest.raises(JobConflictError, match="new submission"):
        store.request_recovery(job_id, scope=SCOPE, principal=OWNER)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("failure", ["empty_request", "empty_binding", "not_intent", "not_generation"])
def test_execution_capture_rejects_invalid_envelope_atomically(tmp_path: Path, failure: str) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    intent = canonical_json_bytes({"schema_version": "curated-release-intent/v1"})
    generation = canonical_json_bytes({"schema_version": "curated-release-generation/v1"})
    if failure == "not_intent":
        intent = canonical_json_bytes({"schema_version": "other"})
    job_id = store.accept(_request(tmp_path / "output", frozen=intent)).job_id
    fence = store.claim(job_id, "worker")
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError, match=r"capture|accepted intent"):
        store.capture_execution(
            job_id,
            fence,
            intent_request=intent,
            frozen_request=b"" if failure == "empty_request" else b"{}" if failure == "not_generation" else generation,
            execution_binding=b"" if failure == "empty_binding" else b"binding",
            reserved_bytes=100,
        )

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    "failure", ["destination", "attempt", "route", "cancellation_without_recovery", "not_cancelled"]
)
def test_execution_binding_cannot_be_broadened_by_worker_configuration(tmp_path: Path, failure: str) -> None:
    store, job_id, fence = _running(tmp_path)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        store.validate_execution_binding(
            job_id,
            fence,
            tmp_path / ("elsewhere" if failure == "destination" else "output"),
            OTHER_ATTEMPT if failure == "attempt" else ATTEMPT,
            route="blob" if failure == "route" else "local",
            recovery=failure == "not_cancelled",
            cancellation=failure in {"not_cancelled", "cancellation_without_recovery"},
        )

    assert store.storage.snapshot() == before


def test_cancelled_execution_requires_explicit_cancellation_recovery(tmp_path: Path) -> None:
    store, job_id, fence = _running(tmp_path)
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)

    with pytest.raises(CancellationError):
        store.validate_execution_binding(job_id, fence, tmp_path / "output", ATTEMPT, route="local", recovery=True)
    store.validate_execution_binding(
        job_id, fence, tmp_path / "output", ATTEMPT, route="local", recovery=True, cancellation=True
    )
    assert store.get(job_id, scope=SCOPE, principal=OWNER).state == "cancelling"


@pytest.mark.parametrize("failure", ["scratch", "not_stopped", "sealed", "cancelled", "unknown_code"])
def test_input_refusal_does_not_hide_unresolved_work(tmp_path: Path, failure: str) -> None:
    store, job_id, fence = _running(tmp_path)
    if failure != "not_stopped":
        store.confirm_stopped(job_id, fence)
    if failure == "scratch":
        store.reserve_scratch(job_id, fence, 1)
    elif failure == "sealed":
        store.seal(job_id, fence, CONTROLS)
    elif failure == "cancelled":
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.require_input(
            job_id, fence, "untrusted_reason" if failure == "unknown_code" else "source_binding_changed"
        )

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("failure", ["already_reserved", "sealed", "cancelled", "capacity"])
def test_scratch_reservation_refusal_preserves_admitted_budget(tmp_path: Path, failure: str) -> None:
    store, job_id, fence = _running(tmp_path)
    if failure == "already_reserved":
        store.reserve_scratch(job_id, fence, 1)
    elif failure == "sealed":
        store.seal(job_id, fence, CONTROLS)
    elif failure == "cancelled":
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.reserve_scratch(job_id, fence, 1025 if failure == "capacity" else 1)

    assert store.storage.snapshot() == before


def test_measured_scratch_overrun_does_not_replace_previous_high_water(tmp_path: Path) -> None:
    store, job_id, fence = _running(tmp_path)
    store.reserve_scratch(job_id, fence, 10)
    store.record_scratch_usage(job_id, fence, 5)
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError, match="exceeds its admission"):
        store.record_scratch_usage(job_id, fence, 11)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("failure", ["empty", "operation", "category", "empty_counters", "negative_bytes", "duration"])
def test_invalid_io_observation_cannot_modify_accounting(tmp_path: Path, failure: str) -> None:
    store, job_id, fence = _running(tmp_path)
    observations = (
        []
        if failure == "empty"
        else [
            (
                "not-an-operation" if failure == "operation" else DIGEST,
                {}
                if failure == "empty_counters"
                else {"private": 1}
                if failure == "category"
                else {"source_read_bytes": -1 if failure == "negative_bytes" else 1},
                -1 if failure == "duration" else 0,
            )
        ]
    )
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.record_ios(job_id, fence, observations)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("operation", ["release_scratch", "authorize_scratch_cleanup", "seal", "recovery_effects"])
def test_active_runtime_prevents_closing_or_reusing_resources(tmp_path: Path, operation: str) -> None:
    store, job_id, fence = _running(tmp_path)
    _register(store, job_id, fence)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match=r"runtime|recovery"):
        if operation == "seal":
            store.seal(job_id, fence, CONTROLS)
        else:
            getattr(store, operation)(job_id, fence)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("failure", ["not_requested", "fence", "not_stopped", "effect", "scratch"])
def test_cancellation_cannot_release_capacity_before_obligations_close(tmp_path: Path, failure: str) -> None:
    store, job_id, fence = _running(tmp_path)
    if failure == "effect":
        store.begin_effect(job_id, fence, ATTEMPT, PATH, 1, DIGEST)
    if failure == "scratch":
        store.reserve_scratch(job_id, fence, 1)
    if failure != "not_stopped":
        store.confirm_stopped(job_id, fence)
    if failure != "not_requested":
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.settle_cancellation(job_id, fence + 1 if failure == "fence" else fence, output_removed=True)

    assert store.storage.snapshot() == before
    assert before[job_id]["reservation"]["status"] == "reserved"


@pytest.mark.parametrize("failure", ["wrong_controls", "digest", "changed_seal", "cancelled", "scratch", "effect"])
def test_seal_refusals_do_not_close_a_mutable_payload_ledger(tmp_path: Path, failure: str) -> None:
    store, job_id, fence = _running(tmp_path)
    controls = dict(CONTROLS)
    if failure == "wrong_controls":
        controls = {}
    elif failure == "digest":
        controls[next(iter(controls))] = (1, "invalid")
    elif failure == "changed_seal":
        store.seal(job_id, fence, CONTROLS)
        controls[next(iter(controls))] = (1, DIGEST)
    elif failure == "cancelled":
        store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    elif failure == "scratch":
        store.reserve_scratch(job_id, fence, 1)
    elif failure == "effect":
        store.begin_effect(job_id, fence, ATTEMPT, PATH, 1, DIGEST)
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.seal(job_id, fence, controls)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    "failure", ["missing", "acknowledged", "length", "digest", "pending", "conflict", "observation"]
)
def test_effect_reconciliation_preserves_uncertain_or_conflicting_intent(tmp_path: Path, failure: str) -> None:
    store, job_id, fence = _running(tmp_path)
    if failure != "missing":
        store.begin_effect(job_id, fence, ATTEMPT, PATH, 1, DIGEST)
        store.finish_effect(job_id, fence, PATH, ok=failure == "acknowledged")
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.reconcile_effect(
            job_id,
            fence,
            PATH,
            observed=failure
            if failure in {"pending", "conflict"}
            else "invalid"
            if failure == "observation"
            else "complete",
            length=2 if failure == "length" else 1,
            digest="b" * 64 if failure == "digest" else DIGEST,
        )

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    "failure", ["empty", "repeated", "copy_id", "attempt", "not_cancelled", "missing", "identity", "copy_conflict"]
)
def test_blob_reconciliation_requires_journaled_batch_identity(tmp_path: Path, failure: str) -> None:
    store, job_id, fence = _running(tmp_path, blob=True)
    store.begin_effect(job_id, fence, ATTEMPT, PATH, 1, DIGEST)
    store.finish_effect(job_id, fence, PATH, ok=False)
    if failure == "copy_conflict":
        store.begin_reconciliation(job_id, fence, ATTEMPT, PATH, 1, DIGEST, copy_id="copy-1", cancel=False)
    items = [(PATH, 1, DIGEST, None)]
    if failure == "empty":
        items = []
    elif failure == "repeated":
        items *= 2
    elif failure == "copy_id":
        items = [(PATH, 1, DIGEST, "")]
    elif failure == "missing":
        items = [("data/other.parquet", 1, DIGEST, None)]
    elif failure == "identity":
        items = [(PATH, 2, DIGEST, None)]
    elif failure == "copy_conflict":
        items = [(PATH, 1, DIGEST, "copy-2")]
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.begin_reconciliations(
            job_id,
            fence,
            OTHER_ATTEMPT if failure == "attempt" else ATTEMPT,
            items,
            cancel=failure == "not_cancelled",
        )

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("operation", ["blob_cleanup_status", "cancellation_effects", "begin_blob_owner_cleanup"])
def test_blob_cleanup_requires_an_accepted_cancellation(tmp_path: Path, operation: str) -> None:
    store, job_id, fence = _running(tmp_path, blob=True)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        if operation == "blob_cleanup_status":
            store.blob_cleanup_status(job_id, fence, failed=True)
        elif operation == "begin_blob_owner_cleanup":
            store.begin_blob_owner_cleanup(job_id, fence, ATTEMPT)
        else:
            store.cancellation_effects(job_id, fence)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("operation", ["cancellation_effects", "begin_reconciliations", "finish_blob_removals"])
def test_active_blob_writer_blocks_cancellation_reconciliation(tmp_path: Path, operation: str) -> None:
    store, job_id, fence = _running(tmp_path, blob=True)
    store.begin_effect(job_id, fence, ATTEMPT, PATH, 1, DIGEST)
    _register(store, job_id, fence, effect_path=PATH)
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        if operation == "begin_reconciliations":
            store.begin_reconciliations(job_id, fence, ATTEMPT, [(PATH, 1, DIGEST, None)], cancel=True)
        elif operation == "finish_blob_removals":
            store.finish_blob_removals(job_id, fence, [PATH])
        else:
            store.cancellation_effects(job_id, fence)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("failure", ["attempt", "manifest", "state", "digest", "capacity"])
def test_marker_intent_cannot_escape_durable_commit_decision(tmp_path: Path, failure: str) -> None:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "output")
    if failure != "state":
        with pytest.raises(RuntimeError), store.publication(job_id, fence, ATTEMPT, COMMIT_DIGEST):
            raise RuntimeError("simulate controller interruption")
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.begin_marker_effect(
            job_id,
            fence,
            OTHER_ATTEMPT if failure == "attempt" else ATTEMPT,
            "b" * 64 if failure == "manifest" else COMMIT_DIGEST,
            1024 if failure == "capacity" else 1,
            "invalid" if failure == "digest" else DIGEST,
        )

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("failure", ["destination", "attempt", "length", "digest", "unregistered"])
def test_blob_child_rechecks_exact_dispatched_identity(tmp_path: Path, batch: bool, failure: str) -> None:
    store, job_id, fence = _running(tmp_path, blob=True)
    store.begin_effect(job_id, fence, ATTEMPT, PATH, 1, DIGEST)
    if failure != "unregistered":
        _register(store, job_id, fence, effect_path=PATH)
    before = store.storage.snapshot()
    attempt = OTHER_ATTEMPT if failure == "attempt" else ATTEMPT
    destination = "another-destination" if failure == "destination" else BLOB.as_text()
    length = 2 if failure == "length" else 1
    digest = "b" * 64 if failure == "digest" else DIGEST

    with pytest.raises(AuthorityError):
        if batch:
            store.authorize_blob_writers(job_id, fence, attempt, [(PATH, length, digest)], destination=destination)
        else:
            store.authorize_blob_writer(job_id, fence, attempt, PATH, length, digest, destination=destination)

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("failure", ["empty", "large", "repeated", "missing_ids", "wrong_id_count"])
def test_blob_child_batch_refuses_invalid_group_before_writes(tmp_path: Path, failure: str) -> None:
    store, job_id, fence = _running(tmp_path, blob=True)
    items = [(PATH, 1, DIGEST)]
    if failure == "empty":
        items = []
    elif failure == "large":
        items *= store.settings.blob_batch_size + 1
    elif failure == "repeated":
        items *= 2
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.authorize_blob_writers(
            job_id, fence, ATTEMPT, items, destination=BLOB.as_text(),
            reconciliation=failure in {"missing_ids", "wrong_id_count"},
            copy_ids=[] if failure == "wrong_id_count" else None,
        )

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("batch", [False, True])
def test_cleanup_writer_cannot_substitute_another_copy_id(tmp_path: Path, batch: bool) -> None:
    store, job_id, fence = _running(tmp_path, blob=True)
    store.begin_effect(job_id, fence, ATTEMPT, PATH, 1, DIGEST)
    store.request_cancel(job_id, scope=SCOPE, principal=OWNER)
    store.begin_reconciliation(job_id, fence, ATTEMPT, PATH, 1, DIGEST, copy_id="copy-1", cancel=True)
    _register(store, job_id, fence, effect_path=PATH, reconciliation=True)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError, match="copy identity"):
        if batch:
            store.authorize_blob_writers(
                job_id, fence, ATTEMPT, [(PATH, 1, DIGEST)], destination=BLOB.as_text(),
                reconciliation=True, copy_ids=["copy-2"],
            )
        else:
            store.authorize_blob_writer(
                job_id, fence, ATTEMPT, PATH, 1, DIGEST, destination=BLOB.as_text(),
                reconciliation=True, copy_id="copy-2",
            )

    assert store.storage.snapshot() == before


def _committing(tmp_path: Path) -> tuple[ReleaseJobStore, str, int]:
    store = _store(tmp_path / "ledger", FakeClock())
    job_id, fence = _sealed_job(store, tmp_path / "output")
    with pytest.raises(RuntimeError), store.publication(job_id, fence, ATTEMPT, COMMIT_DIGEST):
        raise RuntimeError("simulate controller interruption")
    return store, job_id, fence


@pytest.mark.parametrize("failure", ["attempt", "manifest", "length", "digest"])
def test_marker_child_cannot_replace_chosen_commit_or_marker(tmp_path: Path, failure: str) -> None:
    store, job_id, fence = _committing(tmp_path)
    store.begin_marker_effect(job_id, fence, ATTEMPT, COMMIT_DIGEST, 1, DIGEST)
    _register(store, job_id, fence, effect_path=MARKER_PATH)
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        store.authorize_marker_writer(
            job_id, fence, OTHER_ATTEMPT if failure == "attempt" else ATTEMPT,
            "b" * 64 if failure == "manifest" else COMMIT_DIGEST,
            2 if failure == "length" else 1, "b" * 64 if failure == "digest" else DIGEST,
        )

    assert store.storage.snapshot() == before


@pytest.mark.parametrize(
    "failure",
    ["observation", "not_committing", "missing", "acknowledged", "identity", "temporary", "runtime", "pending", "conflict"],
)
def test_marker_recovery_retains_unresolved_or_conflicting_commit(tmp_path: Path, failure: str) -> None:
    if failure == "not_committing":
        store, job_id, fence = _running(tmp_path)
    else:
        store, job_id, fence = _committing(tmp_path)
    if failure not in {"missing", "not_committing"}:
        store.begin_marker_effect(job_id, fence, ATTEMPT, COMMIT_DIGEST, 1, DIGEST)
    if failure == "acknowledged":
        store.finish_effect(job_id, fence, MARKER_PATH, ok=True)
    elif failure in {"runtime", "temporary"}:
        _register(store, job_id, fence, effect_path=MARKER_PATH)
        if failure == "temporary":
            temporary = tmp_path / (".curation-copy-" + "a" * 32 + ".partial")
            temporary.touch()
            store.register_local_temporary(job_id, fence, MARKER_PATH, temporary.name, temporary.stat())
    before = store.storage.snapshot()

    with pytest.raises(ReleaseJobError):
        store.resolve_marker_effect(
            job_id, fence, observed=failure if failure in {"pending", "conflict"} else
            "untrusted" if failure == "observation" else "complete",
            length=2 if failure == "identity" else 1, digest=DIGEST,
        )

    assert store.storage.snapshot() == before


@pytest.mark.parametrize("observed", ["absent", "complete"])
def test_marker_recovery_closes_only_the_verified_intent(tmp_path: Path, observed: str) -> None:
    store, job_id, fence = _committing(tmp_path)
    store.begin_marker_effect(job_id, fence, ATTEMPT, COMMIT_DIGEST, 1, DIGEST)

    result = store.resolve_marker_effect(job_id, fence, observed=observed, length=1, digest=DIGEST)

    assert result == ("abandoned" if observed == "absent" else "acknowledged")
    record = store.storage.snapshot()[job_id]
    assert record["state"] == "committing"
    assert record["commit_digest"] == COMMIT_DIGEST
    assert (MARKER_PATH in record["effects"]) is (observed == "complete")


@pytest.mark.parametrize("failure", ["state", "root", "runtime", "unknown_temporary"])
def test_marker_partial_cleanup_refuses_unbound_residue(tmp_path: Path, failure: str) -> None:
    store, job_id, fence = _running(tmp_path) if failure == "state" else _committing(tmp_path)
    root = tmp_path / "output" / ATTEMPT.prefix
    if failure == "root":
        root = tmp_path / "unowned"
    elif failure == "runtime":
        store.begin_marker_effect(job_id, fence, ATTEMPT, COMMIT_DIGEST, 1, DIGEST)
        _register(store, job_id, fence, effect_path=MARKER_PATH)
    elif failure == "unknown_temporary":
        parent = root / Path(MARKER_PATH).parent
        parent.mkdir(parents=True)
        (parent / (".curation-copy-" + "a" * 32 + ".partial")).touch()
    before = store.storage.snapshot()

    with pytest.raises(AuthorityError):
        store.remove_marker_temporaries(job_id, fence, root)

    assert store.storage.snapshot() == before
