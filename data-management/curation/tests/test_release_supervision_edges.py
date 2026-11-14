"""Guardian framing and process identity checks remain safe across races."""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from physical_ai_curation.release import supervision
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.supervision import ProcessIdentity, ProcessSupervisor, SupervisedRuntime


def test_guardian_channel_buffers_fragmented_frames_and_preserves_message_order() -> None:
    sender, receiver = socket.socketpair()
    with sender, receiver:
        channel = supervision._Channel(receiver)
        first = json.dumps({"op": "heartbeat", "sequence": 1}).encode()
        second = json.dumps({"op": "stop"}).encode()
        sender.sendall(struct.pack(">I", len(first)) + first[:2])
        channel.fill(0)
        assert not channel.pending()

        sender.sendall(first[2:] + struct.pack(">I", len(second)) + second)
        channel.fill(0)

        assert channel.pop() == {"op": "heartbeat", "sequence": 1}
        assert channel.next_message(0) == {"op": "stop"}
        assert not channel.pending()


def test_guardian_channel_read_failure_closes_control_without_leaking_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sock = Mock()
    sock.recv.side_effect = OSError("private socket detail")
    channel = supervision._Channel(sock)
    monkeypatch.setattr(supervision.select, "select", lambda *_args: ([sock], [], []))

    channel.fill(0)

    assert channel.closed
    assert channel.next_message(1) is None


def test_guardian_channel_rejects_oversized_outbound_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = Mock()
    channel = supervision._Channel(sock)
    monkeypatch.setattr(supervision, "MAX_FRAME_BYTES", 8)

    with pytest.raises(ReleaseVerificationError):
        channel.send({"op": "heartbeat", "private": "payload"})

    sock.sendall.assert_not_called()


@pytest.mark.parametrize("race", ["reused_pid", "missing_stat", "exited_during_signal", "same_identity"])
def test_descendant_signalling_checks_start_time_and_closes_pidfd(monkeypatch: pytest.MonkeyPatch, race: str) -> None:
    descriptor = os.memfd_create("test-descendant-pidfd", os.MFD_CLOEXEC)
    monkeypatch.setattr(supervision.os, "pidfd_open", lambda _pid: descriptor)
    current = Mock(start=2 if race == "reused_pid" else 1)
    stat = Mock(return_value=current)
    if race == "missing_stat":
        stat.side_effect = FileNotFoundError
    monkeypatch.setattr(supervision, "_proc_stat", stat)
    send = Mock(side_effect=ProcessLookupError if race == "exited_during_signal" else None)
    monkeypatch.setattr(supervision.signal, "pidfd_send_signal", send)

    supervision._signal_verified(123, 1, signal.SIGTERM)

    if race in {"same_identity", "exited_during_signal"}:
        send.assert_called_once_with(descriptor, signal.SIGTERM)
    else:
        send.assert_not_called()
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_descendant_exiting_before_pidfd_open_requires_no_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervision.os, "pidfd_open", Mock(side_effect=ProcessLookupError))
    send = Mock()
    monkeypatch.setattr(supervision.signal, "pidfd_send_signal", send)

    supervision._signal_verified(123, 1, signal.SIGTERM)

    send.assert_not_called()


