"""Simple sequence-ordered jitter buffer for RTP audio.

Stores up to `depth` packets, then releases them in sequence order.
Handles out-of-order arrival and provides basic packet-loss concealment
(silence frames for missing packets).
"""

from __future__ import annotations
from typing import Dict, Optional


class JitterBuffer:
    def __init__(self, depth: int = 3, silence_on_loss: bool = True) -> None:
        """
        depth: number of packets to buffer before starting playout.
        silence_on_loss: if True, return zero-filled PCM for lost packets.
        """
        self._depth = depth
        self._silence_on_loss = silence_on_loss
        self._buf: Dict[int, bytes] = {}
        self._next_seq: Optional[int] = None
        self._filled: int = 0          # packets pushed, used for initial fill

    def push(self, seq: int, payload: bytes) -> None:
        seq &= 0xFFFF
        if self._next_seq is None:
            self._next_seq = seq
        self._buf[seq] = payload
        self._filled += 1

    def pop(self) -> Optional[bytes]:
        """Return next payload in sequence, or None if not yet ready."""
        if self._next_seq is None:
            return None
        # Wait for initial fill
        if self._filled < self._depth:
            return None

        seq = self._next_seq

        if seq in self._buf:
            payload = self._buf.pop(seq)
            self._next_seq = (seq + 1) & 0xFFFF
            return payload

        # Packet lost or late: advance and return silence or None
        self._next_seq = (seq + 1) & 0xFFFF
        if self._silence_on_loss:
            # 160 samples × 1 byte/sample (ulaw) = 160 bytes silence
            return bytes(160)
        return None

    def reset(self) -> None:
        self._buf.clear()
        self._next_seq = None
        self._filled = 0

    @property
    def pending(self) -> int:
        return len(self._buf)
