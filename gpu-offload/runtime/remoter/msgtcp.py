from __future__ import annotations

import socket
import socketserver
import struct
import threading
from collections.abc import Callable

from . import msgsock
from .msgsock import Messenger, logger
from .safe_codec import CodecLimits

_RECEIVE_CHUNK_BYTES = 256 * 1024
# reject an oversized declared frame length before buffering it, rather than
# accumulating unbounded data in curmsg only for safe_codec to reject it later
_MAX_FRAME_BYTES = CodecLimits().max_encoded_bytes


class MessengerTCP(Messenger):
    GetLen = 0
    GetData = 1

    def __init__(
        self,
        sock: socket.socket | None,
        ep: str,
        initfn: Callable[[Messenger, str], None] | None = None,
        handlefn: Callable[[bytes, Messenger, str], None] | None = None,
        closefn: Callable[[Messenger, str], None] | None = None,
        startrecvthread: bool = False,
    ):
        if sock is None:
            # create socket and connect to server
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # of form tcp://host:port
            assert ep.startswith("tcp://"), "Invalid endpoint for MessengerTCP, must start with tcp://"
            host, port = ep[6:].split(":")
            sock.connect((host, int(port)))
            startrecvthread = True  # for client side, start recv thread
            logger.info(f"Client endpoint {sock.getsockname()} connected to server at {ep}", color="cyan")

        self.sock = sock
        super().__init__(ep, initfn, handlefn, closefn)
        self.state = MessengerTCP.GetLen
        self.curmsg = bytearray()
        if startrecvthread:
            threading.Thread(target=self.recvthread, daemon=True).start()

    def _networkrecv(self):
        try:
            return self.sock.recv(_RECEIVE_CHUNK_BYTES)
        except OSError as e:
            logger.debug(f"Socket error on recv {e}")
            return b""  # indicate connection closed or error by returning empty bytes

    def _ingestrecvdata(self, data: bytes):
        self.curmsg.extend(data)

    def _handlerecvbytes(self) -> tuple[bool, bool, bytes | None]:
        while True:
            if self.state == MessengerTCP.GetLen:
                if len(self.curmsg) < 4:
                    return True, False, None  # not enough data to get length yet
                self.msglen = int.from_bytes(self.curmsg[:4], "big")
                if self.msglen > _MAX_FRAME_BYTES:
                    logger.warning(
                        f"Received oversized frame length {self.msglen} from {self.ep} "
                        f"(max {_MAX_FRAME_BYTES}) -- closing connection"
                    )
                    return False, False, None  # reject before buffering to avoid unbounded memory growth
                logger.debug(f"Received message length from {self.ep}: {self.msglen}")
                del self.curmsg[:4]
                self.state = MessengerTCP.GetData
            elif self.state == MessengerTCP.GetData:
                if len(self.curmsg) < self.msglen:
                    return True, False, None  # not enough data to get full message yet
                msg = bytes(self.curmsg[: self.msglen])
                del self.curmsg[: self.msglen]
                self.state = MessengerTCP.GetLen
                return True, True, msg
            else:
                logger.warning(f"Invalid state {self.state} in MessengerTCP for {self.ep} -- closing connection")
                return False, False, None  # invalid state, close connection

    def _close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError as e:
            logger.debug(f"Socket error on shutdown {e}")
        try:
            self.sock.close()
        except OSError as e:
            logger.debug(f"Socket error on close {e}")

    def _sendmessage(self, message: list[bytes]) -> int | None:
        # insert 4 byte length header before message which is last element of message list
        msglen = sum(len(m) for m in message)
        logger.debug(f"Sending message to {self.ep} of length {msglen} plus header length 4 to indicate message length")
        message = [msglen.to_bytes(4, "big")] + message  # noqa: RUF005 vendored from microsoft/xavier, not refactored
        return msgsock.sendallmsg(self.sock, message)


def getMsgReqHandler(
    initfn: Callable[[Messenger, str], None] | None = None,
    handlefn: Callable[[bytes, Messenger, str], None] | None = None,
    closefn: Callable[[Messenger, str], None] | None = None,
):
    class MessageRequestHandler(socketserver.BaseRequestHandler):
        def __init__(self, *args, **kwdargs):
            self.msgr: MessengerTCP | None = None
            self.closefn = closefn
            super().__init__(*args, **kwdargs)

        def setup(self):
            # check if socket is unix or tcp
            sock: socket.socket = self.request
            if sock.family == socket.AF_UNIX:
                if self.client_address == "":
                    # generate a unique ep for this client based on its socket name
                    ucred = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                    pid, uid, gid = struct.unpack("3i", ucred)
                    remoteaddr = f"{pid}:{uid}:{gid}"
                    ep = f"unix://{remoteaddr}"
                else:
                    ep = f"unix://{self.client_address}"  # a single value, make sure client binds to it
            elif sock.family == socket.AF_INET:
                # get ep from client address
                ep = f"tcp://{self.client_address[0]}:{self.client_address[1]}"
            else:
                raise ValueError(f"Unsupported socket family for client connection: {sock.family}")
            # initfn handled in setup and closefn handled in finish
            self.msgr = MessengerTCP(self.request, ep, None, handlefn, None)
            if initfn is not None:
                initfn(self.msgr, ep)

        def handle(self):
            assert self.msgr is not None
            self.msgr.recvthread()  # handle gets called on separate thread for each client, so can block on recvthread

        def finish(self):
            if self.closefn is not None and self.msgr is not None:
                self.closefn(self.msgr, self.msgr.ep)
            if self.msgr is not None:
                self.msgr.close()

    return MessageRequestHandler


class MessageServerTCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True  # allow quick restart of server

    def __init__(
        self,
        host,
        port,
        initfn: Callable[[Messenger, str], None] | None = None,
        handlefn: Callable[[bytes, Messenger, str], None] | None = None,
        closefn: Callable[[Messenger, str], None] | None = None,
    ):
        self.host = host
        self.port = port
        super().__init__((host, port), getMsgReqHandler(initfn, handlefn, closefn))

    def isself(self, ep):
        protocol, addr = ep.split("://", 1)
        if protocol != "tcp":
            return False
        host, port = addr.split(":", 1)
        return msgsock.isselfip(host, int(port), self.port)
