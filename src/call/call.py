"""Call state machine – one instance per active SIP call."""

from __future__ import annotations
import asyncio
import logging
import random
import time
from enum import Enum, auto
from typing import Optional, Tuple

from ..sip.message import SIPMessage, build_response
from ..sip.sdp import SDPSession
from ..rtp.session import RTPSession
from ..rtp.port_manager import PortManager
from ..codec.g711 import decode as g711_decode, encode as g711_encode

logger = logging.getLogger(__name__)


class CallState(Enum):
    IDLE = auto()
    INVITE_RECEIVED = auto()
    SDP_NEGOTIATED = auto()
    RTP_ACTIVE = auto()
    AI_STREAM_ACTIVE = auto()
    TERMINATING = auto()
    DONE = auto()


class Call:
    def __init__(
        self,
        call_id: str,
        local_ip: str,
        port_manager: PortManager,
        send_sip,          # async callable(data, addr)
        audio_bridge=None,
        echo_mode: bool = False,
        pcm_dump_path: Optional[str] = None,
    ) -> None:
        self.call_id = call_id
        self.local_ip = local_ip
        self.state = CallState.IDLE
        self.created_at = time.time()

        self._port_manager = port_manager
        self._send_sip = send_sip
        self._audio_bridge = audio_bridge
        self._echo_mode = echo_mode
        self._pcm_dump_path = pcm_dump_path

        # Assigned during INVITE handling
        self._local_tag = str(random.randint(100000, 999999))
        self._rtp: Optional[RTPSession] = None
        self._rtp_port: Optional[int] = None
        self._codec_pt: int = 0
        self._codec_name: str = "PCMU"
        self._remote_addr: Optional[Tuple[str, int]] = None
        self._invite_msg: Optional[SIPMessage] = None

        # PCM debug dump
        self._pcm_file = None

    # ------------------------------------------------------------------
    # SIP event handlers
    # ------------------------------------------------------------------

    async def handle_invite(self, msg: SIPMessage, addr: Tuple[str, int]) -> None:
        self.state = CallState.INVITE_RECEIVED
        self._invite_msg = msg
        self._remote_addr = addr

        caller = _extract_number(msg.from_header or "")
        callee = _extract_number(msg.request_uri or "")
        logger.info("INVITE call_id=%s  %s → %s  from %s", self.call_id, caller, callee, addr[0])

        # 100 Trying (no tag per RFC 3261)
        await self._send_sip(build_response(msg, 100, "Trying"), addr)

        ct = msg.content_type or ""
        if not msg.body or "application/sdp" not in ct.lower():
            logger.warning("INVITE without SDP body – rejecting")
            await self._send_sip(
                build_response(msg, 400, "Bad Request", local_tag=self._local_tag), addr
            )
            self.state = CallState.DONE
            return

        sdp_offer = SDPSession.parse(msg.body.decode("utf-8", errors="replace"))
        codec = sdp_offer.preferred_codec
        if not codec:
            logger.warning("No supported codec in SDP offer – rejecting")
            await self._send_sip(
                build_response(msg, 488, "Not Acceptable Here", local_tag=self._local_tag), addr
            )
            self.state = CallState.DONE
            return

        self._codec_pt, self._codec_name = codec
        remote_rtp_ip = sdp_offer.rtp_ip or addr[0]
        remote_rtp_port = sdp_offer.rtp_port

        # Allocate local RTP port
        self._rtp_port = await self._port_manager.allocate()

        self._rtp = RTPSession(
            local_port=self._rtp_port,
            remote_addr=(remote_rtp_ip, remote_rtp_port),
            payload_type=self._codec_pt,
            on_audio=self._on_rtp_audio,
        )
        await self._rtp.start()

        sdp_answer = sdp_offer.build_answer(
            self.local_ip, self._rtp_port, self._codec_pt, self._codec_name
        ).encode()

        extra = {"Contact": f"<sip:{self.local_ip}:5060>"}
        ok = build_response(msg, 200, "OK", extra, sdp_answer, local_tag=self._local_tag)
        await self._send_sip(ok, addr)

        self.state = CallState.SDP_NEGOTIATED
        logger.info("200 OK sent  call_id=%s  codec=%s  rtp_port=%d",
                    self.call_id, self._codec_name, self._rtp_port)

        if self._pcm_dump_path:
            try:
                self._pcm_file = open(self._pcm_dump_path, "wb")
                logger.info("PCM dump → %s", self._pcm_dump_path)
            except OSError as e:
                logger.warning("Cannot open PCM dump file: %s", e)

    async def handle_ack(self, msg: SIPMessage, addr: Tuple[str, int]) -> None:
        if self.state != CallState.SDP_NEGOTIATED:
            return
        self.state = CallState.RTP_ACTIVE
        logger.info("ACK received – RTP active  call_id=%s", self.call_id)

        if self._audio_bridge and not self._echo_mode:
            self._audio_bridge.register_call(self.call_id, self._on_bridge_event)
            self.state = CallState.AI_STREAM_ACTIVE
            logger.info("Registered with AI bridge  call_id=%s", self.call_id)
        elif self._echo_mode:
            logger.info("Echo mode active  call_id=%s", self.call_id)

    async def handle_bye(self, msg: SIPMessage, addr: Tuple[str, int]) -> None:
        logger.info("BYE received  call_id=%s", self.call_id)
        await self._send_sip(
            build_response(msg, 200, "OK", local_tag=self._local_tag), addr
        )
        await self._cleanup()

    async def handle_cancel(self, msg: SIPMessage, addr: Tuple[str, int]) -> None:
        logger.info("CANCEL received  call_id=%s", self.call_id)
        await self._send_sip(
            build_response(msg, 200, "OK"), addr
        )
        if self._invite_msg:
            await self._send_sip(
                build_response(self._invite_msg, 487, "Request Terminated",
                               local_tag=self._local_tag),
                addr,
            )
        await self._cleanup()

    # ------------------------------------------------------------------
    # Audio paths
    # ------------------------------------------------------------------

    def _on_rtp_audio(self, rtp_payload: bytes) -> None:
        """Called from RTPSession playout loop (event loop thread)."""
        pcm = g711_decode(self._codec_pt, rtp_payload)

        if self._pcm_file:
            try:
                self._pcm_file.write(pcm)
            except OSError:
                pass

        if self._echo_mode and self._rtp:
            # Loopback: encode back and send
            self._rtp.send_audio(g711_encode(self._codec_pt, pcm))
            return

        if self.state == CallState.AI_STREAM_ACTIVE and self._audio_bridge:
            asyncio.ensure_future(
                self._audio_bridge.send_audio(self.call_id, pcm)
            )

    async def _on_bridge_event(self, event: str, data) -> None:
        """Called by the audio bridge for events from AI backend."""
        if event == "audio_out":
            if self._rtp and isinstance(data, bytes):
                self._rtp.send_audio(g711_encode(self._codec_pt, data))
        elif event == "hangup":
            logger.info("AI requested hangup  call_id=%s", self.call_id)
            await self._send_bye()
        elif event == "ai_disconnected":
            logger.warning("AI backend disconnected  call_id=%s", self.call_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _send_bye(self) -> None:
        """Proactively send BYE to remote party."""
        if not self._invite_msg or not self._remote_addr:
            return
        cseq_num = 2
        if self._invite_msg.cseq:
            try:
                cseq_num = int(self._invite_msg.cseq.split()[0]) + 1
            except (ValueError, IndexError):
                pass
        bye_lines = [
            f"BYE {self._invite_msg.request_uri} SIP/2.0",
            f"Via: SIP/2.0/UDP {self.local_ip}:5060;branch=z9hG4bK{random.randint(100000,999999)}",
            f"From: {self._invite_msg.to_header};tag={self._local_tag}",
            f"To: {self._invite_msg.from_header}",
            f"Call-ID: {self.call_id}",
            f"CSeq: {cseq_num} BYE",
            "Content-Length: 0",
            "",
            "",
        ]
        await self._send_sip("\r\n".join(bye_lines).encode(), self._remote_addr)
        await self._cleanup()

    async def _cleanup(self) -> None:
        if self.state == CallState.DONE:
            return
        self.state = CallState.TERMINATING

        if self._pcm_file:
            try:
                self._pcm_file.close()
            except OSError:
                pass
            self._pcm_file = None

        if self._audio_bridge:
            await self._audio_bridge.notify_call_end(self.call_id)
            self._audio_bridge.unregister_call(self.call_id)

        if self._rtp:
            self._rtp.stop()
            self._rtp = None

        if self._rtp_port:
            await self._port_manager.release(self._rtp_port)
            self._rtp_port = None

        duration = time.time() - self.created_at
        logger.info("Call ended  call_id=%s  duration=%.1fs", self.call_id, duration)
        self.state = CallState.DONE


def _extract_number(sip_uri: str) -> str:
    """Best-effort extraction of phone number from SIP URI or From header."""
    import re
    m = re.search(r"sip:([+\d]+)@", sip_uri)
    return m.group(1) if m else sip_uri[:40]
