"""Standalone recognition server.

Runs the model behind the HTTP API and nothing else — no microphone, no
hotkey, no clipboard, no desktop notifications. Meant for a machine with the
GPU that other computers talk to, and it coexists with the dictation daemon:
each holds its own copy of the model.
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading

from . import APP, __version__
from . import backends  # noqa: F401  (registers implementations)
from . import config, registry
from .core import ModelHost
from .http_api import TranscriptionAPI
from .log import log


def build_engine(cfg: dict) -> ModelHost:
    return ModelHost(cfg,
                     stt=registry.create("stt", cfg),
                     postprocessor=registry.create("postprocess", cfg))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{APP}-api",
        description="Serve speech recognition over an OpenAI-compatible HTTP API.")
    parser.add_argument("--host", help="bind address (default from config)")
    parser.add_argument("--port", type=int, help="bind port (default from config)")
    parser.add_argument("--api-key", help="bearer token; required off loopback")
    parser.add_argument("--model", help="override the configured model")
    parser.add_argument("--preload", action="store_true",
                        help="load the model at startup instead of on first request")
    parser.add_argument("--version", action="version",
                        version=f"{APP}-api {__version__}")
    args = parser.parse_args(argv)

    cfg = config.load()
    if args.host:
        cfg["api_host"] = args.host
    if args.port:
        cfg["api_port"] = args.port
    if args.api_key:
        cfg["api_key"] = args.api_key
    if args.model:
        cfg["model"] = args.model

    engine = build_engine(cfg)
    if args.preload:
        engine.ensure_loaded()
    engine.start_background()

    api = TranscriptionAPI(cfg, engine)
    if not api.start():
        print(f"{APP}-api: {api.error}", file=sys.stderr)
        return 1

    stop = threading.Event()

    def shutdown(*_):
        log("shutting down")
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        stop.wait()
    finally:
        api.stop()
        engine.shutdown()
    return 0
