"""Text injection on Wayland.

The clipboard half is easy — wl-copy works on any compositor. Delivering the
paste keystroke is the hard part: GNOME's Mutter does not implement
zwp_virtual_keyboard_v1, so `wtype` is a no-op there, and the only portable
route is the kernel's uinput device via ydotool.

Without ydotool the backend degrades to clipboard-only: the text is copied and
the user presses paste. That needs no privileges at all.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

from ..interfaces import TextInjector
from ..log import log
from ..registry import register


@register("inject", "wayland", priority=110)
class WaylandInjector(TextInjector):
    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        if not os.environ.get("WAYLAND_DISPLAY"):
            return False
        return bool(shutil.which("wl-copy"))

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._typer = self._pick_typer()
        if self._typer is None and cfg["insert_method"] == "paste":
            log("wayland: no ydotool/wtype — falling back to clipboard-only; "
                "press paste yourself, or install ydotool")

    @staticmethod
    def _pick_typer() -> str | None:
        # ydotool goes through /dev/uinput and therefore works on GNOME too;
        # wtype only works on compositors with the virtual-keyboard protocol.
        for candidate in ("ydotool", "wtype"):
            if shutil.which(candidate):
                return candidate
        return None

    def _clipboard_get(self) -> bytes:
        try:
            r = subprocess.run(["wl-paste", "--no-newline"],
                               capture_output=True, timeout=1.0)
            return r.stdout if r.returncode == 0 else b""
        except Exception:
            return b""

    def _clipboard_set(self, data: bytes) -> None:
        subprocess.run(["wl-copy"], input=data, timeout=2.0)

    def _send_paste(self) -> None:
        combo = self.cfg.get("wayland_paste_combo", "ctrl+v")
        if self._typer == "ydotool":
            # ydotool key takes keycodes: 29 = leftctrl, 47 = v.
            keymap = {"ctrl": 29, "shift": 42, "alt": 56, "super": 125,
                      "v": 47}
            parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
            codes = [keymap.get(p) for p in parts]
            if any(c is None for c in codes):
                log(f"wayland: cannot map {combo!r} to keycodes")
                return
            seq = [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]
            subprocess.run(["ydotool", "key", *seq], timeout=5)
        elif self._typer == "wtype":
            args = []
            for part in combo.split("+")[:-1]:
                args += ["-M", part]
            args += ["-k", combo.split("+")[-1]]
            subprocess.run(["wtype", *args], timeout=5)

    def insert(self, text: str) -> None:
        method = self.cfg["insert_method"]

        if method == "type" and self._typer == "wtype":
            subprocess.run(["wtype", text], timeout=60)
            return
        if method == "type" and self._typer == "ydotool":
            subprocess.run(["ydotool", "type", "--", text], timeout=60)
            return

        previous = self._clipboard_get() if self.cfg["restore_clipboard"] else None
        self._clipboard_set(text.encode())

        if method == "clipboard" or self._typer is None:
            return

        time.sleep(0.05)
        self._send_paste()

        if previous is not None:
            def restore():
                time.sleep(0.6)
                self._clipboard_set(previous)
            threading.Thread(target=restore, daemon=True).start()
