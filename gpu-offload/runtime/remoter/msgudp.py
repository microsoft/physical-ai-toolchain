from __future__ import annotations

import socket
import socketserver
import struct
import threading
import time
from collections.abc import Callable
from queue import Queue

from . import msgsock
from .msgsock import Messenger, logger
from .safe_codec import CodecLimits

HEADER_STRUCT = struct.Struct("!IHH")  # message index, chunk index, total chunks
CHUNK_SIZE = 1200
HEADER_SIZE = HEADER_STRUCT.size
# a peer-declared totalchunks above this would reassemble to more than the codec's
# max frame size anyway, so reject it before allocating the [None] * totalchunks list
_MAX_CHUNKS_PER_MESSAGE = (CodecLimits().max_encoded_bytes + CHUNK_SIZE - 1) // CHUNK_SIZE

usesingleudpsock = True  # set to True to use single socket for all client-side UDP messengers, set to False to create separate socket for each messenger (not needed since UDP is connectionless)  # noqa: E501 vendored from microsoft/xavier, not refactored
udp_msgrs: dict[tuple, MessengerUDP] = {}  # (ip, port) to MessengerUDP (for server-side)
udp_msgrs_client: dict[tuple, MessengerUDP] = {}  # (ip, port) to MessengerUDP (for client-side)
lock = threading.RLock()  # lock to protect udp_msgrs
bitrate = 50 * 1024 * 1024  # 100 Mbps bitrate for UDP messenger, can be adjusted as needed
tokenbucket = (
    0.01 * bitrate
)  # token bucket size for rate limiting UDP messages, set to 10ms worth of data at the bitrate
tokenbucket = min(tokenbucket, 10 * 1024 * 8)  # cap token bucket at 10KB to prevent excessive memory usage
singlesock: socket.socket | None = (
    None  # single socket for client-side UDP messengers to use, since UDP is connectionless, we can reuse the same socket for all outgoing messages  # noqa: E501 vendored from microsoft/xavier, not refactored
)


def recvUDPThread(sock: socket.socket):
    while True:
        data, addr = sock.recvfrom(4096)  # receive up to 4096 bytes at a time
        handleUDPPacket(False, (data, sock), addr, udp_msgrs_client, lock)


def udpthread():
    logger.info("UDP messenger cleanup/tokenfill thread started.", color="green")
    while True:
        # time.sleep(10)
        time.sleep(0.01)
        with lock:
            # take snapshot of messengers to avoid holding lock while cleaning up
            allmsngrs = list(udp_msgrs.values()) + list(udp_msgrs_client.values())
        curtime = time.time()
        for msgr in allmsngrs:
            msgr: MessengerUDP = msgr
            msgr._tokenfill(curtime)
            msgr._cleanup(curtime)


def initUDP():
    global singlesock
    if usesingleudpsock:  # use single socket for client-side messengers, set to False to create separate socket for each messenger (not needed)  # noqa: E501 vendored from microsoft/xavier, not refactored
        singlesock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # bind to a random free port on all interfaces (not just loopback -- the server may be on a remote host)
        # for receiving responses; each MessengerUDP later calls connect() to set its default send destination,
        # and _networkrecv() checks the sender address against the expected server address before accepting data
        singlesock.bind(("", 0))
        logger.info(f"Created single UDP socket for client-side messengers: {singlesock.getsockname()}", color="cyan")
        threading.Thread(target=recvUDPThread, args=(singlesock,), daemon=True).start()
    threading.Thread(target=udpthread, daemon=True).start()


