from __future__ import annotations

import socket
from collections.abc import Callable

from .msgsock import Messenger
from .msgtcp import MessageServerTCP, MessengerTCP
from .msgudp import MessageServerUDP, MessengerUDP
from .msgunix import MessageServerUnix, MessengerUnix


def CreateMessageServer(
    ep: str,
    initfn: Callable[[MessengerTCP | MessengerUDP | MessengerUnix, str], None] | None = None,
    handlefn: Callable[[bytes, MessengerTCP | MessengerUDP | MessengerUnix, str], None] | None = None,
    closefn: Callable[[MessengerTCP | MessengerUDP | MessengerUnix, str], None] | None = None,
) -> MessageServerTCP | MessageServerUDP | MessageServerUnix:
    protocol, addr = ep.split("://", 1)
    if protocol == "tcp":
        host, port = addr.split(":", 1)
        if host == "":
            host = "0.0.0.0"  # listen on all interfaces if host is empty
        return MessageServerTCP(host, int(port), initfn, handlefn, closefn)
    elif protocol == "udp":
        host, port = addr.split(":", 1)
        if host == "":
            host = "0.0.0.0"
        return MessageServerUDP(host, int(port), initfn, handlefn, closefn)
    elif protocol == "unix":
        return MessageServerUnix(addr, initfn, handlefn, closefn)


def CreateMessenger(
    ep: str,
    sock: socket.socket | None = None,
    localsockpath: str | None = None,
    isserver: bool = False,
    initfn: Callable[[MessengerTCP | MessengerUDP | MessengerUnix, str], None] | None = None,
    handlefn: Callable[[bytes, MessengerTCP | MessengerUDP | MessengerUnix, str], None] | None = None,
    closefn: Callable[[MessengerTCP | MessengerUDP | MessengerUnix, str], None] | None = None,
    startrecvthread: bool = False,
) -> Messenger:
    protocol, _ = ep.split("://", 1)
    if protocol == "tcp":
        return MessengerTCP(sock, ep, initfn, handlefn, closefn, startrecvthread)
    elif protocol == "udp":
        return MessengerUDP(sock, ep, isserver, initfn, handlefn, closefn)
    elif protocol == "unix":
        return MessengerUnix(localsockpath, sock, ep, initfn, handlefn, closefn, startrecvthread)
