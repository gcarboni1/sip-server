# SIP Media Gateway

A minimal, production-oriented SIP-to-RTP media gateway written in pure Python.  
Receives inbound SIP calls from a provider (DIDWW or any SIP trunk), decodes G.711 audio, streams PCM to an AI backend over WebSocket, and plays back the AI's audio response to the caller.

---

## Table of contents

1. [Architecture](#architecture)
2. [Features](#features)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Running the gateway](#running-the-gateway)
7. [AI backend integration](#ai-backend-integration)
8. [Testing](#testing)
9. [DIDWW setup](#didww-setup)
10. [Project structure](#project-structure)
11. [Component reference](#component-reference)
12. [Performance targets](#performance-targets)
13. [Security](#security)
14. [Troubleshooting](#troubleshooting)

---

## Architecture

```
[DIDWW SIP Trunk]
      │  SIP INVITE (UDP 5060)
      ▼
┌─────────────────────────────────────────────────────┐
│                  SIP Server (UDP 5060)               │
│          IP whitelist · INVITE/ACK/BYE/CANCEL        │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│                   Call Manager                       │
│          State machine per ogni chiamata             │
│   IDLE → INVITE → SDP → RTP_ACTIVE → AI → DONE      │
└──────────────────────┬──────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
┌──────────────────┐     ┌─────────────────────────┐
│   RTP Session    │     │     Audio Bridge         │
│  UDP 10000-20000 │     │  WebSocket ws://...:8765 │
│  Jitter buffer   │     │  PCM16-LE 20ms frames    │
│  G.711 codec     │     │                          │
└────────┬─────────┘     └───────────┬─────────────┘
         │ PCM decode                │ PCM in/out
         └─────────────┬─────────────┘
                       ▼
           ┌───────────────────────┐
           │     AI Backend        │
           │  (OpenAI Realtime,    │
           │   custom LLM, etc.)   │
           └───────────────────────┘
```

---

## Features

| Capability | Detail |
|---|---|
| SIP methods | INVITE, ACK, BYE, CANCEL, OPTIONS |
| SDP negotiation | Offer/answer, G.711 codec selection |
| Audio codecs | G.711 μ-law (PCMU pt=0) · G.711 A-law (PCMA pt=8) |
| RTP | Receive + send · jitter buffer · packet-loss concealment |
| AI bridge | WebSocket server · binary PCM16-LE frames · 20 ms chunks |
| Echo mode | Loopback RTP for end-to-end audio testing (no AI needed) |
| PCM dump | Save inbound audio to `.pcm` files for debugging |
| IP whitelist | Drop all SIP from unknown sources |
| Concurrency | Up to 50 simultaneous calls (configurable) |
| Transport | UDP SIP + UDP RTP (TCP SIP planned) |

---

## Requirements

- Python 3.8+
- A server with a **public static IP**
- Open firewall ports:
  - `UDP 5060` — SIP signalling
  - `UDP 10000–20000` — RTP media

Python packages (minimal):

```
websockets >= 11.0
pyyaml     >= 6.0
aiohttp    >= 3.9.0   # optional, for webhook notifications
```

---

## Installation

```bash
git clone <repo-url> sip-server
cd sip-server

# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

---

## Configuration

Edit `config/config.yaml` before the first run.

```yaml
sip:
  host: "0.0.0.0"       # listen on all interfaces
  port: 5060
  public_ip: "AUTO"     # AUTO = detect from routing table; or set "1.2.3.4"
  allowed_ips:          # empty list = allow all (dev only!)
    - "185.98.36.0/22"  # DIDWW Frankfurt
    - "185.98.40.0/22"  # DIDWW Amsterdam
    - "185.98.44.0/22"  # DIDWW US

rtp:
  port_min: 10000
  port_max: 20000
  jitter_buffer_packets: 3   # 3 × 20 ms = 60 ms depth

bridge:
  host: "0.0.0.0"
  port: 8765
  ai_webhook_url: ""         # POST here when a new call arrives (optional)
  frame_ms: 20               # PCM chunk size in milliseconds

audio:
  sample_rate: 8000
  channels: 1
  bit_depth: 16

performance:
  max_concurrent_calls: 50

logging:
  level: "INFO"             # DEBUG | INFO | WARNING | ERROR
  file: "logs/gateway.log"  # remove key to log to stdout only
```

### Key settings

| Setting | Notes |
|---|---|
| `sip.public_ip` | Must be the IP reachable by DIDWW. `AUTO` probes `8.8.8.8`. |
| `sip.allowed_ips` | CIDR or single IPs. Empty list disables whitelist (unsafe for production). |
| `rtp.jitter_buffer_packets` | 3 packets = 60 ms. Increase to 5–6 on high-jitter links. |
| `bridge.ai_webhook_url` | If set, gateway POSTs a JSON event to this URL when a call starts. |

---

## Running the gateway

### Normal mode (with AI backend)

```bash
python gateway.py
```

### Echo mode — loopback audio test, no AI required

```bash
python gateway.py --echo
```

Inbound audio is immediately re-encoded and sent back to the caller. Ideal for verifying SIP signalling and RTP end-to-end before connecting an AI.

### PCM dump mode — save inbound audio to disk

```bash
python gateway.py --echo --dump-pcm /tmp/audio/
```

Creates one `call_<id>_<timestamp>.pcm` file per call. Play back with:

```bash
# Using ffplay
ffplay -f s16le -ar 8000 -ac 1 /tmp/audio/call_abc123_1714000000.pcm

# Convert to WAV with ffmpeg
ffmpeg -f s16le -ar 8000 -ac 1 -i call_abc123.pcm output.wav
```

### All CLI options

```
python gateway.py [options]

  --config PATH       Config file (default: config/config.yaml)
  --echo              Echo mode: loop inbound audio back to caller
  --dump-pcm DIR      Save inbound PCM to DIR/<call_id>.pcm
  --public-ip IP      Override detected public IP
  --debug             Set log level to DEBUG
```

---

## AI backend integration

The gateway exposes a **WebSocket server** on `ws://<host>:8765`.  
The AI backend acts as a WebSocket **client** and handles one connection per active call.

### Protocol

```
1. AI backend connects to ws://gateway-host:8765

2. AI backend sends handshake (JSON text):
   {"call_id": "<SIP Call-ID string>"}

3. Gateway confirms the call exists and starts streaming audio.

4. Gateway → AI (binary frames):
   Raw PCM-16 LE · 8000 Hz · mono · 20 ms per frame = 320 bytes

5. AI → Gateway (binary frames):
   Same format: PCM-16 LE · 8000 Hz · mono · 20 ms = 320 bytes
   Gateway re-encodes (G.711) and sends as RTP to the caller.

6. Gateway → AI (JSON text) on call end:
   {"event": "call_end", "call_id": "..."}

7. AI → Gateway (JSON text) to hang up proactively:
   {"event": "hangup", "call_id": "..."}
   Gateway sends SIP BYE and terminates the call.
```

### How the AI backend knows about a new call

**Option A — webhook (recommended)**

Set `bridge.ai_webhook_url` in `config.yaml`. The gateway will POST:

```json
{
  "event": "call_start",
  "call_id": "f81d4fae-7dec-11d0-...",
  "caller": "+39029000001",
  "callee": "+390291234567",
  "ws_url": "ws://1.2.3.4:8765"
}
```

Your AI service receives the webhook, extracts `call_id` and `ws_url`, and immediately connects.

**Option B — polling**

Your AI backend connects to the WebSocket before any call arrives and repeatedly reconnects after each `call_end`. Works for single-call scenarios.

**Option C — out-of-band signalling**

Use your own mechanism (Redis pub/sub, AMQP, etc.) to push the `call_id` to a pool of AI workers.

### Minimal Python AI backend example

```python
import asyncio, json, websockets

async def handle_call(call_id: str, ws_url: str):
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"call_id": call_id}))
        async for msg in ws:
            if isinstance(msg, bytes):
                # msg = PCM16-LE audio from caller (320 bytes / 20 ms)
                response_pcm = my_ai_pipeline(msg)   # your AI here
                await ws.send(response_pcm)
            elif isinstance(msg, str):
                event = json.loads(msg)
                if event.get("event") == "call_end":
                    break
```

### Audio format contract

| Direction | Format | Sample rate | Channels | Frame size |
|---|---|---|---|---|
| Gateway → AI | PCM-16 LE signed | 8000 Hz | 1 (mono) | 320 bytes (20 ms) |
| AI → Gateway | PCM-16 LE signed | 8000 Hz | 1 (mono) | 320 bytes (20 ms) |

The gateway accepts any chunk size from the AI backend (it buffers internally), but sending 320-byte (20 ms) chunks minimises latency.

---

## Testing

### Unit tests

```bash
python -m pytest tests/ -v
```

38 tests covering:
- G.711 μ-law and A-law encode/decode roundtrip quality
- SIP message parsing (request, response, compact headers)
- SDP offer parsing and answer generation
- RTP packet serialisation/deserialisation
- Jitter buffer ordering, packet loss, and sequence wraparound

### End-to-end SIP test

Terminal 1 — start gateway in echo mode:

```bash
python gateway.py --echo --dump-pcm /tmp/pcm/ --debug
```

Terminal 2 — send a SIP INVITE:

```bash
# Basic call (SIP signalling only, no RTP)
python scripts/send_test_invite.py --gw-ip 127.0.0.1

# Full call with RTP audio
python scripts/send_test_invite.py \
    --gw-ip 127.0.0.1 \
    --send-rtp \
    --duration 5
```

Expected output (terminal 1):

```
INFO  SIP server listening on UDP 0.0.0.0:5060
INFO  INVITE call_id=abc123  +caller → +callee  from 127.0.0.1
INFO  200 OK sent  call_id=abc123  codec=PCMU  rtp_port=10000
INFO  ACK received – RTP active  call_id=abc123
INFO  Echo mode active  call_id=abc123
INFO  BYE received  call_id=abc123
INFO  Call ended  call_id=abc123  duration=5.0s
```

### AI backend smoke test

Terminal 1 — start gateway (without echo mode, so it waits for AI):

```bash
python gateway.py --debug
```

Terminal 2 — start dummy AI backend in echo mode:

```bash
python scripts/dummy_ai_backend.py \
    --call-id <call-id-from-invite> \
    --mode echo
```

Terminal 3 — send INVITE:

```bash
python scripts/send_test_invite.py --gw-ip 127.0.0.1 --send-rtp --duration 10
```

The dummy backend also supports `--mode tone` (plays a 440 Hz sine) and `--mode silent` (receive only).  
Use `--save audio.pcm` to save inbound audio to a file.

---

## DIDWW setup

1. Log in to the DIDWW portal.
2. Create a **SIP Trunk** (type: IP-based, no registration).
3. Set the inbound destination:
   ```
   sip:YOUR_PUBLIC_IP:5060
   ```
4. Under codec settings, enable only:
   - G.711 μ-law (PCMU)
   - G.711 A-law (PCMA)  ← optional; gateway handles both
5. Disable DTMF in-band (or leave default; gateway ignores INFO/RFC 2833).
6. Note the DIDWW sending IP ranges and add them to `config.yaml`:

   ```yaml
   sip:
     allowed_ips:
       - "185.98.36.0/22"
       - "185.98.40.0/22"
       - "185.98.44.0/22"
   ```

7. Test with a DIDWW test call; monitor the gateway log for `INVITE` messages.

### Firewall rules (Linux/iptables)

```bash
# SIP
iptables -A INPUT -p udp --dport 5060 -j ACCEPT

# RTP
iptables -A INPUT -p udp --dport 10000:20000 -j ACCEPT

# WebSocket bridge (only if AI backend is on a remote host)
iptables -A INPUT -p tcp --dport 8765 -j ACCEPT
```

---

## Project structure

```
sip-server/
├── gateway.py                  Entry point / CLI
├── requirements.txt
├── config/
│   └── config.yaml             Runtime configuration
├── src/
│   ├── config.py               Config loader (reads YAML, detects public IP)
│   ├── main.py                 Async bootstrap (wires all components)
│   ├── codec/
│   │   └── g711.py             G.711 μ-law + A-law encode/decode (pure Python)
│   ├── sip/
│   │   ├── message.py          SIP message parser + response builder (RFC 3261)
│   │   ├── sdp.py              SDP offer/answer parser + generator (RFC 4566)
│   │   └── server.py           Async UDP SIP server with IP whitelist
│   ├── rtp/
│   │   ├── packet.py           RTP packet parser + serialiser (RFC 3550)
│   │   ├── jitter_buffer.py    Sequence-ordered jitter buffer with PLC
│   │   ├── port_manager.py     Thread-safe UDP port allocator
│   │   └── session.py          RTP session (playout loop, send/receive)
│   ├── bridge/
│   │   └── websocket_bridge.py WebSocket audio bridge for AI backend
│   └── call/
│       ├── call.py             Per-call state machine + audio routing
│       └── manager.py          Routes SIP messages to the correct Call
├── tests/
│   ├── test_codec.py           G.711 unit tests
│   ├── test_sip.py             SIP parsing / response building tests
│   └── test_rtp.py             RTP packet + jitter buffer tests
├── scripts/
│   ├── send_test_invite.py     Minimal SIP INVITE test client
│   └── dummy_ai_backend.py     AI backend stub (echo / tone / silent)
└── logs/                       Log output directory
```

---

## Component reference

### `src/codec/g711.py`

Pure Python implementation of ITU-T G.711, based on the Sun Microsystems reference.  
Uses pre-computed lookup tables for decode performance.

```python
from src.codec.g711 import decode, encode

# Decode inbound RTP payload to PCM
pcm = decode(payload_type=0, data=rtp_payload)   # PCMU
pcm = decode(payload_type=8, data=rtp_payload)   # PCMA

# Encode PCM from AI backend to RTP payload
rtp_payload = encode(payload_type=0, data=pcm)
```

### `src/sip/message.py`

Parses raw UDP datagrams into `SIPMessage` objects. Handles compact header names (`v` → `Via`, `i` → `Call-ID`, etc.). Builds well-formed SIP responses via `build_response()`.

### `src/rtp/session.py`

Manages one UDP socket per call. A background asyncio task pops from the jitter buffer every 20 ms and calls `on_audio(payload_bytes)`. Outbound audio is sent via `send_audio(rtp_payload)`.

### `src/bridge/websocket_bridge.py`

WebSocket server that holds one connection per active call. Calls `register_call(call_id, handler)` to attach a coroutine that is invoked for events: `audio_out`, `hangup`, `ai_connected`, `ai_disconnected`.

### `src/call/call.py` — Call state machine

```
IDLE
  ↓  SIP INVITE received
INVITE_RECEIVED
  ↓  SDP parsed, RTP port allocated, 200 OK sent
SDP_NEGOTIATED
  ↓  ACK received
RTP_ACTIVE
  ↓  AI backend WebSocket connected
AI_STREAM_ACTIVE
  ↓  BYE received (or AI sends hangup)
TERMINATING
  ↓  resources freed
DONE
```

---

## Performance targets

| Metric | Target | Notes |
|---|---|---|
| Call setup latency | < 300 ms | From INVITE to 200 OK |
| End-to-end audio latency | < 800 ms | Gateway contribution ≈ 60–100 ms |
| Concurrent calls (MVP) | 10–50 | Limited by AI backend, not gateway |
| Packet loss handling | Basic PLC | Silence frame inserted for lost packets |
| CPU per call | < 1 % | Single core, Python 3.8 |

---

## Security

| Measure | Implementation |
|---|---|
| IP whitelist | `sip.allowed_ips` in config; all other sources are silently dropped |
| No SIP registration | Gateway is a pure UAS; no `REGISTER` processing |
| No open relay | Only handles calls to configured local numbers |
| WebSocket binding | Default `0.0.0.0`; bind to `127.0.0.1` if AI backend is local |
| Rate limiting | Not implemented — add a reverse proxy (nginx, HAProxy) in front for production |
| TLS | Not implemented (planned); use a TLS terminator for SIPS/WSS |

**Production checklist:**
- [ ] Set `sip.allowed_ips` to DIDWW CIDR ranges only
- [ ] Bind `bridge.host` to `127.0.0.1` if AI backend is co-located
- [ ] Run gateway as a non-root user (bind port 5060 via `setcap` or use a non-privileged port + port forward)
- [ ] Add fail2ban or rate limiting on UDP 5060
- [ ] Run behind a reverse proxy with TLS for the WebSocket bridge

---

## Troubleshooting

### Gateway does not receive the INVITE

1. Confirm UDP 5060 is open: `nc -u -z -v YOUR_IP 5060`
2. Check `allowed_ips` — empty list allows all, populated list must include DIDWW IPs.
3. Run with `--debug` to see all incoming datagrams.

### 200 OK is sent but ACK never arrives / no RTP

- The `Contact` header in the 200 OK must contain the public IP. Verify `public_ip` in config or use `--public-ip YOUR_IP`.
- The SDP answer `c=` line uses `public_ip` — if the server is behind NAT, the RTP port must be reachable from outside.

### Audio is choppy or one-way

- Increase `jitter_buffer_packets` to 5–6 in config.
- Verify the AI backend is sending frames every 20 ms (not in large bursts).
- Check that the AI backend sends PCM-16 LE at exactly 8000 Hz mono.

### `No free RTP ports in range` error

- The range `rtp_port_min`/`rtp_port_max` is exhausted or those ports are blocked by the OS.
- Run `ss -uln | grep 10000` to see what is already bound.
- Increase the range or reduce `max_concurrent_calls`.

### High CPU usage

The A-law and μ-law encode/decode functions are pure Python loops. For > 50 concurrent calls, replace with NumPy vectorised operations or compile with Cython. A 10× speedup is achievable with a one-line NumPy rewrite of the inner loops.

---

## Extending the gateway

### Add TCP SIP support

In `src/sip/server.py`, create a second endpoint using `loop.create_server()` on port 5060/TCP and feed messages into the same `_dispatch` coroutine.

### Add DTMF (RFC 2833 / telephone-event)

Parse payload type 101 (or negotiated PT) in `src/rtp/session.py` and emit a `dtmf` event to the bridge.

### Add SRTP

Replace the raw UDP socket in `RTPSession` with a socket wrapped by a DTLS-SRTP library such as `pylibsrtp`.

### Scale beyond 50 calls

- Run multiple gateway processes behind a SIP load balancer (Kamailio, OpenSIPS).
- Each process handles its own RTP port range (e.g., 10000–14999, 15000–19999).
- The WebSocket bridge is stateless per call, so horizontal scaling is straightforward.
