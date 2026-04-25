"""SIP message parsing and response building (RFC 3261)."""

from __future__ import annotations
import random
from typing import Dict, List, Optional


class SIPMessage:
    """Represents a parsed SIP request or response."""

    def __init__(self) -> None:
        self.is_request: bool = False
        self.method: Optional[str] = None
        self.request_uri: Optional[str] = None
        self.status_code: Optional[int] = None
        self.reason_phrase: Optional[str] = None
        # headers: lowercase name -> list of raw values
        self.headers: Dict[str, List[str]] = {}
        self.body: bytes = b""
        self.raw: bytes = b""

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, data: bytes) -> "SIPMessage":
        msg = cls()
        msg.raw = data

        if b"\r\n\r\n" in data:
            header_part, msg.body = data.split(b"\r\n\r\n", 1)
        else:
            header_part = data

        text = header_part.decode("utf-8", errors="replace")
        lines = text.split("\r\n")

        if not lines:
            return msg

        first = lines[0]
        if first.startswith("SIP/2.0"):
            msg.is_request = False
            parts = first.split(" ", 2)
            msg.status_code = int(parts[1]) if len(parts) > 1 else 0
            msg.reason_phrase = parts[2] if len(parts) > 2 else ""
        else:
            msg.is_request = True
            parts = first.split(" ", 2)
            msg.method = parts[0] if parts else ""
            msg.request_uri = parts[1] if len(parts) > 1 else ""

        # Parse headers (handle line folding)
        i = 1
        while i < len(lines):
            line = lines[i]
            i += 1
            if not line:
                continue
            while i < len(lines) and lines[i] and lines[i][0] in (" ", "\t"):
                line += " " + lines[i].strip()
                i += 1
            if ":" not in line:
                continue
            name, _, value = line.partition(":")
            name = _normalise_header(name.strip())
            value = value.strip()
            msg.headers.setdefault(name, []).append(value)

        return msg

    # ------------------------------------------------------------------
    # Header access helpers
    # ------------------------------------------------------------------

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        vals = self.headers.get(_normalise_header(name), [])
        return vals[0] if vals else default

    def get_all(self, name: str) -> List[str]:
        return self.headers.get(_normalise_header(name), [])

    @property
    def call_id(self) -> Optional[str]:
        return self.get("call-id") or self.get("i")

    @property
    def from_header(self) -> Optional[str]:
        return self.get("from") or self.get("f")

    @property
    def to_header(self) -> Optional[str]:
        return self.get("to") or self.get("t")

    @property
    def via_headers(self) -> List[str]:
        # _normalise_header maps compact "v" → "via" during parsing,
        # so all Via values land in the same key; no need to union.
        return self.get_all("via")

    @property
    def cseq(self) -> Optional[str]:
        return self.get("cseq")

    @property
    def contact(self) -> Optional[str]:
        return self.get("contact") or self.get("m")

    @property
    def content_type(self) -> Optional[str]:
        return self.get("content-type") or self.get("c")

    @property
    def max_forwards(self) -> int:
        v = self.get("max-forwards")
        try:
            return int(v) if v else 70
        except ValueError:
            return 70

    def __repr__(self) -> str:
        if self.is_request:
            return f"<SIPRequest {self.method} {self.call_id}>"
        return f"<SIPResponse {self.status_code} {self.call_id}>"


# ------------------------------------------------------------------
# Compact header name normalisation (RFC 3261 §20)
# ------------------------------------------------------------------

_COMPACT = {
    "i": "call-id",
    "m": "contact",
    "e": "content-encoding",
    "l": "content-length",
    "c": "content-type",
    "f": "from",
    "s": "subject",
    "k": "supported",
    "t": "to",
    "v": "via",
}


def _normalise_header(name: str) -> str:
    lower = name.lower()
    return _COMPACT.get(lower, lower)


# ------------------------------------------------------------------
# Response builder
# ------------------------------------------------------------------

def build_response(
    request: SIPMessage,
    status_code: int,
    reason: str,
    extra_headers: Optional[Dict[str, str]] = None,
    body: bytes = b"",
    local_tag: Optional[str] = None,
) -> bytes:
    """Build a SIP response bytes object from an incoming request."""
    lines: List[str] = [f"SIP/2.0 {status_code} {reason}"]

    # RFC 3261 §8.2.6: copy Via headers in order
    for via in request.via_headers:
        lines.append(f"Via: {via}")

    # From (verbatim)
    if request.from_header:
        lines.append(f"From: {request.from_header}")

    # To: add tag for non-100 responses
    to = request.to_header or ""
    if status_code != 100 and "tag=" not in to:
        tag = local_tag or str(random.randint(100000, 999999))
        to = f"{to};tag={tag}"
    lines.append(f"To: {to}")

    if request.call_id:
        lines.append(f"Call-ID: {request.call_id}")
    if request.cseq:
        lines.append(f"CSeq: {request.cseq}")

    # Allow header for capabilities advertisement
    if status_code in (200, 405, 501):
        lines.append("Allow: INVITE, ACK, BYE, CANCEL, OPTIONS")

    if extra_headers:
        for k, v in extra_headers.items():
            lines.append(f"{k}: {v}")

    if body:
        lines.append("Content-Type: application/sdp")
        lines.append(f"Content-Length: {len(body)}")
    else:
        lines.append("Content-Length: 0")

    lines.append("")  # blank line
    header_bytes = "\r\n".join(lines).encode() + b"\r\n"
    return header_bytes + body
