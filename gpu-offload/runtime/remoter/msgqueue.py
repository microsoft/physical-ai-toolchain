from __future__ import annotations

import threading
from collections.abc import Callable
from queue import Queue


def queueMessage(queue: Queue[bytes], msg: bytes):
    msgLen = len(msg)
    # network byte order is big endian
    msgLenBytes = msgLen.to_bytes(4, byteorder="big")
    queue.put(msgLenBytes + msg)


# instead of recv, use a queue to handle messages
# instead of send, push to the queue
def fromqueueThread(queue: Queue[bytes], handlefn: Callable[[bytes, None, str], None]):
    while True:
        data = queue.get()  # since block, will never return None
        msg = data[4:]  # first 4 bytes are length
        handlefn(msg, None, "queue")  # use "queue" as ep for messages from queue


def startFromQueueThread(queue: Queue[bytes], handlefn: Callable[[bytes, None, str], None]):
    t = threading.Thread(target=fromqueueThread, args=(queue, handlefn))
    t.daemon = True
    t.start()


class QueueProc:
    def __init__(self, queue: Queue[bytes], handlefn: Callable[[bytes, None, str], None]):
        self.queue = queue
        startFromQueueThread(queue, handlefn)

    def putMessage(self, msg: bytes):
        queueMessage(self.queue, msg)
