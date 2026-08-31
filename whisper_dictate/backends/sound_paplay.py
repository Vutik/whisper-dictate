"""Short cues through PulseAudio/PipeWire's paplay."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..interfaces import SoundPlayer
from ..registry import register


@register("sound", "paplay", priority=100)
class PaplaySound(SoundPlayer):
    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        return bool(shutil.which("paplay"))

    def play(self, event: str) -> None:
        if not self.cfg["sounds"]:
            return
        path = self.cfg.get(f"sound_{event}")
        if path and Path(path).exists():
            subprocess.Popen(["paplay", path],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
