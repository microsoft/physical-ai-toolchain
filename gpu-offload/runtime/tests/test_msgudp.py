from __future__ import annotations

import socket

from remoter.msgudp import _MAX_CHUNKS_PER_MESSAGE, CHUNK_SIZE, HEADER_STRUCT, MessengerUDP


def _server_messenger() -> MessengerUDP:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    return MessengerUDP(sock, "udp://127.0.0.1:1", isserver=True)


def test_handlerecvbytes_rejects_oversized_totalchunks():
    msgr = _server_messenger()
    try:
        header = HEADER_STRUCT.pack(1, 0, _MAX_CHUNKS_PER_MESSAGE + 1)
        msgr.curdata = header + b"x"

        success, complete, msg = msgr._handlerecvbytes()

        assert (success, complete, msg) == (False, False, None)
        assert 1 not in msgr.messages
    finally:
        msgr.sock.close()


def test_handlerecvbytes_rejects_out_of_range_chunkindex():
    msgr = _server_messenger()
    try:
        header = HEADER_STRUCT.pack(1, 5, 3)  # chunkindex 5 >= totalchunks 3
        msgr.curdata = header + b"x"

        success, complete, msg = msgr._handlerecvbytes()

        assert (success, complete, msg) == (False, False, None)
        assert 1 not in msgr.messages
    finally:
        msgr.sock.close()


def test_handlerecvbytes_rejects_mismatched_totalchunks_for_known_message():
    msgr = _server_messenger()
    try:
        first = HEADER_STRUCT.pack(1, 0, 3)
        msgr.curdata = first + b"a"
        assert msgr._handlerecvbytes() == (True, False, None)
        assert len(msgr.messages[1]["data"]) == 3

        second = HEADER_STRUCT.pack(1, 1, 4)  # same messageindex, different totalchunks
        msgr.curdata = second + b"b"

        success, complete, msg = msgr._handlerecvbytes()

        assert (success, complete, msg) == (True, False, None)
        # the original chunk list is untouched, not resized/corrupted
        assert len(msgr.messages[1]["data"]) == 3
    finally:
        msgr.sock.close()


def test_handlerecvbytes_reassembles_a_valid_multi_chunk_message():
    msgr = _server_messenger()
    try:
        payloads = [b"a" * CHUNK_SIZE, b"b" * 10]
        for index, payload in enumerate(payloads):
            msgr.curdata = HEADER_STRUCT.pack(7, index, len(payloads)) + payload
            success, complete, msg = msgr._handlerecvbytes()
            if index < len(payloads) - 1:
                assert (success, complete) == (True, False)
            else:
                assert (success, complete, msg) == (True, True, b"".join(payloads))
    finally:
        msgr.sock.close()
