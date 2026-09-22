from __future__ import annotations

import socket

from remoter.msgtcp import _MAX_FRAME_BYTES, MessengerTCP


def _connected_pair() -> tuple[socket.socket, socket.socket]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(listener.getsockname())
    server, _ = listener.accept()
    listener.close()
    return client, server


def test_handlerecvbytes_rejects_oversized_declared_length():
    client_sock, server_sock = _connected_pair()
    try:
        msgr = MessengerTCP(server_sock, "tcp://peer:1")
        msgr.curmsg = bytearray((_MAX_FRAME_BYTES + 1).to_bytes(4, "big"))

        success, complete, msg = msgr._handlerecvbytes()

        assert success is False
        assert complete is False
        assert msg is None
    finally:
        client_sock.close()
        server_sock.close()


def test_handlerecvbytes_accepts_length_at_the_limit():
    client_sock, server_sock = _connected_pair()
    try:
        msgr = MessengerTCP(server_sock, "tcp://peer:1")
        msgr.curmsg = bytearray(_MAX_FRAME_BYTES.to_bytes(4, "big"))

        success, complete, msg = msgr._handlerecvbytes()

        # header accepted; now waiting for the (not-yet-arrived) message body
        assert success is True
        assert complete is False
        assert msg is None
        assert msgr.msglen == _MAX_FRAME_BYTES
    finally:
        client_sock.close()
        server_sock.close()
