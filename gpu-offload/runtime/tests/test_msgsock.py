from __future__ import annotations

import secrets

import pytest

from remoter import autoremote, msgsock
from remoter.msgsock import decryptMessage, encryptMessage, noncelen

_AES_GCM_TAG_BYTES = 16


def test_aes_gcm_round_trip() -> None:
    key = secrets.token_bytes(32)
    payload = b"authenticated RPC payload"

    encrypted = encryptMessage(payload, key)

    assert decryptMessage(encrypted, key) == payload
    assert len(encrypted) == len(payload) + noncelen + _AES_GCM_TAG_BYTES


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

    assert first[:noncelen] != second[:noncelen]
    assert first != second
    assert decryptMessage(first, key) == payload
    assert decryptMessage(second, key) == payload


def test_configure_message_encryption_loads_key_file(tmp_path, monkeypatch) -> None:
    key = secrets.token_bytes(32)
    key_file = tmp_path / "key"
    key_file.write_bytes(key)
    monkeypatch.setenv("REMOTER_KEY_FILE", str(key_file))
    monkeypatch.setattr(msgsock, "msgkey", None)

    autoremote.configure_message_encryption({"encryption": True})

    assert msgsock.msgkey == key


def test_configure_message_encryption_requires_key_file(monkeypatch) -> None:
    monkeypatch.delenv("REMOTER_KEY_FILE", raising=False)
    monkeypatch.setattr(msgsock, "msgkey", None)

    with pytest.raises(RuntimeError, match="REMOTER_KEY_FILE"):
        autoremote.configure_message_encryption({"encryption": True})


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
