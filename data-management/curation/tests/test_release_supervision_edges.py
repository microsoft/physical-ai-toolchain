"""Guardian framing and process identity checks remain safe across races."""

from __future__ import annotations

import json
import os
import signal
import socket
import struct
from unittest.mock import Mock

import pytest
from physical_ai_curation.release import supervision
from physical_ai_curation.release.errors import ReleaseVerificationError
from physical_ai_curation.release.supervision import ProcessIdentity


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