@pytest.mark.parametrize("race", ["reused_pid", "missing_stat", "alive", "exited"])
def test_stop_observation_uses_original_process_identity_and_closes_pidfd(
    monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    descriptor = os.memfd_create("test-process-pidfd", os.MFD_CLOEXEC)
    identity = ProcessIdentity(pid=123, start=1, boot_id="test-boot")
    monkeypatch.setattr(supervision, "_boot_id", lambda: "test-boot")
    monkeypatch.setattr(supervision.os, "pidfd_open", lambda _pid: descriptor)
    stat = Mock(return_value=Mock(start=2 if race == "reused_pid" else 1))
    if race == "missing_stat":
        stat.side_effect = FileNotFoundError
    monkeypatch.setattr(supervision, "_proc_stat", stat)
    monkeypatch.setattr(supervision.select, "select", lambda *_args: ([descriptor] if race == "exited" else [], [], []))

    assert supervision._identity_exited(identity, 0) is (race != "alive")
    with pytest.raises(OSError):
        os.fstat(descriptor)


@pytest.mark.parametrize("missing", ["pid", "boot"])
def test_previous_boot_or_missing_pid_is_already_stopped(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    identity = ProcessIdentity(pid=123, start=1, boot_id="test-boot")
    monkeypatch.setattr(supervision, "_boot_id", lambda: "new-boot" if missing == "boot" else "test-boot")
    opened = Mock(side_effect=ProcessLookupError)
    monkeypatch.setattr(supervision.os, "pidfd_open", opened)

    assert supervision._identity_exited(identity, 0)
    if missing == "boot":
        opened.assert_not_called()


@pytest.mark.parametrize("capability", ["platform", "pidfd_open", "pidfd_send_signal"])
def test_supervision_refuses_unsupported_kernel_capabilities(monkeypatch: pytest.MonkeyPatch, capability: str) -> None:
    if capability == "platform":
        monkeypatch.setattr(supervision.sys, "platform", "darwin")
    else:
        monkeypatch.delattr(supervision.os if capability == "pidfd_open" else supervision.signal, capability)

    with pytest.raises(ReleaseVerificationError, match="require Linux pidfds"):
        supervision._require_linux()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("argv", "not a list"),
        ("argv", [1]),
        ("inherit_fds", "not a list"),
        ("inherit_fds", [True]),
        ("memory", True),
        ("file", "unbounded"),
        ("cwd", 12),
        ("proof_path", 12),
        ("output_fd", -1),
        ("grace", -1),
        ("liveness", 0),
    ],
)
def test_given_invalid_guardian_configuration_when_decoded_then_no_process_is_admitted(
    field: str, value: object
) -> None:
    config = {
        "argv": ["true"],
        "inherit_fds": [],
        "memory": 512 * 1024**2,
        "file": 1024,
        "cwd": None,
        "proof_path": None,
        "output_fd": 1,
        "grace": 0.0,
        "liveness": 1.0,
        field: value,
    }

    with pytest.raises(ReleaseVerificationError):
        supervision._GuardianConfig.from_message(config)


