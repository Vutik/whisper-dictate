"""Text injection on X11.

Uses the clipboard plus a paste keystroke rather than synthetic typing: with a
non-Latin keyboard layout active, `xdotool type` produces garbage, while the
clipboard path is layout-independent.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time

from ..interfaces import TextInjector
from ..log import log
from ..registry import register


@register("inject", "x11", priority=100)
class X11Injector(TextInjector):
    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        if not os.environ.get("DISPLAY"):
            return False
        return all(shutil.which(b) for b in ("xdotool", "xclip"))

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _active_window_class() -> str:
        """WM_CLASS of the focused window.

        xdotool 3.2016 (Ubuntu 22.04) has no getwindowclassname, so go via
        xprop.
        """
        try:
            wid = subprocess.run(["xdotool", "getactivewindow"],
                                 capture_output=True, text=True,
                                 timeout=1.0).stdout.strip()
            if not wid:
                return ""
            out = subprocess.run(["xprop", "-id", wid, "WM_CLASS"],
                                 capture_output=True, text=True,
                                 timeout=1.0).stdout
            return " ".join(re.findall(r'"([^"]*)"', out))
        except Exception:
            return ""

    @staticmethod
    def _clipboard_get() -> bytes:
        try:
            r = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                               capture_output=True, timeout=1.0)
            return r.stdout if r.returncode == 0 else b""
        except Exception:
            return b""

    @staticmethod
    def _clipboard_set(data: bytes) -> None:
        p = subprocess.Popen(["xclip", "-selection", "clipboard", "-i"],
                             stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        try:
            p.communicate(data, timeout=2.0)
        except Exception:
            p.kill()

    # -- interface ---------------------------------------------------------

    def insert(self, text: str) -> None:
        method = self.cfg["insert_method"]

        if method == "type":
            subprocess.run(["xdotool", "type", "--clearmodifiers",
                            "--delay", str(self.cfg["type_delay_ms"]),
                            "--", text], timeout=60)
            return

        previous = self._clipboard_get() if self.cfg["restore_clipboard"] else None
        self._clipboard_set(text.encode())

        if method == "clipboard":
            return

        wm_class = self._active_window_class()
        is_terminal = any(t.lower() in wm_class.lower()
                          for t in self.cfg["terminal_classes"])
        combo = "ctrl+shift+v" if is_terminal else "ctrl+v"
        time.sleep(0.05)
        subprocess.run(["xdotool", "key", "--clearmodifiers", combo], timeout=5)

        if previous is not None:
            def restore():
                time.sleep(0.6)
                self._clipboard_set(previous)
            threading.Thread(target=restore, daemon=True).start()
