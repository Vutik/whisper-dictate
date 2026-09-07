"""Short cues through PulseAudio/PipeWire's paplay."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..interfaces import SoundPlayer
from ..log import log
from ..registry import register


@register("sound", "paplay", priority=100)
class PaplaySound(SoundPlayer):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._complained: set[str] = set()

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        return bool(shutil.which("paplay"))

    def _complain_once(self, key: str, message: str) -> None:
        # Cues fire several times per dictation; one line per distinct fault
        # is a hint, one per cue is a flood.
        if key not in self._complained:
            self._complained.add(key)
            log(f"sound: {message}")

    def play(self, event: str) -> None:
        if not self.cfg["sounds"]:
            return
        path = self.cfg.get(f"sound_{event}")
        if not path:
            return
        if not Path(path).exists():
            self._complain_once(
                f"missing:{event}",
                f"sound_{event} points at {path}, which does not exist — "
                f"this cue stays silent")
            return
        try:
            subprocess.Popen(["paplay", path],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception as exc:                              # noqa: BLE001
            self._complain_once(f"spawn:{exc.__class__.__name__}",
                                f"cannot run paplay: {exc}")
