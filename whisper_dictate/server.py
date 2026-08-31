"""Wiring: build backends from config, serve the control socket."""
from __future__ import annotations

import argparse
import signal
import socket
import socketserver
import sys
import threading
import time

from . import APP, __version__
from . import backends  # noqa: F401  (registers implementations)
from . import config, registry
from .core import Dictator
from .log import log


class Handler(socketserver.StreamRequestHandler):
    timeout = 5

    def handle(self):
        line = self.rfile.readline().decode(errors="replace").strip()
        sup = self.server.supervisor
        if line.lower() == "reload":
            reply = sup.reload(force=True)
        else:
            reply = sup.dictator.handle(line)
        self.wfile.write((reply + "\n").encode())


class Server(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True
    daemon_threads = True


def build(cfg: dict) -> Dictator:
    """Instantiate every replaceable part and inject it into the core."""
    return Dictator(
        cfg,
        stt=registry.create("stt", cfg),
        audio=registry.create("audio", cfg),
        injector=registry.create("inject", cfg),
        notifier=registry.create("notify", cfg),
        sound=registry.create("sound", cfg),
        postprocessor=registry.create("postprocess", cfg),
    )


# Changing any of these means the recogniser or the grab has to be rebuilt;
# everything else is cheap enough to rebuild unconditionally.
STT_KEYS = ("model", "device", "compute_type")
HOTKEY_KEYS = ("hotkey", "hotkey_grab")


class Supervisor:
    """Owns the config and rebuilds backends when it changes on disk."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.dictator = build(cfg)
        self.grabber = None
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._mtime = self._config_mtime()

    @staticmethod
    def _config_mtime() -> float:
        try:
            return config.CONFIG_PATH.stat().st_mtime
        except OSError:
            return 0.0

    def watch(self):
        while not self._stop.wait(2.0):
            if self._config_mtime() != self._mtime:
                self.reload()

    def stop(self):
        self._stop.set()

    def bind_hotkey(self):
        cfg = self.cfg
        if not (cfg["hotkey_grab"] and cfg["hotkey"]):
            return
        try:
            self.grabber = registry.create("hotkey", cfg, cfg["hotkey"],
                                           self.dictator.toggle)
            self.grabber.start()
        except registry.BackendError as exc:
            log(f"hotkey unavailable: {exc}")
            return
        grabber = self.grabber

        def report():
            if grabber.error:
                self.dictator.notifier.show("⚠️ Хоткей не захвачен",
                                            grabber.error, 8000, close_after=8.0)
        t = threading.Timer(0.5, report)
        t.daemon = True
        t.start()

    def reload(self, force: bool = False) -> str:
        with self.lock:
            self._mtime = self._config_mtime()
            try:
                new = config.load()
            except Exception as exc:
                log(f"reload: cannot read config: {exc}")
                return f"error: {exc}"

            old = self.cfg
            if new == old and not force:
                return "unchanged"

            changed = sorted(k for k in set(new) | set(old)
                             if new.get(k) != old.get(k))
            if not changed and not force:
                return "unchanged"
            log(f"reload: changed keys {changed or '(forced)'}")

            # Do not rebuild under someone's feet.
            for _ in range(50):
                with self.dictator.lock:
                    if self.dictator.state == self.dictator.IDLE:
                        break
                time.sleep(0.1)

            rebuilt = {}
            backend_names = new.get("backends") or {}
            old_names = old.get("backends") or {}

            need_stt = (any(k in changed for k in STT_KEYS)
                        or backend_names.get("stt") != old_names.get("stt"))
            if need_stt:
                try:
                    self.dictator.stt.unload(deep=True)
                except Exception:
                    pass
                rebuilt["stt"] = registry.create("stt", new)

            for kind, attr in (("audio", "audio"), ("inject", "injector"),
                               ("notify", "notifier"), ("sound", "sound"),
                               ("postprocess", "postprocessor")):
                try:
                    rebuilt[attr] = registry.create(kind, new)
                except registry.BackendError as exc:
                    log(f"reload: keeping current {kind} ({exc})")

            self.cfg = new
            self.dictator.apply(new, **rebuilt)

            need_hotkey = (any(k in changed for k in HOTKEY_KEYS)
                           or backend_names.get("hotkey") != old_names.get("hotkey"))
            if need_hotkey:
                if self.grabber:
                    self.grabber.stop()
                    self.grabber = None
                self.bind_hotkey()

            if need_stt:
                threading.Thread(target=self.dictator._preload, daemon=True).start()

            self.dictator.notifier.show("⚙️ Настройки перечитаны",
                                        ", ".join(changed[:6]) or "без изменений",
                                        3000, close_after=3.0)
            return "reloaded: " + (", ".join(changed) or "(forced)")


def _socket_in_use() -> bool:
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(config.SOCKET_PATH))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=APP)
    parser.add_argument("--list-backends", action="store_true",
                        help="show registered backends and exit")
    parser.add_argument("--version", action="version",
                        version=f"{APP} {__version__}")
    args = parser.parse_args(argv)

    cfg = config.load()

    if args.list_backends:
        print("registered backends (+ = usable here):")
        print(registry.report(cfg))
        return 0

    if config.SOCKET_PATH.exists():
        if _socket_in_use():
            print(f"{APP}: already running on {config.SOCKET_PATH}", file=sys.stderr)
            return 1
        config.SOCKET_PATH.unlink()

    supervisor = Supervisor(cfg)
    dictator = supervisor.dictator
    dictator.ensure_loaded()
    dictator.start_background()
    threading.Thread(target=supervisor.watch, daemon=True,
                     name="config-watch").start()

    server = Server(str(config.SOCKET_PATH), Handler)
    server.supervisor = supervisor
    server.dictator = dictator
    config.SOCKET_PATH.chmod(0o600)
    log(f"listening on {config.SOCKET_PATH}")

    supervisor.bind_hotkey()

    def shutdown(*_):
        # shutdown() blocks until serve_forever() returns, so it must not run
        # on the thread that is inside serve_forever().
        log("shutting down")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        server.serve_forever()
    finally:
        supervisor.stop()
        if supervisor.grabber:
            supervisor.grabber.stop()
        dictator.shutdown()
        server.server_close()
        config.SOCKET_PATH.unlink(missing_ok=True)
    return 0
