from __future__ import annotations

import atexit
import os
import socket
import socketserver
import threading
from collections.abc import Callable

from . import msgtcp
from .k8sutils_compat import utils
from .msgsock import Messenger, logger

if not hasattr(socket, "AF_UNIX"):
    logger.warning(
        "AF_UNIX not supported on this platform, MessageServerUnix and MessageClientUnix will not work", color="yellow"
    )

    class UnsupportedUnixSocket:
        def __init__(self, *args, **kwargs):
            raise NotImplementedError("AF_UNIX not supported on this platform")

    socketserver.ThreadingUnixStreamServer = UnsupportedUnixSocket
    socket.AF_UNIX = None


class MessengerUnix(msgtcp.MessengerTCP):
    cnt = 0  # for generating unique client socket paths
    lock = threading.Lock()  # lock to protect cnt

    def __init__(
        self,
        localsocketpath: str,
        sock: socket.socket | None,
        ep: str,
        initfn: Callable[[Messenger, str], None] | None = None,
        handlefn: Callable[[bytes, Messenger, str], None] | None = None,
        closefn: Callable[[Messenger, str], None] | None = None,
        startrecvthread: bool = False,
    ):
        if sock is None:
            # create socket and connect to server
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            # bind to local file - ep is of form unix://path
            localsockdir = os.path.dirname(localsocketpath)
            with MessengerUnix.lock:
                localsockfile = os.path.join(
                    localsockdir, os.path.basename(localsocketpath).replace(".sock", f".client{MessengerUnix.cnt}.sock")
                )
                MessengerUnix.cnt += 1
            utils.cleansocketpath(localsockfile)
            atexit.register(
                lambda: os.path.exists(localsockfile) and os.remove(localsockfile)
            )  # ensure local socket file is removed on exit
            sock.bind(localsockfile)
            # of form unix://path
            assert ep.startswith("unix://"), "Invalid endpoint for MessengerUnix, must start with unix://"
            path = ep[7:]
            logger.info(f"Client socket {localsockfile} connecting to server at {ep}", color="cyan")
            sock.connect(path)
            startrecvthread = True  # for client side, always start recv thread

        super().__init__(sock, ep, initfn, handlefn, closefn, startrecvthread)


class MessageServerUnix(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True  # allow quick restart of server

    def __init__(
        self,
        server_address: str,
        initfn: Callable[[Messenger, str], None] | None = None,
        handlefn: Callable[[bytes, Messenger, str], None] | None = None,
        closefn: Callable[[Messenger, str], None] | None = None,
    ):
        if socket.AF_UNIX is None:
            raise NotImplementedError("AF_UNIX not supported on this platform")
        socketpath = server_address[7:] if server_address.startswith("unix://") else server_address
        self.socketpath = socketpath  # just keep filename
        utils.cleansocketpath(socketpath)
        atexit.register(
            lambda: os.path.exists(socketpath) and os.remove(socketpath)
        )  # ensure socket file is removed on exit
        super().__init__(socketpath, msgtcp.getMsgReqHandler(initfn, handlefn, closefn))

    def isself(self, ep):
        protocol, path = ep.split("://", 1)
        if protocol != "unix":
            return False
        return path == self.socketpath