class MessengerUDP(Messenger):
    def __init__(
        self,
        sock: socket.socket | None,
        ep: str,
        isserver: bool,
        initfn: Callable[[Messenger, str], None] | None = None,
        handlefn: Callable[[bytes, Messenger, str], None] | None = None,
        closefn: Callable[[Messenger, str], None] | None = None,
    ):
        # of form udp://host:port
        assert ep.startswith("udp://"), "Invalid endpoint for MessengerUDP, must start with udp://"
        host, port = ep[6:].split(":")
        if sock is None and singlesock is None:
            assert not isserver, (
                "Server-side MessengerUDP requires a socket to be provided since single socket is not available"
            )
            # create socket and connect to server
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect((host, int(port)))
            self.sock = sock
        elif sock is not None:
            assert isserver, (
                "Client-side MessengerUDP should not have a socket provided since single socket is available"
            )
            self.sock = sock
        else:
            assert isserver or singlesock is not None, (
                "Client-side MessengerUDP requires a socket to be provided or a single socket to be available"
            )
            self.sock = singlesock  # if None, use recvQ with single socket for receiving, if not None then use this socket for sending and receiving  # noqa: E501 vendored from microsoft/xavier, not refactored
        self.isserver = isserver
        self.recvQ = Queue()  # queue to receive messages from recv thread
        startrecvthread = True  # for UDP, always start recv thread
        self.host = host
        self.port = int(port)
        self.addr = (self.host, self.port)
        hostip = socket.gethostbyname(self.host)
        self.ipaddr = (hostip, self.port)
        super().__init__(ep, initfn, handlefn, closefn)
        self.curmsg = b""
        self.ep = ep
        self.messageindex = 0
        self.maxmessageindex = -1
        self.idxlock = threading.Lock()  # protects messageindex/messages/tokens (distinct from base class's self.lock)
        self.messages = {}  # messageindex to list of chunks received so far
        self.lastcleanup = time.time()
        self.lasttokenfill = time.time()
        self.curdata = b""
        self.tokens = tokenbucket  # initialize tokens to full bucket for rate limiting
        # convert host to IP address for client-side messengers since server will send messages to IP address and we need to match that in recv thread  # noqa: E501 vendored from microsoft/xavier, not refactored
        hostip = socket.gethostbyname(host)
        if not isserver:
            with lock:
                udp_msgrs_client[(hostip, int(port))] = self
            logger.info(f"Client endpoint {self.sock.getsockname()} connected to server at {ep}", color="cyan")
        else:
            with lock:
                udp_msgrs[(hostip, int(port))] = self  # add to global dict of UDP messengers for server side
            logger.info(f"Server messenger created for client at {ep} using {self.sock.getsockname()}", color="cyan")
        if startrecvthread:
            threading.Thread(target=self.recvthread, daemon=True).start()

    def _tokenfill(self, curtime):
        with self.idxlock:
            # fill tokens based on time elapsed since last fill
            elapsed = curtime - self.lasttokenfill
            self.tokens = min(tokenbucket, self.tokens + elapsed * bitrate)
            self.lasttokenfill = curtime

    def _networkrecv(self):
        if self.isserver or self.sock is None or self.sock == singlesock:
            # server or client using single socket, receive messages from recvQ populated by recv thread
            return self.recvQ.get()  # block until message received from recv thread
        else:
            assert singlesock is None, (
                "If single socket is available, client-side MessengerUDP should use that instead of separate sockets"
            )
            try:
                data, retaddr = self.sock.recvfrom(4096)  # receive up to 4096 bytes at a time
            except OSError as e:
                # for UDP this is unlikely to happen since sockets don't close, rely on heartbeat to do close
                logger.debug(f"Socket error on recv {e}")
                return b""  # indicate connection closed or error by returning empty bytes
            if retaddr != self.ipaddr:
                # this may fail due to localhost vs actual IP address, so just log a warning and ignore the message
                logger.warning(
                    f"Received UDP message from unexpected address {retaddr} (expected {self.ipaddr}) -- ignoring",
                    color="yellow",
                )
            return data

    # for UDP out of order messageas allowed, but if received messages from future then delete old
    def _cleanup(self, curtime=None):
        if curtime is None:
            curtime = time.time()
        if curtime - self.lastcleanup < 10:  # only cleanup every 10 seconds
            return
        # cleanup old messages that have not been fully received to prevent memory leak
        with self.idxlock:
            if curtime - self.lastcleanup < 10:  # check again after acquiring lock
                return
            self.lastcleanup = curtime
            todelete = []
            for messageindex in self.messages:
                # if message index is more than 3 less than max received, delete it
                if messageindex < self.maxmessageindex - 3 or curtime - self.messages[messageindex]["time"] > 60:
                    todelete.append(messageindex)
            for messageindex in todelete:
                logger.info(f"Cleaning up old UDP message with index {messageindex} from {self.addr}", color="yellow")
                del self.messages[messageindex]

    def _ingestrecvdata(self, data: bytes):
        logger.debug(f"Received UDP message chunk from {self.addr} of length {len(data)}")
        self.curdata = data

    def _handlerecvbytes(self) -> tuple[bool, bool, bytes | None]:
        if len(self.curdata) == 0:
            return True, True, None  # done with data chunk
        if len(self.curdata) < HEADER_SIZE:
            logger.warning(
                f"Received UDP message too short to contain header from {self.addr}  of length {len(self.curdata)} -- ignoring",  # noqa: E501 vendored from microsoft/xavier, not refactored
                color="yellow",
            )
            return False, False, None  # invalid message, close connection
        messageindex, chunkindex, totalchunks = HEADER_STRUCT.unpack(self.curdata[:HEADER_SIZE])
        logger.debug(
            f"Received UDP message chunk from {self.addr} with message index {messageindex}, chunk index {chunkindex}, total chunks {totalchunks}"  # noqa: E501 vendored from microsoft/xavier, not refactored
        )
        if not (0 < totalchunks <= _MAX_CHUNKS_PER_MESSAGE) or not (0 <= chunkindex < totalchunks):
            logger.warning(
                f"Received UDP message with invalid totalchunks {totalchunks} / chunkindex {chunkindex} "
                f"from {self.addr} -- ignoring",
                color="yellow",
            )
            self.curdata = b""
            return False, False, None  # invalid message, close connection
        self.maxmessageindex = max(self.maxmessageindex, messageindex)
        chunkdata = self.curdata[HEADER_SIZE:]
        with self.idxlock:
            if messageindex not in self.messages:
                self.messages[messageindex] = {"time": time.time(), "data": [None] * totalchunks}
            existing_chunks = self.messages[messageindex]["data"]
            if len(existing_chunks) != totalchunks:
                logger.warning(
                    f"Received UDP message with mismatched totalchunks for message {messageindex} "
                    f"from {self.addr} -- ignoring chunk",
                    color="yellow",
                )
                self.curdata = b""
                return True, False, None
            existing_chunks[chunkindex] = chunkdata
        self.curdata = b""  # clear data after ingesting
        self._cleanup()
        if all(chunk is not None for chunk in self.messages[messageindex]["data"]):
            fullmsg = b"".join(self.messages[messageindex]["data"])
            del self.messages[messageindex]
            return True, True, fullmsg
        else:
            return True, False, None

    def _close(self):
        if not self.isserver and self.sock is not None and self.sock != singlesock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError as e:
                logger.debug(f"Socket error on shutdown {e}")
            try:
                self.sock.close()
            except OSError as e:
                logger.debug(f"Socket error on close {e}")
        else:
            logger.info(f"Adding empty message to recv queue to signal recv thread to exit for {self.addr}")
            self.recvQ.put(b"")  # put empty message to signal recv thread to exit
        with lock:
            if self.isserver and self.addr in udp_msgrs:
                del udp_msgrs[self.addr]
            if not self.isserver and self.addr in udp_msgrs_client:
                del udp_msgrs_client[self.addr]

    def _sendmessage(self, message: list[bytes]) -> int | None:
        # break up into chunks of 1200 bytes and send each chunk with a header of message index and total messages
        with self.idxlock:
            messageindex = self.messageindex
            self.messageindex += 1
        totallen = sum(len(m) for m in message)
        numchunks = (totallen + CHUNK_SIZE - 1) // CHUNK_SIZE
        start = 0
        logger.debug(f"Sending message to {self.ep} of total length {totallen} in {numchunks} chunks")
        for chunkindex in range(numchunks):
            # time.sleep(0.001) # small sleep to prevent overwhelming the network, can be adjusted as needed
            hdr = HEADER_STRUCT.pack(messageindex, chunkindex, numchunks)
            if chunkindex == 0:
                tosend = min(CHUNK_SIZE - len(message[0]), len(message[1]) - start)
                sendmsgs = [hdr, message[0], memoryview(message[1])[start : start + tosend]]
            else:
                tosend = min(CHUNK_SIZE, len(message[1]) - start)
                sendmsgs = [hdr, memoryview(message[1])[start : start + tosend]]
            start += tosend
            totalchunklen = sum(len(m) for m in sendmsgs)
            while self.tokens < totalchunklen:
                time.sleep(0.001)  # wait for tokens to be refilled, can be adjusted as needed
            with self.idxlock:
                self.tokens -= totalchunklen * 8  # convert bytes to bits for token calculation
            logger.debug(
                f"Sending chunk {chunkindex + 1}/{numchunks} of message {messageindex} to {self.ep} of length {totalchunklen}"  # noqa: E501 vendored from microsoft/xavier, not refactored
            )
            assert self.sock is not None, "Socket is None in _sendmessage of MessengerUDP"
            msgsock.sendmsg(self.sock, sendmsgs, [], 0, self.addr)
        assert start == len(message[1]), (
            f"Error in sending message, sent {start} bytes but expected to send {len(message[1])} bytes"
        )
        return start


