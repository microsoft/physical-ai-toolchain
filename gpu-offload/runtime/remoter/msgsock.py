from __future__ import annotations

import enum
import logging
import secrets
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .simplelog import initlog

logger = initlog("sockmessage.log", logging.DEBUG, logging.INFO)


class MsgType(enum.Enum):
    Heartbeat = 0
    Data = 1


msgkey: bytes | None = None
noncelen = 12  # length of nonce for AESGCM

if not hasattr(socket, "AF_UNIX"):

    def sendmsg(sock: socket.socket, buffers, ancdata=[], flags=0, address: tuple[str, int] | str = ""):  # noqa: B006 vendored from microsoft/xavier, not refactored
        # just concatenate buffers and send as normal message for win32
        data = b"".join(buffers)
        if sock.type == socket.SOCK_STREAM:
            ret = sock.sendall(data, flags)
            if ret is None:
                return len(data)  # sendall returns None on success, we want to return length of data sent
            else:
                return -1  # indicate failure
        elif sock.type == socket.SOCK_DGRAM:
            return sock.sendto(data, address)
        else:
            raise ValueError("Unsupported socket type for sendmsg")
else:
    sendmsg = (
        socket.socket.sendmsg
    )  # for unix, use the native sendmsg function which supports multiple buffers and ancillary data


def sendallmsg(sock: socket.socket, buffers: list[bytes | memoryview]) -> int:
    views = [memoryview(buffer) for buffer in buffers if len(buffer) > 0]
    senttotal = 0

    while views:
        sent = sendmsg(sock, views)  # may not send all data in one call, so we need to loop until all data is sent
        if sent is None or sent <= 0:
            raise OSError("socket connection broken while sending message")
        senttotal += sent
        while views and sent >= len(views[0]):
            sent -= len(views[0])
            views.pop(0)
        if views and sent:
            views[0] = views[0][sent:]

    return senttotal


def encryptMessage(msg: bytes, msgkey: bytes) -> bytes:
    # Encrypt the message using AESGCM
    nonce = secrets.token_bytes(noncelen)  # GCM mode needs 12 fresh bytes every time
    msg = nonce + AESGCM(msgkey).encrypt(nonce, msg, b"")
    return msg


def decryptMessage(msg: bytes, decryptkey: bytes) -> bytes | None:
    # Decrypt the message using AESGCM
    nonce = msg[:noncelen]
    try:
        return AESGCM(decryptkey).decrypt(nonce, msg[noncelen:], b"")
    except Exception as e:
        logger.error(f"Decryption failed for message: {e}")
        return None  # decryption failed, return None to indicate failure, will close connection on caller side


initheartbeat = False  # whether heartbeat has been started or not
heartbeatlock = threading.Lock()  # lock to make sure only one heartbeat thread is started
messengers: list[Messenger] = []  # list of all active messengers for sending heartbeats
heartbeattime = 10


def heartbeatThread():
    heartbeatpool = ThreadPoolExecutor(max_workers=10)  # thread pool for sending heartbeats in parallel
    while True:
        # send heartbeats to active connections
        with heartbeatlock:
            # take snapshot of active connections to avoid holding lock while sending heartbeats
            msgrs = messengers.copy()
        notaliveconns = []
        curtime = time.time()
        for msgr in msgrs:
            try:
                heartbeatpool.submit(msgr.sendheartbeat)
            except Exception as e:
                logger.error(f"Error submitting heartbeat message: {e}")
                continue
            lastheartbeat = msgr.lastheartbeat
            if curtime - lastheartbeat > 3 * heartbeattime:
                notaliveconns.append(msgr)
        time.sleep(heartbeattime)
        # remove any connections that are not alive
        for msgr in notaliveconns:
            logger.info(f"No heartbeat received from connection {msgr.ep} -- closing connection")
            msgr.close()


