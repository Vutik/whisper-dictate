"""Microphone capture: the trailing window, and what a dead stream returns.

PortAudio is replaced by a fake module, so this needs no microphone.
"""
import sys
import threading
import types

import numpy as np
import pytest

from whisper_dictate.backends.audio_sounddevice import SoundDeviceCapture


class FakeStream:
    """Delivers one block at once and a second one 120 ms later.

    The late block stands in for the audio still in flight when the user hits
    the hotkey: whether it lands is exactly what `tail_seconds` decides.
    """

    def __init__(self, *, callback, **kw):
        self.callback = callback
        self.kw = kw
        self.closed = False
        self._timer = None

    def start(self):
        self._deliver()
        self._timer = threading.Timer(0.12, self._deliver)
        self._timer.daemon = True
        self._timer.start()

    def _deliver(self):
        if not self.closed:
            self.callback(np.full((1024, 1), 0.2, dtype=np.float32),
                          1024, None, None)

    def stop(self):
        self.closed = True
        if self._timer:
            self._timer.cancel()

    def close(self):
        self.closed = True


@pytest.fixture
def fake_sd(monkeypatch):
    mod = types.ModuleType("sounddevice")
    mod.InputStream = lambda **kw: FakeStream(**kw)
    mod.query_devices = lambda dev, kind: {"name": "Fake Mic", "hostapi": 0}
    mod.query_hostapis = lambda i: {"name": "FakeAPI"}
    monkeypatch.setitem(sys.modules, "sounddevice", mod)
    return mod


@pytest.fixture
def cfg():
    from whisper_dictate import config
    return dict(config.DEFAULTS, samplerate=16000, tail_seconds=0.25)


def blocks(audio):
    return len(audio) // 1024


def test_tail_keeps_the_last_syllable(fake_sd, cfg):
    cap = SoundDeviceCapture(cfg)
    cap.start()
    audio = cap.stop()
    assert blocks(audio) == 2, "the in-flight block should have been waited for"


def test_zero_tail_cuts_immediately(fake_sd, cfg):
    cfg["tail_seconds"] = 0
    cap = SoundDeviceCapture(cfg)
    cap.start()
    audio = cap.stop()
    assert blocks(audio) == 1, "without a tail the late block is lost"


def test_tail_is_configurable_and_survives_a_null(fake_sd, cfg):
    cfg["tail_seconds"] = None                 # a hand-edited config
    cap = SoundDeviceCapture(cfg)
    cap.start()
    assert blocks(cap.stop()) == 1             # treated as no tail, not a crash


def test_a_stream_that_delivers_nothing_returns_an_empty_buffer(fake_sd, cfg):
    cfg["tail_seconds"] = 0                    # no late block to muddy this
    cap = SoundDeviceCapture(cfg)
    cap.start()
    cap._frames.clear()                        # as if no callback ever fired
    audio = cap.stop()
    assert len(audio) == 0
    assert audio.dtype == np.float32


def test_device_is_named_for_the_log(fake_sd, cfg):
    import sounddevice as sd
    assert "Fake Mic via FakeAPI" in SoundDeviceCapture(cfg)._describe(sd)
