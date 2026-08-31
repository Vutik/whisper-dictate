"""Fakes standing in for every platform backend.

The point of the interfaces is that the core can be exercised with no GPU, no
microphone, no display server and no network — these are the stand-ins that
make that true.
"""
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from whisper_dictate.core import Dictator                       # noqa: E402
from whisper_dictate.interfaces import (READY, AudioCapture, Notifier,  # noqa: E402
                                        PARKED, SoundPlayer, SpeechToText,
                                        TextInjector, TextProcessor,
                                        Transcript, UNLOADED)


class FakeSTT(SpeechToText):
    supports_parking = True
    backend_name = "fake-stt"

    def __init__(self, cfg, text="привет", language="ru"):
        super().__init__(cfg)
        self.text = text
        self.language = language
        self._state = UNLOADED
        self.loads = 0
        self.unloads = []
        self.calls = []
        self.fail_with = None

    @property
    def state(self):
        return self._state

    def load(self):
        self.loads += 1
        self._state = READY

    def unload(self, deep=False):
        self.unloads.append(deep)
        self._state = UNLOADED if deep else PARKED

    def transcribe(self, audio, samplerate):
        self.calls.append((len(audio), samplerate))
        if self.fail_with:
            raise self.fail_with
        return Transcript(text=self.text, language=self.language,
                          language_probability=0.99,
                          duration=len(audio) / samplerate)


class FakeAudio(AudioCapture):
    backend_name = "fake-audio"

    def __init__(self, cfg, seconds=2.0):
        super().__init__(cfg)
        self.seconds = seconds
        self.started = 0
        self.fail_on_start = None

    @property
    def samplerate(self):
        return self.cfg["samplerate"]

    def start(self):
        if self.fail_on_start:
            raise self.fail_on_start
        self.started += 1

    def stop(self):
        n = int(self.seconds * self.samplerate)
        return np.zeros(n, dtype=np.float32)

    @property
    def elapsed(self):
        return self.seconds


class FakeInjector(TextInjector):
    backend_name = "fake-inject"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.inserted = []

    def insert(self, text):
        self.inserted.append(text)


class FakeNotifier(Notifier):
    backend_name = "fake-notify"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.shown = []
        self.closed = 0

    def show(self, title, body="", timeout_ms=4000, close_after=None):
        self.shown.append((title, body))

    def close(self):
        self.closed += 1


class FakeSound(SoundPlayer):
    backend_name = "fake-sound"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.played = []

    def play(self, event):
        self.played.append(event)


class Passthrough(TextProcessor):
    backend_name = "fake-post"

    def process(self, transcript):
        return transcript.text.strip()


@pytest.fixture
def cfg():
    from whisper_dictate import config
    base = dict(config.DEFAULTS)
    base["max_seconds"] = 5
    base["idle_unload_seconds"] = 0
    base["deep_unload_seconds"] = 0
    return base


@pytest.fixture
def parts(cfg):
    return {
        "stt": FakeSTT(cfg),
        "audio": FakeAudio(cfg),
        "injector": FakeInjector(cfg),
        "notifier": FakeNotifier(cfg),
        "sound": FakeSound(cfg),
        "postprocessor": Passthrough(cfg),
    }


@pytest.fixture
def dictator(cfg, parts):
    d = Dictator(cfg, **parts)
    d.ensure_loaded()
    yield d
    d.shutdown()


def wait_idle(dictator, timeout=5.0):
    """Transcription runs on a worker thread; wait for it to settle."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with dictator.lock:
            if dictator.state == Dictator.IDLE:
                return True
        time.sleep(0.01)
    return False
