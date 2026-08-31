"""Microphone capture through PortAudio (sounddevice)."""
from __future__ import annotations

import numpy as np

from ..interfaces import AudioCapture
from ..log import log
from ..registry import register


@register("audio", "sounddevice", priority=100)
class SoundDeviceCapture(AudioCapture):
    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._frames: list[np.ndarray] = []
        self._stream = None
        self._started_at = 0.0

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        try:
            import sounddevice  # noqa: F401
        except Exception:
            return False
        return True

    @property
    def samplerate(self) -> int:
        return self.cfg["samplerate"]

    def start(self) -> None:
        import sounddevice as sd
        import time
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="float32",
            blocksize=1024,
            device=self.cfg["input_device"],
            callback=self._callback,
        )
        self._stream.start()
        self._started_at = time.monotonic()

    def _callback(self, indata, frames, time_info, status):
        if status:
            log(f"audio: {status}")
        self._frames.append(indata.copy())

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._frames, axis=0).reshape(-1)

    @property
    def elapsed(self) -> float:
        import time
        return time.monotonic() - self._started_at
