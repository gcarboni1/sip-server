"""Unit tests for G.711 codec."""

import struct
import pytest

from src.codec.g711 import (
    ulaw_decode, ulaw_encode,
    alaw_decode, alaw_encode,
    decode, encode,
)


def _pcm16_bytes(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def _pcm16_samples(data):
    n = len(data) // 2
    return list(struct.unpack(f"<{n}h", data))


class TestUlaw:
    def test_decode_length(self):
        payload = bytes(160)
        pcm = ulaw_decode(payload)
        assert len(pcm) == 320

    def test_encode_length(self):
        pcm = _pcm16_bytes([0] * 160)
        payload = ulaw_encode(pcm)
        assert len(payload) == 160

    def test_silence_roundtrip(self):
        silence_pcm = _pcm16_bytes([0] * 160)
        encoded = ulaw_encode(silence_pcm)
        decoded = ulaw_decode(encoded)
        samples = _pcm16_samples(decoded)
        # μ-law silence should be close to zero (quantisation bias of ~33)
        assert all(abs(s) < 100 for s in samples)

    def test_roundtrip_quality(self):
        """Encode then decode should stay within G.711 quantisation error."""
        import math
        # 1 kHz sine wave
        samples = [int(32000 * math.sin(2 * math.pi * i / 8)) for i in range(160)]
        pcm = _pcm16_bytes(samples)
        encoded = ulaw_encode(pcm)
        decoded_samples = _pcm16_samples(ulaw_decode(encoded))
        # Allow up to 2% RMS error for G.711
        for orig, rec in zip(samples, decoded_samples):
            assert abs(orig - rec) < 1500, f"Too much error: {orig} → {rec}"

    def test_full_scale(self):
        pcm = _pcm16_bytes([32767, -32768])
        encoded = ulaw_encode(pcm)
        assert len(encoded) == 2


class TestAlaw:
    def test_decode_length(self):
        assert len(alaw_decode(bytes(160))) == 320

    def test_encode_length(self):
        assert len(alaw_encode(_pcm16_bytes([0] * 160))) == 160

    def test_roundtrip_quality(self):
        import math
        samples = [int(30000 * math.sin(2 * math.pi * i / 8)) for i in range(160)]
        pcm = _pcm16_bytes(samples)
        decoded = _pcm16_samples(alaw_decode(alaw_encode(pcm)))
        for orig, rec in zip(samples, decoded):
            assert abs(orig - rec) < 2000


class TestUnifiedInterface:
    def test_pt0_pcmu(self):
        pcm = _pcm16_bytes([1000] * 10)
        assert encode(0, decode(0, ulaw_encode(pcm))) is not None

    def test_pt8_pcma(self):
        pcm = _pcm16_bytes([1000] * 10)
        assert encode(8, decode(8, alaw_encode(pcm))) is not None

    def test_unknown_pt_raises(self):
        with pytest.raises(ValueError):
            decode(18, b"\x00")
        with pytest.raises(ValueError):
            encode(18, b"\x00")
