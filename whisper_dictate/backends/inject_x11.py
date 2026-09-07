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
import tempfile
import threading
import time

from ..interfaces import TextInjector
from ..log import log
from ..registry import register


@register("inject", "x11", priority=100)
class X11Injector(TextInjector):
    _warned_about_window = False

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
        except Exception as exc:                              # noqa: BLE001
            # Falling back to "" picks the non-terminal paste combo, which
            # is wrong in a terminal — say so once rather than never.
            if not X11Injector._warned_about_window:
                X11Injector._warned_about_window = True
                log(f"inject: cannot read the focused window class ({exc}); "
                    f"assuming a non-terminal paste combo from here on")
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
        # stderr goes to a file, never to a pipe. `xclip -i` forks a child
        # that owns the selection until someone else claims it, and that
        # child inherits our stderr — on a pipe, communicate() would wait
        # for an EOF that only arrives when the clipboard changes hands,
        # and every paste would cost the full timeout.
        with tempfile.TemporaryFile() as err:
            p = subprocess.Popen(["xclip", "-selection", "clipboard", "-i"],
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL,
                                 stderr=err)
            try:
                p.communicate(data, timeout=2.0)
            except Exception as exc:
                p.kill()
                raise RuntimeError(
                    f"xclip did not accept the text: {exc}") from exc
            if p.returncode != 0:
                err.seek(0)
                message = err.read().decode(errors="replace").strip()
                raise RuntimeError(f"xclip exited {p.returncode}: "
                                   f"{message or 'no output'}")

    @staticmethod
    def _run(argv: list[str], timeout: float) -> None:
        """Run a helper and turn a non-zero exit into a real exception.

        Without this a failing `xdotool key` is invisible: the text sits on
        the clipboard, nothing is pasted, and the daemon still reports the
        dictation as a success.
        """
        try:
            r = subprocess.run(argv, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"{argv[0]} timed out after {timeout:g} s") from exc
        if r.returncode != 0:
            raise RuntimeError(
                f"{argv[0]} exited {r.returncode}: "
                f"{r.stderr.decode(errors='replace').strip() or 'no output'}")

    # -- interface ---------------------------------------------------------

    def insert(self, text: str) -> None:
        method = self.cfg["insert_method"]

        if method == "type":
            self._run(["xdotool", "type", "--clearmodifiers",
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
        self._run(["xdotool", "key", "--clearmodifiers", combo], timeout=5)

        if previous is not None:
            def restore():
                time.sleep(0.6)
                try:
                    self._clipboard_set(previous)
                except Exception as exc:                      # noqa: BLE001
                    # Best effort by design: losing the old clipboard is a
                    # nuisance, not a reason to raise out of a stray thread.
                    log(f"inject: could not restore the previous clipboard: "
                        f"{exc}")
            threading.Thread(target=restore, daemon=True).start()
