"""Turning a failed dictation into a sentence that names the cause.

An empty transcript has several very different causes — a dead capture
device, a muted microphone, a user who said nothing, a recogniser that
genuinely heard no words — and the daemon used to report all of them the
same way: by returning silently. Measuring the captured waveform separates
them, because each leaves a distinct signature in the sample values.

Kept out of `core.py` so the classification can be unit-tested without a
state machine, and reused by any future capture path.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Anything at or below this is not a recording, it is a buffer of zeros.
#: A real microphone always carries a noise floor, even in a quiet room.
SILENT = 1e-6

#: About -54 dBFS. Below this there is a signal path but nothing usable on
#: it — a muted input, or capture gain sitting at zero.
FAINT = 2e-3


@dataclass(frozen=True)
class Level:
    """How loud a captured buffer actually was."""

    peak: float
    rms: float
    samples: int

    def __str__(self) -> str:
        return f"peak {self.peak:.5f} rms {self.rms:.5f}"


def measure(audio: np.ndarray) -> Level:
    if audio is None or len(audio) == 0:
        return Level(0.0, 0.0, 0)
    finite = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    return Level(peak=float(np.abs(finite).max()),
                 rms=float(np.sqrt(np.mean(np.square(finite)))),
                 samples=int(len(finite)))


def is_dead(level: Level) -> bool:
    """True when the buffer cannot possibly hold speech.

    Exact digital silence is not a quiet room: no ADC produces it. Worth
    testing separately from `explain_silence`, because it is the one case
    where running the recogniser is pure waste — and worse than waste, since
    Whisper invents subtitles over silence.
    """
    return level.samples == 0 or level.peak <= SILENT


def explain_silence(level: Level) -> tuple[str, str] | None:
    """Name the fault behind a quiet buffer, or None if it was loud enough.

    Returns ``(headline, detail)``: the headline is short enough for a
    desktop notification, the detail carries the command that tells the
    user which of the two silent cases they are in.
    """
    if level.samples == 0:
        return ("Microphone produced no audio",
                "the capture stream opened but delivered zero samples — "
                "the device disappeared mid-recording")

    if level.peak <= SILENT:
        return ("Microphone is silent",
                "every captured sample is zero, so this is not a real "
                "microphone. Check `pactl list short sources`: if the only "
                "entry is `auto_null.monitor`, the sound card dropped out of "
                "PipeWire — run `systemctl --user restart wireplumber`, then "
                "restart this daemon so PortAudio re-reads the device list")

    if level.peak < FAINT:
        return ("Microphone almost silent",
                f"{level} — there is a signal path but nothing on it. "
                "The input is probably muted or its capture volume is at "
                "zero: check `pactl get-source-mute @DEFAULT_SOURCE@` and "
                "`pactl get-source-volume @DEFAULT_SOURCE@`")

    return None
