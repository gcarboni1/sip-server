"""G.711 μ-law (PCMU) and A-law (PCMA) codec.

Reference implementation following ITU-T G.711 / Sun Microsystems source.
All public functions accept/return bytes of 8-bit encoded or 16-bit LE PCM samples.
"""

import struct

# ---------------------------------------------------------------------------
# μ-law segment exponent lookup table (index = top 8 bits after bias)
# ---------------------------------------------------------------------------
_ULAW_EXP_LUT = [
    0,0,1,1,2,2,2,2,3,3,3,3,3,3,3,3,
    4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,4,
    5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
    5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
    6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
    6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
    6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
    6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
    7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,7,
]

# A-law segment upper boundaries for 16-bit linear PCM
_ALAW_SEG_END = [0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF, 0x3FFF, 0x7FFF]


# ---------------------------------------------------------------------------
# Decode lookup tables (built once at import time)
# ---------------------------------------------------------------------------

def _build_ulaw_decode_table():
    """Decode table: encoded byte → PCM16 sample.

    Formula (Sun/ITU-T):
        u  = ~encoded & 0xFF
        t  = ((u & 0x0F) << 3) + BIAS
        t <<= (u & 0x70) >> 4
        return BIAS - t  if u & 0x80 else t - BIAS
    """
    BIAS = 0x84
    table = []
    for i in range(256):
        u = ~i & 0xFF
        mantissa = u & 0x0F
        exp = (u & 0x70) >> 4
        sign = u & 0x80
        t = ((mantissa << 3) + BIAS) << exp
        sample = (BIAS - t) if sign else (t - BIAS)
        table.append(max(-32768, min(32767, sample)))
    return table


def _build_alaw_decode_table():
    """Decode table: encoded byte → PCM16 sample.

    Formula (Sun/ITU-T):
        a  = encoded ^ 0x55
        t  = (a & 0x0F) << 4
        seg = (a & 0x70) >> 4
        if seg == 0: t += 8
        elif seg == 1: t += 0x108
        else: t += 0x108; t <<= seg-1
        return t if a & 0x80 else -t
    """
    table = []
    for i in range(256):
        a = i ^ 0x55
        mantissa = a & 0x0F
        seg = (a & 0x70) >> 4
        sign = a & 0x80
        t = mantissa << 4
        if seg == 0:
            t += 8
        elif seg == 1:
            t += 0x108
        else:
            t += 0x108
            t <<= (seg - 1)
        sample = t if sign else -t
        table.append(max(-32768, min(32767, sample)))
    return table


_ULAW_DEC = _build_ulaw_decode_table()
_ALAW_DEC = _build_alaw_decode_table()


# ---------------------------------------------------------------------------
# Decode: compressed bytes → PCM16-LE bytes
# ---------------------------------------------------------------------------

def ulaw_decode(data: bytes) -> bytes:
    """Decode μ-law (PCMU) payload bytes to PCM16 little-endian bytes."""
    samples = [_ULAW_DEC[b] for b in data]
    return struct.pack(f'<{len(samples)}h', *samples)


def alaw_decode(data: bytes) -> bytes:
    """Decode A-law (PCMA) payload bytes to PCM16 little-endian bytes."""
    samples = [_ALAW_DEC[b] for b in data]
    return struct.pack(f'<{len(samples)}h', *samples)


# ---------------------------------------------------------------------------
# Encode: PCM16-LE bytes → compressed bytes
# ---------------------------------------------------------------------------

def _ulaw_encode_sample(sample: int) -> int:
    """Encode a 16-bit signed PCM sample to μ-law (Sun/ITU-T reference)."""
    BIAS = 0x84   # 132
    CLIP = 32635
    # sign: 0x80 when negative, 0 when positive (matches decode convention)
    sign = (sample >> 8) & 0x80
    if sign:
        sample = -sample
    if sample > CLIP:
        sample = CLIP
    sample += BIAS
    exp = _ULAW_EXP_LUT[(sample >> 7) & 0xFF]
    mantissa = (sample >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | mantissa)) & 0xFF


def _alaw_encode_sample(sample: int) -> int:
    """Encode a 16-bit signed PCM sample to A-law (Sun/ITU-T reference)."""
    CLIP = 32635
    # mask: 0xD5 (positive) or 0x55 (negative) — applied via XOR at end
    if sample >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        sample = -sample - 1
    if sample > CLIP:
        sample = CLIP
    # find segment (0-7)
    seg = 8
    for i, bound in enumerate(_ALAW_SEG_END):
        if sample <= bound:
            seg = i
            break
    if seg >= 8:
        aval = 0x7F
    elif seg < 2:
        aval = (seg << 4) | ((sample >> 4) & 0x0F)
    else:
        aval = (seg << 4) | ((sample >> (seg + 3)) & 0x0F)
    return aval ^ mask


def ulaw_encode(data: bytes) -> bytes:
    """Encode PCM16 little-endian bytes to μ-law (PCMU) bytes."""
    n = len(data) // 2
    samples = struct.unpack(f'<{n}h', data[:n * 2])
    return bytes([_ulaw_encode_sample(s) for s in samples])


def alaw_encode(data: bytes) -> bytes:
    """Encode PCM16 little-endian bytes to A-law (PCMA) bytes."""
    n = len(data) // 2
    samples = struct.unpack(f'<{n}h', data[:n * 2])
    return bytes([_alaw_encode_sample(s) for s in samples])


# ---------------------------------------------------------------------------
# Unified interface used by the call layer
# ---------------------------------------------------------------------------

def decode(payload_type: int, data: bytes) -> bytes:
    """Decode RTP payload to PCM16-LE.  payload_type: 0=PCMU, 8=PCMA."""
    if payload_type == 0:
        return ulaw_decode(data)
    if payload_type == 8:
        return alaw_decode(data)
    raise ValueError(f"Unsupported payload type {payload_type}")


def encode(payload_type: int, data: bytes) -> bytes:
    """Encode PCM16-LE to RTP payload.  payload_type: 0=PCMU, 8=PCMA."""
    if payload_type == 0:
        return ulaw_encode(data)
    if payload_type == 8:
        return alaw_encode(data)
    raise ValueError(f"Unsupported payload type {payload_type}")
