"""Configuration loader."""

from __future__ import annotations
import os
import socket
from typing import List, Optional

import yaml


class Config:
    def __init__(self, path: str = "config/config.yaml") -> None:
        with open(path) as f:
            d = yaml.safe_load(f)

        sip = d.get("sip", {})
        self.sip_host: str = sip.get("host", "0.0.0.0")
        self.sip_port: int = sip.get("port", 5060)
        raw_ip: str = sip.get("public_ip", "AUTO")
        self.public_ip: str = _detect_ip() if raw_ip == "AUTO" else raw_ip
        self.allowed_ips: List[str] = sip.get("allowed_ips") or []

        rtp = d.get("rtp", {})
        self.rtp_port_min: int = rtp.get("port_min", 10000)
        self.rtp_port_max: int = rtp.get("port_max", 20000)
        self.jitter_depth: int = rtp.get("jitter_buffer_packets", 3)

        bridge = d.get("bridge", {})
        self.bridge_host: str = bridge.get("host", "0.0.0.0")
        self.bridge_port: int = bridge.get("port", 8765)
        self.ai_webhook_url: str = bridge.get("ai_webhook_url", "")
        self.frame_ms: int = bridge.get("frame_ms", 20)

        perf = d.get("performance", {})
        self.max_calls: int = perf.get("max_concurrent_calls", 50)

        log = d.get("logging", {})
        self.log_level: str = log.get("level", "INFO")
        self.log_file: Optional[str] = log.get("file")

        # Runtime flags (set by CLI args)
        self.echo_mode: bool = False
        self.pcm_dump_dir: Optional[str] = None


def _detect_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
