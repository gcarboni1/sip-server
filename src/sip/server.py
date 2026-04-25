"""Async SIP UDP server with IP whitelisting."""

from __future__ import annotations
import asyncio
import ipaddress
import logging
from typing import Callable, List, Optional

from .message import SIPMessage

logger = logging.getLogger(__name__)


class SIPServer:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5060,
        allowed_ips: Optional[List[str]] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._allowed_networks: List[ipaddress.IPv4Network] = []
        self._on_message: Optional[Callable] = None
        self._transport: Optional[asyncio.DatagramTransport] = None

        for entry in (allowed_ips or []):
            try:
                self._allowed_networks.append(
                    ipaddress.ip_network(entry, strict=False)
                )
            except ValueError:
                logger.warning("Invalid IP/CIDR in allowed_ips: %s", entry)

    def set_message_handler(self, handler: Callable) -> None:
        """Register an async coroutine handler(msg, addr)."""
        self._on_message = handler

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _SIPProtocol(self),
            local_addr=(self._host, self._port),
        )
        logger.info("SIP server listening on UDP %s:%s", self._host, self._port)

    def stop(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    async def send(self, data: bytes, addr: tuple) -> None:
        if self._transport:
            self._transport.sendto(data, addr)

    def _is_allowed(self, ip: str) -> bool:
        if not self._allowed_networks:
            return True
        try:
            addr = ipaddress.ip_address(ip)
            return any(addr in net for net in self._allowed_networks)
        except ValueError:
            return False

    async def _dispatch(self, data: bytes, addr: tuple) -> None:
        ip = addr[0]
        if not self._is_allowed(ip):
            logger.warning("Blocked SIP from unauthorised IP %s", ip)
            return
        try:
            msg = SIPMessage.parse(data)
            if self._on_message:
                await self._on_message(msg, addr)
        except Exception:
            logger.exception("Error processing SIP from %s", addr)


class _SIPProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: SIPServer) -> None:
        self._server = server

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        asyncio.ensure_future(self._server._dispatch(data, addr))

    def error_received(self, exc: Exception) -> None:
        logger.error("SIP socket error: %s", exc)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            logger.error("SIP connection lost: %s", exc)
