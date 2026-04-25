"""Unit tests for SIP message parsing and response building."""

import pytest
from src.sip.message import SIPMessage, build_response
from src.sip.sdp import SDPSession


INVITE_RAW = (
    b"INVITE sip:+390291234567@203.0.113.5:5060 SIP/2.0\r\n"
    b"Via: SIP/2.0/UDP 185.98.36.10:5060;branch=z9hG4bK77ef4c2312b46\r\n"
    b"Max-Forwards: 70\r\n"
    b"From: <sip:+39029000001@didww.com>;tag=20439182\r\n"
    b"To: <sip:+390291234567@203.0.113.5>\r\n"
    b"Call-ID: f81d4fae-7dec-11d0-a765-00a0c91e6bf6@foo.bar\r\n"
    b"CSeq: 1 INVITE\r\n"
    b"Contact: <sip:+39029000001@185.98.36.10:5060>\r\n"
    b"Content-Type: application/sdp\r\n"
    b"Content-Length: 146\r\n"
    b"\r\n"
    b"v=0\r\n"
    b"o=- 12345 12345 IN IP4 185.98.36.10\r\n"
    b"s=DIDWW\r\n"
    b"c=IN IP4 185.98.36.10\r\n"
    b"t=0 0\r\n"
    b"m=audio 16000 RTP/AVP 0 8\r\n"
    b"a=rtpmap:0 PCMU/8000\r\n"
    b"a=rtpmap:8 PCMA/8000\r\n"
    b"a=ptime:20\r\n"
)


class TestSIPParsing:
    def test_method(self):
        msg = SIPMessage.parse(INVITE_RAW)
        assert msg.is_request
        assert msg.method == "INVITE"

    def test_call_id(self):
        msg = SIPMessage.parse(INVITE_RAW)
        assert msg.call_id == "f81d4fae-7dec-11d0-a765-00a0c91e6bf6@foo.bar"

    def test_via(self):
        msg = SIPMessage.parse(INVITE_RAW)
        assert len(msg.via_headers) == 1
        assert "z9hG4bK77ef4c2312b46" in msg.via_headers[0]

    def test_from(self):
        msg = SIPMessage.parse(INVITE_RAW)
        assert "+39029000001" in msg.from_header

    def test_content_type(self):
        msg = SIPMessage.parse(INVITE_RAW)
        assert "application/sdp" in msg.content_type

    def test_body_preserved(self):
        msg = SIPMessage.parse(INVITE_RAW)
        assert b"m=audio" in msg.body

    def test_compact_header_i(self):
        raw = (
            b"OPTIONS sip:server SIP/2.0\r\n"
            b"i: test-call-id-123\r\n"
            b"t: <sip:to>\r\n"
            b"f: <sip:from>\r\n"
            b"v: SIP/2.0/UDP 1.2.3.4:5060;branch=abc\r\n"
            b"CSeq: 1 OPTIONS\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n"
        )
        msg = SIPMessage.parse(raw)
        assert msg.call_id == "test-call-id-123"


class TestResponseBuilding:
    def setup_method(self):
        self.invite = SIPMessage.parse(INVITE_RAW)

    def test_100_trying_no_tag(self):
        resp = build_response(self.invite, 100, "Trying").decode()
        assert "SIP/2.0 100 Trying" in resp
        # To header must NOT have a tag for 100
        to_line = next(l for l in resp.splitlines() if l.startswith("To:"))
        assert "tag=" not in to_line

    def test_200_ok_has_tag(self):
        resp = build_response(self.invite, 200, "OK", local_tag="abc123").decode()
        to_line = next(l for l in resp.splitlines() if l.startswith("To:"))
        assert "tag=abc123" in to_line

    def test_via_copied(self):
        resp = build_response(self.invite, 200, "OK").decode()
        assert "z9hG4bK77ef4c2312b46" in resp

    def test_call_id_copied(self):
        resp = build_response(self.invite, 200, "OK").decode()
        assert "f81d4fae-7dec-11d0-a765-00a0c91e6bf6" in resp

    def test_body_attached(self):
        body = b"v=0\r\nc=IN IP4 1.2.3.4\r\n"
        resp = build_response(self.invite, 200, "OK", body=body)
        assert body in resp
        assert b"Content-Type: application/sdp" in resp


class TestSDPParsing:
    def test_parse_rtp_ip(self):
        sdp = SDPSession.parse(INVITE_RAW.split(b"\r\n\r\n", 1)[1].decode())
        assert sdp.rtp_ip == "185.98.36.10"

    def test_parse_rtp_port(self):
        sdp = SDPSession.parse(INVITE_RAW.split(b"\r\n\r\n", 1)[1].decode())
        assert sdp.rtp_port == 16000

    def test_preferred_codec_pcmu(self):
        sdp = SDPSession.parse(INVITE_RAW.split(b"\r\n\r\n", 1)[1].decode())
        pt, name = sdp.preferred_codec
        assert pt == 0
        assert name == "PCMU"

    def test_build_answer(self):
        sdp = SDPSession.parse(INVITE_RAW.split(b"\r\n\r\n", 1)[1].decode())
        answer = sdp.build_answer("203.0.113.5", 12000, 0, "PCMU")
        assert "m=audio 12000 RTP/AVP 0" in answer
        assert "c=IN IP4 203.0.113.5" in answer
        assert "a=rtpmap:0 PCMU/8000" in answer