def handleUDPPacket(
    isserver: bool,
    request: tuple[bytes, socket.socket],
    client_address: tuple[str, int],
    msgrs: dict[tuple, MessengerUDP],
    lock: threading.RLock,
    initfn: Callable[[Messenger, str], None] | None = None,
    handlefn: Callable[[bytes, Messenger, str], None] | None = None,
    closefn: Callable[[Messenger, str], None] | None = None,
):
    data, sock = request
    clientip, clientport = client_address
    addr = (clientip, int(clientport))
    logger.debug(f"GLOBAL RECV from {addr} - {len(data)} bytes")
    msgr = msgrs.get(addr)
    if msgr is None:
        with lock:
            if addr not in msgrs:
                assert isserver, (
                    f"Received UDP message from unknown client address {addr} on client side, keys in msgrs: {list(msgrs.keys())}"  # noqa: E501 vendored from microsoft/xavier, not refactored
                )
                logger.info(f"New UDP connection from {addr} - creating MessengerUDP", color="green")
                # creattion will add to msgrs, so we can safely put message into recvQ after creation
                msgr = MessengerUDP(sock, f"udp://{clientip}:{clientport}", isserver, initfn, handlefn, closefn)
            else:
                msgr = msgrs[addr]
    msgr.recvQ.put(data)  # put received data into MessengerUDP's recv queue


