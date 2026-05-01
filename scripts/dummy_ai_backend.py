#!/usr/bin/env python3
"""
Dummy AI backend for gateway integration testing.

Connects to the gateway WebSocket, receives PCM audio from the caller,
optionally applies a transform (echo / tone-gen), and sends audio back.

Usage:
    # Pure echo (loops back what it hears)
    python scripts/dummy_ai_backend.py --call-id <id> --mode echo

    # Play a 440 Hz sine tone regardless of input
    python scripts/dummy_ai_backend.py --call-id <id> --mode tone

    # Silent (receive only – useful to verify inbound pipeline)
    python scripts/dummy_ai_backend.py --call-id <id> --mode silent

    # Save inbound PCM to file
    python scripts/dummy_ai_backend.py --call-id <id> --mode echo --save inbound.pcm

The gateway WebSocket handshake:
    send: {"call_id": "<id>"}
    then binary frames = PCM-16LE 8 kHz mono, 20 ms chunks (320 bytes)
"""

import argparse
import asyncio
import json
import math
import struct
import sys
import time

try:
    import websockets
except ImportError:
    print("pip install websockets")
    sys.exit(1)


SAMPLE_RATE = 8000
FRAME_MS    = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000   # 160
FRAME_BYTES   = FRAME_SAMPLES * 2                # 320 (PCM16)


def gen_sine(freq: float, frame_index: int) -> bytes:
    """Generate one 20 ms frame of a sine wave at `freq` Hz."""
    samples = []
    for i in range(FRAME_SAMPLES):
        t = (frame_index * FRAME_SAMPLES + i) / SAMPLE_RATE
        s = int(16000 * math.sin(2 * math.pi * freq * t))
        samples.append(max(-32768, min(32767, s)))
    return struct.pack(f"<{FRAME_SAMPLES}h", *samples)


async def run(ws_url: str, call_id: str, mode: str, save_path: str) -> None:
    print(f"Connecting to {ws_url}  call_id={call_id}  mode={mode}")
    save_file = open(save_path, "wb") if save_path else None
    frame_idx = 0
    rx_bytes = 0
    start = time.time()

    async with websockets.connect(ws_url) as ws:
        # Handshake
        await ws.send(json.dumps({"call_id": call_id}))
        print("Handshake sent – waiting for audio…")

        async def receiver():
            nonlocal rx_bytes
            async for msg in ws:
                if isinstance(msg, bytes):
                    rx_bytes += len(msg)
                    if save_file:
                        save_file.write(msg)
                    if mode == "echo":
                        await ws.send(msg)
                elif isinstance(msg, str):
                    evt = json.loads(msg)
                    print(f"Control message: {evt}")
                    if evt.get("event") == "call_end":
                        break

        async def sender():
            nonlocal frame_idx
            if mode not in ("tone",):
                return
            while True:
                frame = gen_sine(440.0, frame_idx)
                frame_idx += 1
                await ws.send(frame)
                await asyncio.sleep(FRAME_MS / 1000)

        try:
            if mode == "tone":
                await asyncio.gather(receiver(), sender())
            else:
                await receiver()
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if save_file:
                save_file.close()

    elapsed = time.time() - start
    print(f"Done. rx={rx_bytes} bytes  duration={elapsed:.1f}s")


def main():
    p = argparse.ArgumentParser(description="Dummy AI backend for SIP gateway testing")
    p.add_argument("--ws-url",  default="ws://127.0.0.1:8765",
                   help="Gateway WebSocket URL")
    p.add_argument("--call-id", required=True,
                   help="Call-ID to connect to")
    p.add_argument("--mode",    choices=["echo", "tone", "silent"], default="echo",
                   help="Audio mode")
    p.add_argument("--save",    metavar="FILE",
                   help="Save inbound PCM to file")
    args = p.parse_args()

    asyncio.run(run(args.ws_url, args.call_id, args.mode, args.save))


if __name__ == "__main__":
    main()
