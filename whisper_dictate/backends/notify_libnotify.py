"""Fallback banner via notify-send.

Cannot replace or close a banner on libnotify < 0.8, so timeouts are kept
short and every banner is allowed to expire on its own.
"""
from __future__ import annotations

import shutil
import subprocess

from ..interfaces import Notifier
from ..registry import register


@register("notify", "libnotify", priority=50)
class LibNotifyNotifier(Notifier):
    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        return bool(shutil.which("notify-send"))

    def show(self, title: str, body: str = "", timeout_ms: int = 4000,
             close_after: float | None = None) -> None:
        if not self.cfg["notifications"]:
            return
        subprocess.Popen(
            ["notify-send", "-a", "Dictate", "-t", str(min(timeout_ms, 5000)),
             title, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def close(self) -> None:
        pass
