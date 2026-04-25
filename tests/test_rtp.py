"""Unit tests for RTP packet and jitter buffer."""

import struct
import pytest

from src.rtp.packet import RTPPacket
from src.rtp.jitter_buffer import JitterBuffer


class TestRTPPacket:
    def _make_packet(self, seq=1, ts=160, ssrc=0xDEADBEEF, pt=0, payload=b"\x7f" * 160):
        b0 = (2 << 6)  # version=2
        b1 = pt & 0x7F
        header = struct.pack("!BBHII", b0, b1, seq, ts, ssrc)
        return header + payload

    def test_parse_basic(self):
        raw = self._make_packet(seq=42, ts=1000, pt=0)
        pkt = RTPPacket.parse(raw)
        assert pkt is not None
        assert pkt.version == 2
        assert pkt.sequence == 42
        assert pkt.timestamp == 1000
        assert pkt.payload_type == 0
        assert len(pkt.payload) == 160

    def test_parse_too_short(self):
        assert RTPPacket.parse(b"\x80\x00\x00") is None

    def test_wrong_version(self):
        raw = self._make_packet()
        # corrupt version bits
        bad = bytes([raw[0] & 0x3F]) + raw[1:]
        assert RTPPacket.parse(bad) is None

    def test_build_roundtrip(self):
        pkt = RTPPacket(
            payload_type=0,
            sequence=1234,
            timestamp=56789,
            ssrc=0xCAFEBABE,
            payload=b"\xAA" * 80,
        )
        rebuilt = RTPPacket.parse(pkt.build())
        assert rebuilt.sequence == 1234
        assert rebuilt.timestamp == 56789
        assert rebuilt.ssrc == 0xCAFEBABE
        assert rebuilt.payload == b"\xAA" * 80

    def test_marker_bit(self):
        pkt = RTPPacket(marker=True, payload_type=0, payload=b"\x00")
        raw = pkt.build()
        parsed = RTPPacket.parse(raw)
        assert parsed.marker is True


class TestJitterBuffer:
    def test_initial_fill(self):
        jb = JitterBuffer(depth=3)
        jb.push(1, b"a")
        jb.push(2, b"b")
        # Not yet filled to depth=3
        assert jb.pop() is None

    def test_pop_after_fill(self):
        jb = JitterBuffer(depth=3)
        jb.push(0, b"A")
        jb.push(1, b"B")
        jb.push(2, b"C")
        assert jb.pop() == b"A"
        assert jb.pop() == b"B"

    def test_out_of_order(self):
        jb = JitterBuffer(depth=3)
        jb.push(0, b"A")
        jb.push(2, b"C")
        jb.push(1, b"B")
        assert jb.pop() == b"A"
        assert jb.pop() == b"B"
        assert jb.pop() == b"C"

    def test_packet_loss_returns_silence(self):
        jb = JitterBuffer(depth=3, silence_on_loss=True)
        jb.push(0, b"A")
        jb.push(1, b"B")
        jb.push(3, b"D")  # seq 2 missing
        assert jb.pop() == b"A"
        assert jb.pop() == b"B"
        silence = jb.pop()
        assert silence is not None
        assert all(b == 0 for b in silence)  # silence frame

    def test_sequence_wrap(self):
        jb = JitterBuffer(depth=2)
        jb.push(0xFFFE, b"X")
        jb.push(0xFFFF, b"Y")
        assert jb.pop() == b"X"
        assert jb.pop() == b"Y"
        # next expected wraps to 0
        jb.push(0, b"Z")
        assert jb.pop() == b"Z"

    def test_reset(self):
        jb = JitterBuffer(depth=2)
        jb.push(0, b"A")
        jb.push(1, b"B")
        jb.pop()
        jb.reset()
        assert jb.pending == 0
        assert jb.pop() is None
