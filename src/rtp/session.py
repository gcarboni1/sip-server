"""RTP session: receive inbound packets and send outbound packets."""

from __future__ import annotations
import asyncio
import logging
import random
from typing import Callable, Optional, Tuple

from .packet import RTPPacket
from .jitter_buffer import JitterBuffer

logger = logging.getLogger(__name__)


class RTPSession:
    """
    Manages a single RTP stream (one call leg).

    - Binds a UDP socket on `local_port`.
    - Receives RTP packets, passes decoded payloads to `on_audio`.
    - Sends RTP packets via `send_audio()`.
    """

    def __init__(
        self,
        local_port: int,
        remote_addr: Tuple[str, int],
        payload_type: int,
        on_audio: Callable[[bytes], None],
        jitter_depth: int = 3,
    ) -> None:
        self.local_port = local_port
        self.remote_addr = remote_addr
        self.payload_type = payload_type
        self.on_audio = on_audio

        self._ssrc = random.randint(0, 0xFFFFFFFF)
        self._seq = random.randint(0, 0xFFFF)
        self._timestamp = random.randint(0, 0xFFFFFFFF)

        self._jitter = JitterBuffer(depth=jitter_depth)
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._running = False
        self._playout_task: Optional[asyncio.Task] = None

        # stats
        self.rx_packets = 0
        self.tx_packets = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        self._running = True
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _RTPProtocol(self),
            local_addr=("0.0.0.0", self.local_port),
        )
        self._playout_task = asyncio.create_task(self._playout_loop())
        logger.debug("RTP session started on port %d → %s:%d",
                     self.local_port, *self.remote_addr)

    def stop(self) -> None:
        self._running = False
        if self._playout_task:
            self._playout_task.cancel()
        if self._transport:
            self._transport.close()
            self._transport = None
        logger.debug("RTP session stopped (port %d, rx=%d tx=%d)",
                     self.local_port, self.rx_packets, self.tx_packets)

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    def send_audio(self, rtp_payload: bytes) -> None:
        """Send a single RTP packet containing the given payload bytes."""
        if not self._transport or not self._running:
            return
        pkt = RTPPacket(
            payload_type=self.payload_type,
            sequence=self._seq & 0xFFFF,
            timestamp=self._timestamp & 0xFFFFFFFF,
            ssrc=self._ssrc,
            payload=rtp_payload,
        )
        self._seq = (self._seq + 1) & 0xFFFF
        # 8 kHz, 8-bit samples → each byte = 1 sample = 125 µs
        self._timestamp = (self._timestamp + len(rtp_payload)) & 0xFFFFFFFF
        self._transport.sendto(pkt.build(), self.remote_addr)
        self.tx_packets += 1

    # ------------------------------------------------------------------
    # Receive (called from protocol)
    # ------------------------------------------------------------------

    def _receive(self, data: bytes, addr: Tuple[str, int]) -> None:
        pkt = RTPPacket.parse(data)
        if pkt is None:
            return
        self.rx_packets += 1
        # Accept media from the expected remote; update if they remap port
        if addr[0] == self.remote_addr[0]:
            self.remote_addr = (addr[0], addr[1])
        self._jitter.push(pkt.sequence, pkt.payload)

    # ------------------------------------------------------------------
    # Playout loop – fires every 20 ms
    # ------------------------------------------------------------------

    async def _playout_loop(self) -> None:
        INTERVAL = 0.020  # 20 ms
        while self._running:
            await asyncio.sleep(INTERVAL)
            payload = self._jitter.pop()
            if payload is not None and self.on_audio:
                try:
                    self.on_audio(payload)
                except Exception:
                    logger.exception("on_audio callback error")


class _RTPProtocol(asyncio.DatagramProtocol):
    def __init__(self, session: RTPSession) -> None:
        self._session = session

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self._session._receive(data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.warning("RTP socket error: %s", exc)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        pass