@pytest.mark.parametrize("damage", ["missing", "directory", "symlink", "oversized", "invalid_json", "missing_path"])
def test_given_untrusted_stop_proof_when_read_then_only_missing_file_is_absent(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "proof.json"
    if damage == "directory":
        path.mkdir()
    elif damage == "symlink":
        target = tmp_path / "target.json"
        target.write_bytes(b"{}")
        path.symlink_to(target)
    elif damage in {"oversized", "invalid_json"}:
        path.write_bytes(b"x" * (supervision.MAX_PROOF_BYTES + 1) if damage == "oversized" else b"invalid JSON")
    identity = ProcessIdentity(pid=123, start=1, boot_id="test-boot")
    runtime = SupervisedRuntime(identity, identity, None if damage == "missing_path" else str(path))

    if damage == "missing":
        assert supervision._read_proof(runtime) is None
    else:
        with pytest.raises(ReleaseVerificationError, match="unresolved"):
            supervision._read_proof(runtime)


def test_given_stop_proof_metadata_read_failure_when_checked_then_descriptor_is_closed(
    tmp_path: Path, mocker: Any
) -> None:
    path = tmp_path / "proof.json"
    path.write_bytes(b"{}")
    identity = ProcessIdentity(pid=123, start=1, boot_id="test-boot")
    runtime = SupervisedRuntime(identity, identity, str(path))
    opened = mocker.spy(os, "open")
    metadata = mocker.patch.object(os, "fstat", side_effect=OSError("metadata unavailable"))

    with pytest.raises(ReleaseVerificationError, match="unresolved"):
        supervision._read_proof(runtime)
    mocker.stop(metadata)

    with pytest.raises(OSError):
        os.fstat(opened.spy_return)


@pytest.mark.parametrize("reply", ["failed", "malformed"])
def test_given_failed_guardian_handshake_when_received_then_control_channel_is_closed(reply: str) -> None:
    sender, receiver = socket.socketpair()
    with sender, receiver:
        channel = supervision._Channel(sender)
        supervisor = ProcessSupervisor(["true"], memory_bytes=512 * 1024**2)
        supervisor._channel = supervision._Channel(receiver)
        if reply == "failed":
            channel.send({"op": "failed"})
        else:
            channel.send({"op": "spawned", "guardian": {}, "leader": {}})
            channel.send({"op": "stopped"})

        with pytest.raises(ReleaseVerificationError, match="supervised start"):
            supervisor._handshake({"op": "start"})

        assert receiver.fileno() == -1
        assert supervisor._runtime is None


def test_given_unstarted_supervisor_when_channel_required_then_refused() -> None:
    supervisor = ProcessSupervisor(["true"], memory_bytes=512 * 1024**2)

    with pytest.raises(ReleaseVerificationError, match="not started"):
        supervisor._require_channel()


@pytest.mark.parametrize("message", [{"op": "unknown"}, {"op": "leader_exited", "code": True}])
def test_given_invalid_guardian_message_when_dispatched_then_protocol_is_refused(message: dict[str, object]) -> None:
    supervisor = ProcessSupervisor(["true"], memory_bytes=512 * 1024**2)

    with pytest.raises(ReleaseVerificationError):
        supervisor._dispatch(message)


def test_given_already_exited_process_group_when_signalled_then_absence_is_complete(mocker: Any) -> None:
    kill = mocker.patch.object(os, "killpg", side_effect=ProcessLookupError)

    supervision._signal_group(123, signal.SIGTERM)

    kill.assert_called_once_with(123, signal.SIGTERM)


_SLEEP_CHILD = [sys.executable, "-c", "import time; time.sleep(60)"]


def _python_child(code: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _tree_for(pid: int, *, gate: int | None = None, pidfd: int = -1) -> supervision._Tree:
    return supervision._Tree(
        pid=pid,
        pidfd=pidfd,
        gate=gate,
        stdin=None,
        identity=ProcessIdentity(pid=pid, start=1, boot_id="test-boot"),
    )


def test_given_live_child_when_reaped_then_nothing_is_counted_until_it_exits() -> None:
    child = _python_child("import time; time.sleep(60)")
    try:
        tree = _tree_for(child.pid)

        tree.reap(child.pid)

        assert tree.reaped == 0
        child.kill()
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        tree.reap(child.pid)
        assert tree.reaped == 1
    finally:
        child.kill()
        child.wait()


def test_given_already_collected_or_foreign_pid_when_reaped_then_reap_is_a_no_op() -> None:
    child = _python_child("pass")
    child.wait()
    tree = _tree_for(child.pid)

    tree.reap(child.pid)
    tree.reap(os.getpid())

    assert tree.reaped == 0


def test_given_gate_open_when_released_then_child_reads_go_and_gate_is_closed() -> None:
    read_fd, write_fd = os.pipe()
    tree = _tree_for(os.getpid(), gate=write_fd)
    try:
        os.set_blocking(read_fd, False)
        tree.release_gate()
        tree.release_gate()

        assert os.read(read_fd, 1) == b"1"
        assert tree.gate is None
    finally:
        os.close(read_fd)
        tree.close_gate()


def test_given_gate_reader_gone_when_released_then_gate_is_closed_and_error_surfaces() -> None:
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    tree = _tree_for(os.getpid(), gate=write_fd)

    with pytest.raises(OSError):
        tree.release_gate()

    assert tree.gate is None


@pytest.mark.parametrize("message", [{"op": "go"}, None])
def test_given_non_config_or_silent_first_frame_when_awaited_then_guardian_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, message: dict[str, Any] | None
) -> None:
    monkeypatch.setattr(supervision, "START_TIMEOUT_SECONDS", 0.05)
    sender, receiver = socket.socketpair()
    with sender, receiver:
        if message is not None:
            supervision._Channel(sender).send(message)

        with pytest.raises(ReleaseVerificationError):
            supervision._await_config(supervision._Channel(receiver))


def test_given_prctl_refusal_when_subreaper_requested_then_guardian_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    libc = Mock()
    libc.prctl.return_value = -1
    monkeypatch.setattr(supervision, "ctypes", SimpleNamespace(CDLL=lambda *args, **kwargs: libc))

    with pytest.raises(ReleaseVerificationError, match="child subreaper"):
        supervision._set_subreaper()


@pytest.mark.parametrize(
    ("code", "expected"),
    [("import sys; sys.exit(3)", 3), ("import os,signal; os.kill(os.getpid(), signal.SIGTERM)", -signal.SIGTERM)],
)
def test_given_exited_leader_when_reported_then_exit_status_is_forwarded(code: str, expected: int) -> None:
    child = _python_child(code)
    sender, receiver = socket.socketpair()
    try:
        with sender, receiver:
            tree = _tree_for(child.pid)
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)

            supervision._report_exit(supervision._Channel(sender), tree)

            assert supervision._Channel(receiver).next_message(1.0) == {"op": "leader_exited", "code": expected}
    finally:
        child.kill()
        child.wait(timeout=5)


