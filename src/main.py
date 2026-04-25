"""Gateway bootstrap – wires all components and runs the event loop."""

from __future__ import annotations
import asyncio
import logging
import os
import signal
from typing import Optional

from .config import Config
from .sip.server import SIPServer
from .rtp.port_manager import PortManager
from .call.manager import CallManager
from .bridge.websocket_bridge import AudioBridge


def setup_logging(level: str, log_file: Optional[str] = None) -> None:
    fmt = "%(asctime)s %(levelname)-8s %(name)-30s %(message)s"
    handlers = [logging.StreamHandler()]
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, handlers=handlers)


async def run(cfg: Config) -> None:
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("SIP Media Gateway starting")
    logger.info("  Public IP   : %s", cfg.public_ip)
    logger.info("  SIP         : UDP %s:%d", cfg.sip_host, cfg.sip_port)
    logger.info("  RTP range   : %d – %d", cfg.rtp_port_min, cfg.rtp_port_max)
    logger.info("  WS bridge   : ws://%s:%d", cfg.bridge_host, cfg.bridge_port)
    if cfg.echo_mode:
        logger.info("  Mode        : ECHO (loopback test)")
    if cfg.pcm_dump_dir:
        logger.info("  PCM dump    : %s", cfg.pcm_dump_dir)
    logger.info("=" * 60)

    port_manager = PortManager(cfg.rtp_port_min, cfg.rtp_port_max)

    sip_server = SIPServer(
        host=cfg.sip_host,
        port=cfg.sip_port,
        allowed_ips=cfg.allowed_ips,
    )

    audio_bridge = AudioBridge(host=cfg.bridge_host, port=cfg.bridge_port)

    call_manager = CallManager(
        local_ip=cfg.public_ip,
        port_manager=port_manager,
        send_sip=sip_server.send,
        audio_bridge=audio_bridge if not cfg.echo_mode else None,
        max_calls=cfg.max_calls,
        echo_mode=cfg.echo_mode,
        pcm_dump_dir=cfg.pcm_dump_dir,
    )

    sip_server.set_message_handler(call_manager.dispatch)

    await sip_server.start()
    await audio_bridge.start()

    logger.info("Gateway ready – waiting for calls")

    loop = asyncio.get_event_loop()
    stop = asyncio.Event()

    def _on_signal() -> None:
        logger.info("Shutdown signal received")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            pass  # Windows

    await stop.wait()

    logger.info("Shutting down…")
    sip_server.stop()
    await audio_bridge.stop()
    logger.info("Goodbye.")
