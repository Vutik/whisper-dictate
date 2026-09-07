"""Model load / park / drop behaviour."""
import time

from conftest import wait_idle
from whisper_dictate.core import Dictator
from whisper_dictate.interfaces import PARKED, READY, UNLOADED


def test_ensure_loaded_is_idempotent(dictator, parts):
    dictator.ensure_loaded()
    dictator.ensure_loaded()
    assert parts["stt"].loads == 1
    assert parts["stt"].state == READY


def test_idle_watcher_parks_then_drops(cfg, parts):
    cfg["idle_unload_seconds"] = 0.01
    cfg["deep_unload_seconds"] = 0.02
    d = Dictator(cfg, **parts)
    d.ensure_loaded()
    d.start_background()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and parts["stt"].unloads != [False, True]:
        time.sleep(0.05)
    assert parts["stt"].unloads == [False, True]
    assert parts["stt"].state == UNLOADED
    d.shutdown()


def test_recording_reloads_a_parked_model(dictator, parts):
    parts["stt"].unload(deep=False)
    assert parts["stt"].state == PARKED
    dictator.start()
    dictator.stop()
    assert wait_idle(dictator)
    assert parts["stt"].state == READY
    assert parts["stt"].loads >= 2


def test_idle_watcher_leaves_an_active_recording_alone(cfg, parts):
    cfg["idle_unload_seconds"] = 0.01
    d = Dictator(cfg, **parts)
    d.ensure_loaded()
    d.start_background()
    d.start()
    time.sleep(0.3)
    assert parts["stt"].unloads == []      # never unload mid-recording
    d.cancel()
    d.shutdown()


def test_zero_disables_unloading(cfg, parts):
    cfg["idle_unload_seconds"] = 0
    d = Dictator(cfg, **parts)
    d.ensure_loaded()
    d.start_background()
    time.sleep(0.3)
    assert parts["stt"].unloads == []
    d.shutdown()


def test_idle_watcher_leaves_a_remote_recogniser_alone(cfg, parts):
    """A backend holding no weights must not be "unloaded" every two seconds.

    It always reports READY and its unload() is a no-op, so the watcher used
    to fire on every pass and log a release that never happened — dozens of
    lines a minute, drowning the journal.
    """
    cfg["idle_unload_seconds"] = 0.01
    parts["stt"].holds_weights = False
    d = Dictator(cfg, **parts)
    d.ensure_loaded()
    d.start_background()
    time.sleep(0.3)
    assert parts["stt"].unloads == []
    d.shutdown()
