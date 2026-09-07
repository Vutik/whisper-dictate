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
        self._described = ""
        self._glitches: dict[str, int] = {}

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

    def _describe(self, sd) -> str:
        """Which device PortAudio actually settled on.

        Worth logging because `input_device: null` means "whatever the
        system default is", and that can silently become a different device
        — or a dummy one — without anything else in the daemon noticing.
        """
        try:
            requested = self.cfg["input_device"]
            info = sd.query_devices(requested, "input")
            host = sd.query_hostapis(info["hostapi"])["name"]
            return (f"{info['name']} via {host}"
                    f"{'' if requested is not None else ' (system default)'}")
        except Exception as exc:                              # noqa: BLE001
            return f"unknown ({exc})"

    def start(self) -> None:
        import sounddevice as sd
        import time

        # Log the device once, and again whenever it changes underneath us.
        described = self._describe(sd)
        if described != self._described:
            log(f"audio: capturing from {described}")
            self._described = described

        self._frames = []
        self._glitches = {}
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
        # Counted rather than logged: a wedged stream raises this on every
        # block, and 40 lines a second buries whatever else went wrong.
        if status:
            self._glitches[str(status)] = self._glitches.get(str(status), 0) + 1
        self._frames.append(indata.copy())

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if self._glitches:
            summary = ", ".join(f"{n}× {flag}"
                                for flag, n in sorted(self._glitches.items()))
            log(f"audio: PortAudio reported {summary} during {self.elapsed:.1f} s "
                f"— samples were dropped")

        if not self._frames:
            log(f"audio: {self._described} delivered no blocks in "
                f"{self.elapsed:.1f} s")
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._frames, axis=0).reshape(-1)

    @property
    def elapsed(self) -> float:
        import time
        return time.monotonic() - self._started_at