def test_given_running_leader_when_reported_then_no_exit_is_sent() -> None:
    child = _python_child("import time; time.sleep(60)")
    sender, receiver = socket.socketpair()
    try:
        with sender, receiver:
            supervision._report_exit(supervision._Channel(sender), _tree_for(child.pid))

            assert supervision._Channel(receiver).next_message(0.05) is None
    finally:
        child.kill()
        child.wait()


def _scripted_supervisor(frames: list[dict[str, Any]], **kwargs: Any) -> tuple[ProcessSupervisor, socket.socket]:
    """Return an unstarted supervisor whose channel peer has already written the given frames."""
    supervisor = ProcessSupervisor(["true"], memory_bytes=512 * 1024**2, **kwargs)
    parent, peer = socket.socketpair()
    peer_channel = supervision._Channel(peer)
    for frame in frames:
        peer_channel.send(frame)
    supervisor._channel = supervision._Channel(parent)
    supervisor._stop_budget = lambda: 0.05  # type: ignore[method-assign]
    return supervisor, peer


def test_given_silent_guardian_when_expected_then_wait_returns_none() -> None:
    supervisor, peer = _scripted_supervisor([])
    try:
        assert supervisor._expect({"stopped"}, time.monotonic()) is None
    finally:
        peer.close()
        supervisor._channel.close()  # type: ignore[union-attr]


def test_given_exit_report_before_stop_when_expected_then_exit_is_recorded_and_stop_returned() -> None:
    supervisor, peer = _scripted_supervisor(
        [{"op": "leader_exited", "code": 0}, {"op": "stopped", "proof": None}],
    )
    try:
        message = supervisor._expect({"stopped"}, time.monotonic() + 1.0)

        assert message == {"op": "stopped", "proof": None}
        assert supervisor._exit_code == 0
    finally:
        peer.close()
        supervisor._channel.close()  # type: ignore[union-attr]


def test_given_second_exit_report_when_dispatched_then_first_code_is_kept() -> None:
    supervisor = ProcessSupervisor(["true"], memory_bytes=512 * 1024**2)

    supervisor._dispatch({"op": "leader_exited", "code": 4})
    supervisor._dispatch({"op": "leader_exited", "code": 9})

    assert supervisor._exit_code == 4


