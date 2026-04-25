"""WebSocket audio bridge between the SIP gateway and the AI backend.

Protocol
--------
The AI backend connects to ws://gateway:8765.

Handshake (text JSON, must be the first message from the client):
    {"call_id": "<uuid>"}

After handshake, binary frames carry raw PCM-16LE audio at 8 kHz mono.

Gateway → AI: 20 ms chunks (320 bytes = 160 samples × 2 bytes).
AI → Gateway: same format; the gateway encodes and sends via RTP.

Control messages (text JSON) from the gateway:
    {"event": "call_end",   "call_id": "..."}

Control messages the AI backend can send:
    {"event": "hangup",     "call_id": "..."}
"""

from __future__ import annotations
import asyncio
import json
import logging
from typing import Callable, Dict, Optional

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

logger = logging.getLogger(__name__)


class AudioBridge:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        if not _WS_AVAILABLE:
            raise ImportError("websockets package is required: pip install websockets")
        self._host = host
        self._port = port
        # call_id -> WebSocket
        self._connections: Dict[str, "WebSocketServerProtocol"] = {}
        # call_id -> async callable(event, data)
        self._handlers: Dict[str, Callable] = {}
        self._server = None

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handle,
            self._host,
            self._port,
        )
        logger.info("Audio bridge listening on ws://%s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # ------------------------------------------------------------------
    # Called by the call layer
    # ------------------------------------------------------------------

    def register_call(self, call_id: str, handler: Callable) -> None:
        """Register an async handler(event, data) for a call."""
        self._handlers[call_id] = handler

    def unregister_call(self, call_id: str) -> None:
        ws = self._connections.pop(call_id, None)
        self._handlers.pop(call_id, None)
        if ws:
            asyncio.ensure_future(ws.close())

    async def send_audio(self, call_id: str, pcm: bytes) -> None:
        """Send PCM16-LE audio to the AI backend."""
        ws = self._connections.get(call_id)
        if ws is None:
            return
        try:
            await ws.send(pcm)
        except Exception:
            self._connections.pop(call_id, None)

    async def notify_call_end(self, call_id: str) -> None:
        ws = self._connections.get(call_id)
        if ws:
            try:
                await ws.send(json.dumps({"event": "call_end", "call_id": call_id}))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # WebSocket handler
    # ------------------------------------------------------------------

    async def _handle(self, ws: "WebSocketServerProtocol", path: str = "/") -> None:
        call_id: Optional[str] = None
        try:
            # Wait for handshake
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            if not isinstance(raw, str):
                logger.warning("AI backend sent binary before handshake – closing")
                return
            data = json.loads(raw)
            call_id = data.get("call_id")
            if not call_id:
                logger.warning("AI backend handshake missing call_id")
                return

            self._connections[call_id] = ws
            logger.info("AI backend connected for call %s", call_id)

            if call_id in self._handlers:
                await self._handlers[call_id]("ai_connected", ws)

            async for msg in ws:
                if isinstance(msg, bytes):
                    # PCM audio from AI → route to RTP sender
                    if call_id in self._handlers:
                        await self._handlers[call_id]("audio_out", msg)
                elif isinstance(msg, str):
                    ctrl = json.loads(msg)
                    evt = ctrl.get("event")
                    if evt == "hangup" and call_id in self._handlers:
                        await self._handlers[call_id]("hangup", None)
                        break

        except asyncio.TimeoutError:
            logger.warning("AI backend handshake timeout")
        except Exception:
            logger.exception("AI bridge WebSocket error (call=%s)", call_id)
        finally:
            if call_id:
                self._connections.pop(call_id, None)
                if call_id in self._handlers:
                    await self._handlers[call_id]("ai_disconnected", None)
