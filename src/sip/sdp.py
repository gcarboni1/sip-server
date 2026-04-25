"""SDP offer/answer parsing and generation (RFC 4566)."""

from __future__ import annotations
import re
import time
from typing import Dict, List, Optional, Tuple


class SDPMedia:
    def __init__(self) -> None:
        self.type: str = "audio"
        self.port: int = 0
        self.proto: str = "RTP/AVP"
        self.formats: List[str] = []
        # pt -> (codec_name, clock_rate)
        self.rtpmap: Dict[int, Tuple[str, int]] = {}
        self.ptime: int = 20
        self.direction: str = "sendrecv"
        # override connection from session level
        self.connection: Optional[Tuple[str, str, str]] = None


class SDPSession:
    def __init__(self) -> None:
        self.version: int = 0
        self.origin: Optional[str] = None
        self.session_name: str = "-"
        self.connection: Optional[Tuple[str, str, str]] = None
        self.time: str = "0 0"
        self.media: List[SDPMedia] = []

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def rtp_ip(self) -> Optional[str]:
        for m in self.media:
            if m.type == "audio" and m.connection:
                return m.connection[2]
        return self.connection[2] if self.connection else None

    @property
    def rtp_port(self) -> int:
        for m in self.media:
            if m.type == "audio":
                return m.port
        return 0

    @property
    def preferred_codec(self) -> Optional[Tuple[int, str]]:
        """Return (payload_type, codec_name) for the first supported G.711 codec."""
        for m in self.media:
            if m.type != "audio":
                continue
            for fmt in m.formats:
                try:
                    pt = int(fmt)
                except ValueError:
                    continue
                # Static payload types
                if pt == 0:
                    return (0, "PCMU")
                if pt == 8:
                    return (8, "PCMA")
                # Dynamic payload types from rtpmap
                if pt in m.rtpmap:
                    name = m.rtpmap[pt][0].upper()
                    if name in ("PCMU", "PCMA"):
                        return (pt, name)
        return None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> "SDPSession":
        sdp = cls()
        current_media: Optional[SDPMedia] = None

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            type_char, _, value = line.partition("=")
            type_char = type_char.strip()

            if type_char == "v":
                try:
                    sdp.version = int(value)
                except ValueError:
                    pass
            elif type_char == "o":
                sdp.origin = value
            elif type_char == "s":
                sdp.session_name = value
            elif type_char == "c":
                conn = _parse_connection(value)
                if current_media is not None:
                    current_media.connection = conn
                else:
                    sdp.connection = conn
            elif type_char == "t":
                sdp.time = value
            elif type_char == "m":
                parts = value.split()
                if len(parts) < 3:
                    continue
                m = SDPMedia()
                m.type = parts[0]
                # port may be "port/count"
                m.port = int(parts[1].split("/")[0])
                m.proto = parts[2]
                m.formats = parts[3:]
                current_media = m
                sdp.media.append(m)
            elif type_char == "a" and current_media is not None:
                if value.startswith("rtpmap:"):
                    mat = re.match(r"rtpmap:(\d+)\s+(\S+)/(\d+)", value)
                    if mat:
                        pt = int(mat.group(1))
                        current_media.rtpmap[pt] = (mat.group(2), int(mat.group(3)))
                elif value.startswith("ptime:"):
                    try:
                        current_media.ptime = int(value.split(":")[1])
                    except (ValueError, IndexError):
                        pass
                elif value in ("sendrecv", "sendonly", "recvonly", "inactive"):
                    current_media.direction = value

        return sdp

    # ------------------------------------------------------------------
    # Answer generation
    # ------------------------------------------------------------------

    def build_answer(self, local_ip: str, local_port: int,
                     codec_pt: int, codec_name: str) -> str:
        ts = int(time.time())
        lines = [
            "v=0",
            f"o=- {ts} {ts} IN IP4 {local_ip}",
            "s=SIP Gateway",
            f"c=IN IP4 {local_ip}",
            "t=0 0",
            f"m=audio {local_port} RTP/AVP {codec_pt}",
            f"a=rtpmap:{codec_pt} {codec_name}/8000",
            "a=ptime:20",
            "a=sendrecv",
        ]
        return "\r\n".join(lines) + "\r\n"


def _parse_connection(value: str) -> Optional[Tuple[str, str, str]]:
    parts = value.split()
    if len(parts) >= 3:
        return (parts[0], parts[1], parts[2])
    return None
