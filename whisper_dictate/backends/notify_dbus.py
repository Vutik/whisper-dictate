"""Desktop banner over the freedesktop Notifications DBus service.

libnotify 0.7.9 (Ubuntu 22.04) has no --print-id/--replace-id, so notify-send
can only fire-and-forget and a banner then sits on screen for its whole
timeout. Going through DBus returns the id, so one banner is replaced in place
and explicitly closed when the work is done.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading

from ..interfaces import Notifier
from ..log import log
from ..registry import register


@register("notify", "dbus", priority=100)
class DBusNotifier(Notifier):
    DEST = ["--session",
            "--dest", "org.freedesktop.Notifications",
            "--object-path", "/org/freedesktop/Notifications"]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.gdbus = shutil.which("gdbus") or "/usr/bin/gdbus"
        self.nid = 0
        self.lock = threading.Lock()
        self.timer: threading.Timer | None = None

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        if not shutil.which("gdbus"):
            return False
        return bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS")
                    or os.path.exists(f"/run/user/{os.getuid()}/bus"))

    def _call(self, method: str, *args: str):
        return subprocess.run(
            [self.gdbus, "call", *self.DEST,
             "--method", f"org.freedesktop.Notifications.{method}", *args],
            capture_output=True, text=True, timeout=3)

    def show(self, title: str, body: str = "", timeout_ms: int = 4000,
             close_after: float | None = None) -> None:
        if not self.cfg["notifications"]:
            return
        with self.lock:
            self._cancel_timer()
            try:
                r = self._call("Notify", "Dictate", str(self.nid), "",
                               title, body, "[]", "{}", str(timeout_ms))
                m = re.search(r"uint32 (\d+)", r.stdout)
                if m:
                    self.nid = int(m.group(1))
            except Exception as exc:
                log(f"notify failed: {exc}")
                return
            if close_after:
                self.timer = threading.Timer(close_after, self.close)
                self.timer.daemon = True
                self.timer.start()

    def close(self) -> None:
        with self.lock:
            self._cancel_timer()
            if not self.nid:
                return
            try:
                self._call("CloseNotification", str(self.nid))
            except Exception:
                pass
            self.nid = 0

    def _cancel_timer(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
