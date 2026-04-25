"""UDP port allocator for RTP sessions."""

from __future__ import annotations
import asyncio
import socket
from typing import Set


class PortManager:
    def __init__(self, min_port: int = 10000, max_port: int = 20000) -> None:
        self._min = min_port
        self._max = max_port
        self._used: Set[int] = set()
        self._lock = asyncio.Lock()

    async def allocate(self) -> int:
        """Allocate an even port (RTP); RTCP uses port+1."""
        async with self._lock:
            for port in range(self._min, self._max, 2):
                if port not in self._used and _port_free(port):
                    self._used.add(port)
                    return port
        raise RuntimeError("No free RTP ports in range")

    async def release(self, port: int) -> None:
        async with self._lock:
            self._used.discard(port)

    @property
    def used_count(self) -> int:
        return len(self._used)


def _port_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
