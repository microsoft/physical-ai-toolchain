from __future__ import annotations

import secrets
import socket
import threading

import pytest

from remoter import autoremote, msgsock
from remoter.msgsock import ENCRYPTED_MESSAGE_OVERHEAD, decryptMessage, encryptMessage, noncelen
from remoter.msgtcp import MessengerTCP


@pytest.fixture(autouse=True)
def reset_replay_window(monkeypatch) -> None:
    monkeypatch.setattr(
        msgsock,
        "_replay_window",
        msgsock._ReplayWindow(
            max_age_seconds=msgsock._MAX_MESSAGE_AGE_SECONDS,
            max_entries=msgsock._MAX_REPLAY_NONCES,
        ),
    )


def test_aes_gcm_round_trip() -> None:
    key = secrets.token_bytes(32)
    payload = b"authenticated RPC payload"

    encrypted = encryptMessage(payload, key)

    assert decryptMessage(encrypted, key) == payload
    assert len(encrypted) == len(payload) + ENCRYPTED_MESSAGE_OVERHEAD


def test_aes_gcm_rejects_wrong_key() -> None:
    encrypted = encryptMessage(b"payload", secrets.token_bytes(32))

    assert decryptMessage(encrypted, secrets.token_bytes(32)) is None


def test_aes_gcm_rejects_tampering() -> None:
    key = secrets.token_bytes(32)
    encrypted = bytearray(encryptMessage(b"payload", key))
    encrypted[-1] ^= 1

    assert decryptMessage(bytes(encrypted), key) is None


def test_aes_gcm_uses_unique_nonces() -> None:
    key = secrets.token_bytes(32)
    payload = b"same payload"

    first = encryptMessage(payload, key)
    second = encryptMessage(payload, key)

    nonce_start = 1 + msgsock._TIMESTAMP_BYTES
    assert first[nonce_start : nonce_start + noncelen] != second[nonce_start : nonce_start + noncelen]
    assert first != second
    assert decryptMessage(first, key) == payload
    assert decryptMessage(second, key) == payload


def test_aes_gcm_rejects_replayed_frame() -> None:
    key = secrets.token_bytes(32)
    encrypted = encryptMessage(b"payload", key)

    assert decryptMessage(encrypted, key) == b"payload"
    assert decryptMessage(encrypted, key) is None


def test_aes_gcm_rejects_stale_frame(monkeypatch) -> None:
    key = secrets.token_bytes(32)
    monkeypatch.setattr(msgsock.time, "time", lambda: 1_000.0)
    encrypted = encryptMessage(b"payload", key)
    monkeypatch.setattr(
        msgsock.time,
        "time",
        lambda: 1_000.0 + msgsock._MAX_MESSAGE_AGE_SECONDS + 0.001,
    )

    assert decryptMessage(encrypted, key) is None


def test_aes_gcm_rejects_frame_too_far_in_the_future(monkeypatch) -> None:
    key = secrets.token_bytes(32)
    monkeypatch.setattr(
        msgsock.time,
        "time",
        lambda: 1_000.0 + msgsock._MAX_FUTURE_SKEW_SECONDS + 0.001,
    )
    encrypted = encryptMessage(b"payload", key)
    monkeypatch.setattr(msgsock.time, "time", lambda: 1_000.0)

    assert decryptMessage(encrypted, key) is None


def test_aes_gcm_authenticates_frame_timestamp() -> None:
    key = secrets.token_bytes(32)
    encrypted = bytearray(encryptMessage(b"payload", key))
    encrypted[1] ^= 1

    assert decryptMessage(bytes(encrypted), key) is None


def test_configure_message_encryption_defaults_to_enabled(tmp_path, monkeypatch) -> None:
    key = secrets.token_bytes(32)
    key_file = tmp_path / "key"
    key_file.write_bytes(key)
    monkeypatch.setenv("REMOTER_KEY_FILE", str(key_file))
    monkeypatch.setattr(msgsock, "msgkey", None)

    autoremote.configure_message_encryption({})

    assert msgsock.msgkey == key


def test_configure_message_encryption_rejects_unauthenticated_default(monkeypatch) -> None:
    monkeypatch.delenv("REMOTER_KEY_FILE", raising=False)
    monkeypatch.setattr(msgsock, "msgkey", None)

    with pytest.raises(RuntimeError, match="REMOTER_KEY_FILE"):
        autoremote.configure_message_encryption({})


