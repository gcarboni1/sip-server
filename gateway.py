#!/usr/bin/env python3
"""SIP Media Gateway – entry point.

Usage:
    python gateway.py [options]

Options:
    --config PATH       Path to config YAML (default: config/config.yaml)
    --echo              Echo mode: loop inbound audio back to caller (no AI)
    --dump-pcm DIR      Dump incoming PCM audio to DIR/<call_id>.pcm files
    --public-ip IP      Override detected public IP
    --debug             Set log level to DEBUG
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from src.config import Config
from src.main import run, setup_logging


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SIP Media Gateway")
    p.add_argument("--config", default="config/config.yaml",
                   help="Config file path")
    p.add_argument("--echo", action="store_true",
                   help="Echo mode: loop inbound audio back to caller")
    p.add_argument("--dump-pcm", metavar="DIR",
                   help="Dump incoming PCM to DIR/*.pcm files")
    p.add_argument("--public-ip", metavar="IP",
                   help="Override public IP (overrides config)")
    p.add_argument("--debug", action="store_true",
                   help="Set log level to DEBUG")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    cfg = Config(args.config)

    if args.debug:
        cfg.log_level = "DEBUG"
    if args.echo:
        cfg.echo_mode = True
    if args.dump_pcm:
        cfg.pcm_dump_dir = args.dump_pcm
        os.makedirs(args.dump_pcm, exist_ok=True)
    if args.public_ip:
        cfg.public_ip = args.public_ip

    setup_logging(cfg.log_level, cfg.log_file)

    asyncio.run(run(cfg))


if __name__ == "__main__":
    main()
