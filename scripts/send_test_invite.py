#!/usr/bin/env python3
"""
Minimal SIP INVITE test client.

Sends a SIP INVITE to the gateway, waits for 200 OK, sends ACK,
waits `--duration` seconds while receiving RTP, then sends BYE.

Usage:
    python scripts/send_test_invite.py --gw-ip 127.0.0.1 --gw-port 5060
    python scripts/send_test_invite.py --gw-ip 1.2.3.4 --duration 10
"""

import argparse
import random
import socket
import struct
import threading
import time


def parse_args():
    p = argparse.ArgumentParser(description="SIP INVITE test client")
    p.add_argument("--gw-ip",    default="127.0.0.1")
    p.add_argument("--gw-port",  type=int, default=5060)
    p.add_argument("--local-ip", default="127.0.0.1")
    p.add_argument("--local-sip-port", type=int, default=5090)
    p.add_argument("--local-rtp-port", type=int, default=19000)
    p.add_argument("--caller",   default="+39029000001")
    p.add_argument("--callee",   default="+390291234567")
    p.add_argument("--duration", type=float, default=5.0,
                   help="Call duration in seconds after 200 OK")
    p.add_argument("--send-rtp", action="store_true",
                   help="Send dummy PCMU RTP packets during the call")
    return p.parse_args()


def make_branch():
    return f"z9hG4bK{random.randint(10**10, 10**11)}"


def make_call_id():
    return f"{random.randint(10**15, 10**16)}@test.client"


def make_tag():
    return str(random.randint(100000, 999999))


def send_invite(sock, gw, local_ip, local_sip_port, local_rtp_port,
                caller, callee, call_id, branch, from_tag):
    sdp = (
        "v=0\r\n"
        f"o=- 12345 12345 IN IP4 {local_ip}\r\n"
        "s=TestClient\r\n"
        f"c=IN IP4 {local_ip}\r\n"
        "t=0 0\r\n"
        f"m=audio {local_rtp_port} RTP/AVP 0 8\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:8 PCMA/8000\r\n"
        "a=ptime:20\r\n"
    ).encode()

    msg = (
        f"INVITE sip:{callee}@{gw[0]}:{gw[1]} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP {local_ip}:{local_sip_port};branch={branch};rport\r\n"
        "Max-Forwards: 70\r\n"
        f"From: <sip:{caller}@{local_ip}>;tag={from_tag}\r\n"
        f"To: <sip:{callee}@{gw[0]}>\r\n"
        f"Call-ID: {call_id}\r\n"
        "CSeq: 1 INVITE\r\n"
        f"Contact: <sip:{caller}@{local_ip}:{local_sip_port}>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(sdp)}\r\n"
        "\r\n"
    ).encode() + sdp

    sock.sendto(msg, gw)
    print(f"→ INVITE  call-id={call_id}")


def send_ack(sock, gw, local_ip, local_sip_port, callee, call_id,
             branch, from_tag, to_header, contact):
    req_uri = contact.strip("<>") if contact else f"sip:{callee}@{gw[0]}"
    # strip angle brackets
    if req_uri.startswith("<"):
        req_uri = req_uri[1:]
    if req_uri.endswith(">"):
        req_uri = req_uri[:-1]

    msg = (
        f"ACK {req_uri} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP {local_ip}:{local_sip_port};branch={make_branch()}\r\n"
        "Max-Forwards: 70\r\n"
        f"From: <sip:caller@{local_ip}>;tag={from_tag}\r\n"
        f"To: {to_header}\r\n"
        f"Call-ID: {call_id}\r\n"
        "CSeq: 1 ACK\r\n"
        "Content-Length: 0\r\n"
        "\r\n"
    ).encode()
    sock.sendto(msg, gw)
    print("→ ACK")


def send_bye(sock, gw, local_ip, local_sip_port, call_id,
             from_tag, to_header):
    msg = (
        f"BYE sip:gateway@{gw[0]}:{gw[1]} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP {local_ip}:{local_sip_port};branch={make_branch()}\r\n"
        "Max-Forwards: 70\r\n"
        f"From: <sip:caller@{local_ip}>;tag={from_tag}\r\n"
        f"To: {to_header}\r\n"
        f"Call-ID: {call_id}\r\n"
        "CSeq: 2 BYE\r\n"
        "Content-Length: 0\r\n"
        "\r\n"
    ).encode()
    sock.sendto(msg, gw)
    print("→ BYE")


def rtp_sender(rtp_sock, remote_addr, duration):
    """Send dummy PCMU silence RTP packets every 20 ms."""
    ssrc = random.randint(0, 0xFFFFFFFF)
    seq = random.randint(0, 0xFFFF)
    ts = random.randint(0, 0xFFFFFFFF)
    payload = bytes(160)  # PCMU silence
    end_time = time.time() + duration
    while time.time() < end_time:
        b0 = 0x80  # version=2
        b1 = 0     # PT=0 PCMU
        header = struct.pack("!BBHII", b0, b1, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc)
        rtp_sock.sendto(header + payload, remote_addr)
        seq += 1
        ts += 160
        time.sleep(0.020)


def main():
    args = parse_args()
    gw = (args.gw_ip, args.gw_port)

    sip_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sip_sock.bind((args.local_ip, args.local_sip_port))
    sip_sock.settimeout(10.0)

    rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rtp_sock.bind((args.local_ip, args.local_rtp_port))
    rtp_sock.settimeout(0.5)

    call_id  = make_call_id()
    branch   = make_branch()
    from_tag = make_tag()

    send_invite(sip_sock, gw, args.local_ip, args.local_sip_port,
                args.local_rtp_port, args.caller, args.callee,
                call_id, branch, from_tag)

    to_header = None
    contact = None
    gw_rtp_port = None

    # Wait for 200 OK (skip provisional responses)
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            data, _ = sip_sock.recvfrom(4096)
            text = data.decode("utf-8", errors="replace")
            first = text.split("\r\n")[0]
            print(f"← {first}")
            if "200 OK" in first:
                for line in text.splitlines():
                    if line.lower().startswith("to:"):
                        to_header = line[3:].strip()
                    elif line.lower().startswith("contact:"):
                        contact = line[8:].strip()
                    elif line.lower().startswith("m=audio"):
                        parts = line.split()
                        if len(parts) >= 2:
                            gw_rtp_port = int(parts[1])
                break
        except socket.timeout:
            break

    if to_header is None:
        print("ERROR: did not receive 200 OK")
        return

    send_ack(sip_sock, gw, args.local_ip, args.local_sip_port,
             args.callee, call_id, branch, from_tag, to_header, contact)

    if args.send_rtp and gw_rtp_port:
        print(f"  RTP → {args.gw_ip}:{gw_rtp_port}  for {args.duration}s")
        t = threading.Thread(
            target=rtp_sender,
            args=(rtp_sock, (args.gw_ip, gw_rtp_port), args.duration),
            daemon=True,
        )
        t.start()

    print(f"  Call up – waiting {args.duration}s")
    time.sleep(args.duration)

    send_bye(sip_sock, gw, args.local_ip, args.local_sip_port,
             call_id, from_tag, to_header)

    # Wait for 200 OK to BYE
    try:
        data, _ = sip_sock.recvfrom(4096)
        print(f"← {data.decode(errors='replace').splitlines()[0]}")
    except socket.timeout:
        pass

    sip_sock.close()
    rtp_sock.close()
    print("Done.")


if __name__ == "__main__":
    main()