@pytest.mark.parametrize(
    "operation",
    [
        lambda supervisor: supervisor.ping(),
        lambda supervisor: supervisor.wait_for_exit(0.0),
        lambda supervisor: supervisor.stop(),
    ],
)
def test_given_unstarted_supervisor_when_controlled_then_operation_is_refused(operation: Any) -> None:
    supervisor = ProcessSupervisor(["true"], memory_bytes=512 * 1024**2)

    with pytest.raises(ReleaseVerificationError, match="not started"):
        operation(supervisor)


def test_given_started_supervisor_when_started_again_then_second_launch_is_refused() -> None:
    supervisor = ProcessSupervisor(_SLEEP_CHILD, memory_bytes=512 * 1024**2)
    supervisor.start()
    try:
        with pytest.raises(ReleaseVerificationError, match="already started"):
            supervisor.start()
    finally:
        supervisor.stop()


def test_given_unresolved_reply_when_stopping_then_proof_is_refused_and_channel_is_released() -> None:
    supervisor = ProcessSupervisor(_SLEEP_CHILD, memory_bytes=512 * 1024**2)
    supervisor.start()
    supervisor._reply = {"op": "unresolved"}

    with pytest.raises(ReleaseVerificationError, match="unresolved"):
        supervisor.stop()

    assert supervisor._channel is not None
    assert supervisor._channel.closed


def test_given_guardian_lost_when_waiting_for_exit_then_control_loss_is_reported() -> None:
    supervisor = ProcessSupervisor(_SLEEP_CHILD, memory_bytes=512 * 1024**2)
    supervisor.start()
    try:
        supervisor._reply = {"op": "stopped", "proof": None}

        with pytest.raises(ReleaseVerificationError, match="control channel"):
            supervisor.wait_for_exit(0.0)
    finally:
        supervisor._teardown(supervisor._runtime)


def test_given_silent_guardian_when_handshaking_then_start_is_aborted_with_unresolved_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervision, "START_TIMEOUT_SECONDS", 0.05)
    supervisor, peer = _scripted_supervisor([])

    with pytest.raises(ReleaseVerificationError, match="supervised start failed; stop proof is unresolved"):
        supervisor._handshake({"op": "config"})

    assert supervisor._channel is not None
    assert supervisor._channel.closed
    peer.close()


def test_given_guardian_refusal_when_handshaking_then_start_fails_without_proof_claim() -> None:
    supervisor, peer = _scripted_supervisor([{"op": "failed"}])
    try:
        with pytest.raises(ReleaseVerificationError, match=r"^supervised start failed$"):
            supervisor._handshake({"op": "config"})
    finally:
        peer.close()


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ([{"op": "stopped", "proof": None}], "supervised start protocol failed"),
        ([{"op": "unresolved"}], "supervised start protocol failed; stop proof is unresolved"),
        ([], "supervised start protocol failed; stop proof is unresolved"),
    ],
)
def test_given_malformed_spawn_report_when_handshaking_then_abort_reply_decides_proof_claim(
    reply: list[dict[str, Any]], expected: str
) -> None:
    supervisor, peer = _scripted_supervisor([{"op": "spawned", "guardian": None, "leader": None}, *reply])
    supervisor._stop_budget = lambda: 0.05  # type: ignore[method-assign]
    try:
        with pytest.raises(ReleaseVerificationError, match="^" + re.escape(expected)):
            supervisor._handshake({"op": "config"})
    finally:
        peer.close()


def test_given_registration_callback_failure_when_handshaking_then_guardian_is_stopped() -> None:
    def refuse(_: SupervisedRuntime) -> None:
        raise RuntimeError("registry unavailable")

    identity = ProcessIdentity.of(os.getpid()).to_dict()
    supervisor, peer = _scripted_supervisor(
        [{"op": "spawned", "guardian": identity, "leader": identity}, {"op": "stopped", "proof": None}],
        on_started=refuse,
    )
    try:
        with pytest.raises(ReleaseVerificationError, match=r"^registration failed$"):
            supervisor._handshake({"op": "config"})
    finally:
        peer.close()
