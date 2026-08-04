"""Logging: stdout plus an in-memory ring the status page renders.

The ring handler owns its own lock and never touches the status store.
Holding the status lock while logging, or reaching into the status store
from a handler, would deadlock the worker against the HTTP thread.
"""

import logging
import sys
import threading
from collections import deque

FORMAT = "[%(asctime)s] %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"


class RingHandler(logging.Handler):
    def __init__(self, maxlen=200):
        super().__init__()
        self._lock = threading.Lock()
        self._lines = deque(maxlen=maxlen)

    def emit(self, record):
        line = self.format(record)
        with self._lock:
            self._lines.append(line)

    def lines(self):
        with self._lock:
            return list(self._lines)


def configure(level="INFO", ring_size=200) -> RingHandler:
    ring = RingHandler(ring_size)
    fmt = logging.Formatter(FORMAT, datefmt=DATEFMT)
    ring.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)

    # sys.stdout is None under a GUI subsystem; guard so a console handler
    # is never attached to nothing.
    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        root.addHandler(console)
    root.addHandler(ring)
    return ring
