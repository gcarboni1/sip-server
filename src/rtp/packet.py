"""RTP packet parsing and serialisation (RFC 3550)."""

from __future__ import annotations
import struct
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RTPPacket:
    version: int = 2
    padding: bool = False
    extension: bool = False
    cc: int = 0
    marker: bool = False
    payload_type: int = 0
    sequence: int = 0
    timestamp: int = 0
    ssrc: int = 0
    payload: bytes = field(default_factory=bytes)

    HEADER_SIZE: int = field(default=12, init=False, repr=False, compare=False)

    @classmethod
    def parse(cls, data: bytes) -> Optional["RTPPacket"]:
        if len(data) < 12:
            return None

        b0, b1 = data[0], data[1]
        version = (b0 >> 6) & 0x03
        if version != 2:
            return None

        padding = bool(b0 & 0x20)
        extension = bool(b0 & 0x10)
        cc = b0 & 0x0F
        marker = bool(b1 & 0x80)
        payload_type = b1 & 0x7F

        sequence, timestamp, ssrc = struct.unpack("!HII", data[2:12])

        offset = 12 + cc * 4  # skip CSRC list

        if extension and len(data) >= offset + 4:
            ext_len = struct.unpack("!H", data[offset + 2: offset + 4])[0]
            offset += 4 + ext_len * 4

        payload = data[offset:]

        if padding and payload:
            pad_len = payload[-1]
            if pad_len > 0:
                payload = payload[:-pad_len]

        return cls(
            version=version,
            padding=padding,
            extension=extension,
            cc=cc,
            marker=marker,
            payload_type=payload_type,
            sequence=sequence,
            timestamp=timestamp,
            ssrc=ssrc,
            payload=payload,
        )

    def build(self) -> bytes:
        b0 = (self.version << 6) | (int(self.padding) << 5) | (int(self.extension) << 4) | self.cc
        b1 = (int(self.marker) << 7) | self.payload_type
        return struct.pack("!BBHII", b0, b1, self.sequence, self.timestamp, self.ssrc) + self.payload