def test_configure_message_encryption_rejects_invalid_key_length(tmp_path, monkeypatch) -> None:
    key_file = tmp_path / "key"
    key_file.write_bytes(b"too-short")
    monkeypatch.setenv("REMOTER_KEY_FILE", str(key_file))
    monkeypatch.setattr(msgsock, "msgkey", None)

    with pytest.raises(ValueError, match="exactly 32 bytes"):
        autoremote.configure_message_encryption({"encryption": True})


def test_configure_message_encryption_clears_key_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(msgsock, "msgkey", secrets.token_bytes(32))

    autoremote.configure_message_encryption({"encryption": False})

    assert msgsock.msgkey is None


def test_forged_peer_message_is_rejected_before_handler(monkeypatch) -> None:
    server_key = secrets.token_bytes(32)
    client_sock, server_sock = socket.socketpair()
    handled = []
    closed = threading.Event()
    monkeypatch.setattr(msgsock, "msgkey", server_key)
    messenger = MessengerTCP(
        server_sock,
        "tcp://forged-peer:1",
        handlefn=lambda data, _messenger, _endpoint: handled.append(data),
        closefn=lambda _messenger, _endpoint: closed.set(),
    )
    thread = threading.Thread(target=messenger.recvthread)
    thread.start()

    payload = encryptMessage(b"forged RPC payload", secrets.token_bytes(32))
    frame = messenger.data + payload
    client_sock.sendall(len(frame).to_bytes(4, "big") + frame)

    assert closed.wait(timeout=2)
    thread.join(timeout=2)
    assert handled == []
    assert messenger.closecalled is True
    client_sock.close()


def test_replayed_authenticated_message_is_rejected_before_handler(monkeypatch) -> None:
    key = secrets.token_bytes(32)
    client_sock, server_sock = socket.socketpair()
    handled = []
    closed = threading.Event()
    monkeypatch.setattr(msgsock, "msgkey", key)
    messenger = MessengerTCP(
        server_sock,
        "tcp://authenticated-peer:1",
        handlefn=lambda data, _messenger, _endpoint: handled.append(data),
        closefn=lambda _messenger, _endpoint: closed.set(),
    )
    thread = threading.Thread(target=messenger.recvthread)
    thread.start()

    payload = encryptMessage(b"authenticated RPC payload", key)
    frame = messenger.data + payload
    wire_frame = len(frame).to_bytes(4, "big") + frame
    client_sock.sendall(wire_frame + wire_frame)

    assert closed.wait(timeout=2)
    thread.join(timeout=2)
    assert handled == [b"authenticated RPC payload"]
    assert messenger.closecalled is True
    client_sock.close()

def test_aes_gcm_evicts_oldest_when_replay_cache_full(monkeypatch) -> None:
    """Sustained traffic must not hard-fail once the bounded cache fills (#1938)."""
    key = secrets.token_bytes(32)
    monkeypatch.setattr(
        msgsock,
        "_replay_window",
        msgsock._ReplayWindow(max_age_seconds=msgsock._MAX_MESSAGE_AGE_SECONDS, max_entries=2),
    )
    # Freeze monotonic time so cleanup does not expire entries mid-test.
    monkeypatch.setattr(msgsock.time, "monotonic", lambda: 1_000.0)

    first = encryptMessage(b"one", key)
    second = encryptMessage(b"two", key)
    third = encryptMessage(b"three", key)

    assert decryptMessage(first, key) == b"one"
    assert decryptMessage(second, key) == b"two"
    # Cache full: registering a third nonce evicts the oldest (first), then accepts.
    assert decryptMessage(third, key) == b"three"
    # Second is still in the window; first was evicted and can be accepted again.
    assert decryptMessage(second, key) is None
    assert decryptMessage(first, key) == b"one"
    assert decryptMessage(third, key) is None


def test_aes_gcm_skew_rejection_mentions_clock_sync(monkeypatch) -> None:
    key = secrets.token_bytes(32)
    messages: list[str] = []

    def capture_error(msg: str, *args, **kwargs) -> None:
        messages.append(msg % args if args else msg)

    monkeypatch.setattr(msgsock.logger, "error", capture_error)
    monkeypatch.setattr(
        msgsock.time,
        "time",
        lambda: 1_000.0 + msgsock._MAX_FUTURE_SKEW_SECONDS + 0.001,
    )
    encrypted = encryptMessage(b"payload", key)
    monkeypatch.setattr(msgsock.time, "time", lambda: 1_000.0)

    assert decryptMessage(encrypted, key) is None
    assert any("Sync peer clocks" in message for message in messages)

