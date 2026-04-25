"""Call manager – routes SIP messages to the correct Call instance."""

from __future__ import annotations
import logging
from typing import Callable, Dict, Optional

from ..sip.message import SIPMessage, build_response
from ..rtp.port_manager import PortManager
from .call import Call, CallState

logger = logging.getLogger(__name__)


class CallManager:
    def __init__(
        self,
        local_ip: str,
        port_manager: PortManager,
        send_sip: Callable,
        audio_bridge=None,
        max_calls: int = 50,
        echo_mode: bool = False,
        pcm_dump_dir: Optional[str] = None,
    ) -> None:
        self._local_ip = local_ip
        self._port_manager = port_manager
        self._send_sip = send_sip
        self._audio_bridge = audio_bridge
        self._max_calls = max_calls
        self._echo_mode = echo_mode
        self._pcm_dump_dir = pcm_dump_dir

        self._calls: Dict[str, Call] = {}

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def dispatch(self, msg: SIPMessage, addr: tuple) -> None:
        call_id = msg.call_id
        if not call_id:
            logger.warning("SIP message without Call-ID from %s – ignored", addr)
            return

        if not msg.is_request:
            return  # we only act as UAS

        method = msg.method
        if method == "INVITE":
            await self._on_invite(msg, addr, call_id)
        elif method == "ACK":
            call = self._calls.get(call_id)
            if call:
                await call.handle_ack(msg, addr)
        elif method == "BYE":
            call = self._calls.pop(call_id, None)
            if call:
                await call.handle_bye(msg, addr)
            else:
                # Unknown dialog – still reply 200
                await self._send_sip(build_response(msg, 200, "OK"), addr)
        elif method == "CANCEL":
            call = self._calls.pop(call_id, None)
            if call:
                await call.handle_cancel(msg, addr)
            else:
                await self._send_sip(build_response(msg, 200, "OK"), addr)
        elif method == "OPTIONS":
            await self._send_sip(
                build_response(msg, 200, "OK",
                               {"Accept": "application/sdp"}),
                addr,
            )
        else:
            await self._send_sip(build_response(msg, 501, "Not Implemented"), addr)

        # Purge DONE calls from the dict
        done = [cid for cid, c in self._calls.items() if c.state == CallState.DONE]
        for cid in done:
            del self._calls[cid]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _on_invite(self, msg: SIPMessage, addr: tuple, call_id: str) -> None:
        if len(self._calls) >= self._max_calls:
            logger.warning("Max concurrent calls reached – rejecting INVITE")
            await self._send_sip(build_response(msg, 486, "Busy Here"), addr)
            return

        if call_id in self._calls:
            # Re-INVITE: handle in existing call
            await self._calls[call_id].handle_invite(msg, addr)
            return

        dump_path: Optional[str] = None
        if self._pcm_dump_dir:
            import os, time
            fname = f"call_{call_id[:8]}_{int(time.time())}.pcm"
            dump_path = os.path.join(self._pcm_dump_dir, fname)

        call = Call(
            call_id=call_id,
            local_ip=self._local_ip,
            port_manager=self._port_manager,
            send_sip=self._send_sip,
            audio_bridge=self._audio_bridge,
            echo_mode=self._echo_mode,
            pcm_dump_path=dump_path,
        )
        self._calls[call_id] = call
        await call.handle_invite(msg, addr)

    @property
    def active_calls(self) -> int:
        return len(self._calls)