class Messenger:
    def __init__(
        self,
        ep: str,
        initfn: Callable[[Messenger, str], None] | None = None,
        handlefn: Callable[[bytes, Messenger, str], None] | None = None,
        closefn: Callable[[Messenger, str], None] | None = None,
    ):
        # heartbeat message
        self.heartbeat = int.to_bytes(MsgType.Heartbeat.value, 1, "big")  # for 1 byte, endian doesn't really matter
        self.data = int.to_bytes(MsgType.Data.value, 1, "big")
        self.ep = ep
        self.lastheartbeat = (
            time.time() + heartbeattime
        )  # initialize last heartbeat time to now to avoid premature timeout
        self.initfn = initfn
        self.handlefn = handlefn
        self.closefn = closefn
        self.closecalled = False
        self.lock = threading.Lock()  # lock to protect closecalled and lastheartbeat
        self.sendlock = threading.Lock()  # stream transports must not interleave framed messages
        with heartbeatlock:
            messengers.append(self)
            global initheartbeat
            if not initheartbeat:
                threading.Thread(target=heartbeatThread, daemon=True).start()
                initheartbeat = True
        logger.info(f"Created messenger for endpoint {ep} of type {self.__class__.__name__}", color="cyan")

    def _close(self):
        raise NotImplementedError("close not implemented - to be implemented by subclass")

    def _sendmessage(self, msg: list[bytes]) -> int | None:
        raise NotImplementedError("sendmessage not implemented - to be implemented by subclass")

    def _networkrecv(self):
        raise NotImplementedError("recvmessage not implemented - to be implemented by subclass")

    def _handlerecvbytes(self) -> tuple[bool, bool, bytes | None]:
        raise NotImplementedError("handlerecvbytes not implemented - to be implemented by subclass")

    def sendheartbeat(self):
        logger.debug(f"Sending heartbeat to {self.ep} of length (including header): {len(self.heartbeat)}")
        with self.sendlock:
            try:
                self._sendmessage([self.heartbeat, b""])  # send heartbeat message with empty data
            except Exception as e:
                logger.error(
                    f"Error sending heartbeat to {self.ep}: {e} -- possibly connection closed -- closing connection"
                )
                self.close()

    def senddata(self, data: bytes) -> bool:
        if msgkey is not None:
            data = encryptMessage(data, msgkey)
            logger.debug(f"Encrypted message for {self.ep} of length {len(data)} plus header length {len(self.data)}")
        logger.debug(f"Sending data message to {self.ep} of length {len(data)}")
        with self.sendlock:
            try:
                ret = self._sendmessage([self.data, data])
            except Exception as e:
                logger.error(f"Error sending message to {self.ep}: {e}")
                ret = -1
        if ret is None or ret == -1:
            logger.warning(f"Failed to send message to {self.ep} -- closing connection -- ret {ret}", color="yellow")
            self.close()
            return False
        return True

    def close(self):
        with self.lock:
            if self.closecalled:
                return
            self.closecalled = True
        with heartbeatlock:
            if self in messengers:
                messengers.remove(self)
        self._close()
        logger.info(f"Closed messenger for endpoint {self.ep} of type {self.__class__.__name__}", color="cyan")

    def _ingestrecvdata(self, data: bytes):
        raise NotImplementedError("ingestrecvdata not implemented - to be implemented by subclass")

    def recvthread(self):
        if self.initfn is not None:
            self.initfn(self, self.ep)
        while True:
            data = self._networkrecv()  # receive raw data from network
            if data is None or len(data) == 0:
                logger.warning(
                    f"Received empty data from {self.ep} connection closed? -- closing connection", color="yellow"
                )
                break
            logger.debug(f"Received raw data from {self.ep} -- datalen: {len(data)}")
            failed = False
            self._ingestrecvdata(
                data
            )  # ingest raw data into buffer and try to assemble messages, handle messages if fully assembled, return False if connection should be closed, True to continue receiving  # noqa: E501 vendored from microsoft/xavier, not refactored
            while True:
                success, complete, msg = self._handlerecvbytes()  # handle raw data and return message if assembled
                logger.debug(
                    f"Received message from {self.ep} -- success: {success} -- msglen: {len(msg) if msg is not None else 'N/A'}"  # noqa: E501 vendored from microsoft/xavier, not refactored
                )
                if not success:
                    logger.warning(
                        f"Failed to handle received data from {self.ep} -- closing connection", color="yellow"
                    )
                    failed = True
                    break  # failed to handle message, close connection
                if msg is None:
                    if not complete:
                        logger.debug(f"Message from {self.ep} not fully received yet, waiting for more data")
                    break  # message not fully received yet, wait for more data
                msgtype = msg[0:1]
                # print("Msgtype: ", msgtype) # for debugging
                # print("Heartbeat type: ", self.heartbeat) # for debugging
                # print("Data type: ", self.data) # for debugging
                if msgtype == self.heartbeat:
                    self.lastheartbeat = time.time()
                    logger.debug(f"Received heartbeat from {self.ep}")
                elif msgtype == self.data:
                    data = msg[1:]
                    if msgkey is not None:
                        data = decryptMessage(data, msgkey)
                        if data is None:
                            logger.warning(
                                f"Failed to decrypt message from {self.ep} -- closing connection", color="yellow"
                            )
                            failed = True
                            break  # decryption failed, close connection
                    if self.handlefn is not None:
                        logger.debug(f"Handling message from {self.ep} of length {len(data)}")
                        self.handlefn(data, self, self.ep)
                else:
                    logger.warning(
                        f"Received message with unknown type from {self.ep} type {msgtype} -- closing connection",
                        color="yellow",
                    )
                    failed = True
                    break  # unknown message type, close connection
            if failed:
                break
        print(f"Closing connection to {self.ep}")
        if self.closefn is not None:
            self.closefn(self, self.ep)
        self.close()


def isselfip(rmthost: str, rmtport: int, port: int) -> bool:
    # get all local IP addresses
    local_ips = socket.gethostbyname_ex(socket.gethostname())[2]
    # add local host
    local_ips.append("127.0.0.1")
    # if host is ip address, don't do anything, if it is domain name, resolve it
    try:
        rmthostip = socket.gethostbyname(rmthost)
    except socket.gaierror:
        rmthostip = rmthost
    return rmthostip in local_ips and int(rmtport) == port