def getUDPMsgReqHandler(
    initfn: Callable[[Messenger, str], None] | None = None,
    handlefn: Callable[[bytes, Messenger, str], None] | None = None,
    closefn: Callable[[Messenger, str], None] | None = None,
):
    class UDPRequestHandler(socketserver.BaseRequestHandler):
        def handle(self):
            handleUDPPacket(True, self.request, self.client_address, udp_msgrs, lock, initfn, handlefn, closefn)

    return UDPRequestHandler


class MessageServerUDP(socketserver.ThreadingUDPServer):
    allow_reuse_address = True  # allow quick restart of server

    def __init__(
        self,
        serverhost,
        serverport,
        initfn: Callable[[Messenger, str], None] | None = None,
        handlefn: Callable[[bytes, Messenger, str], None] | None = None,
        closefn: Callable[[Messenger, str], None] | None = None,
    ):
        udpreqhandler = getUDPMsgReqHandler(initfn, handlefn, closefn)
        logger.info(f"Starting UDP server on {serverhost}:{serverport}...", color="green")
        self.host = serverhost
        self.port = serverport
        super().__init__((serverhost, serverport), udpreqhandler)

    def isself(self, ep):
        protocol, addr = ep.split("://", 1)
        if protocol != "udp":
            return False
        host, port = addr.split(":", 1)
        return msgsock.isselfip(host, int(port), self.port)
