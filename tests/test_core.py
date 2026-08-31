"""The dictation state machine, exercised with no hardware at all."""
import threading

import pytest

from conftest import wait_idle
from whisper_dictate.core import Dictator


def test_full_cycle_inserts_text(dictator, parts):
    assert dictator.handle("status").startswith("idle")

    assert dictator.start() == "recording"
    assert parts["audio"].started == 1
    assert dictator.handle("status").startswith("recording")

    assert dictator.stop() == "transcribing"
    assert wait_idle(dictator)

    assert parts["injector"].inserted == ["привет "]      # append_space
    assert parts["sound"].played == ["start", "done"]


def test_toggle_alternates(dictator, parts):
    assert dictator.toggle() == "recording"
    assert dictator.toggle() == "transcribing"
    assert wait_idle(dictator)
    assert len(parts["injector"].inserted) == 1


def test_toggle_while_busy_is_refused(dictator):
    with dictator.lock:
        dictator.state = Dictator.BUSY
    assert dictator.toggle() == "busy"
    with dictator.lock:
        dictator.state = Dictator.IDLE


def test_cancel_discards_audio(dictator, parts):
    dictator.start()
    assert dictator.cancel() == "cancelled"
    assert dictator.state == Dictator.IDLE
    assert parts["injector"].inserted == []
    assert parts["stt"].calls == []


def test_cancel_when_idle_is_an_error(dictator):
    assert dictator.cancel().startswith("error")


def test_start_twice_is_an_error(dictator):
    dictator.start()
    assert dictator.start().startswith("error")
    dictator.cancel()


def test_too_short_recording_is_dropped(cfg, parts):
    parts["audio"].seconds = 0.1                 # below min_seconds
    d = Dictator(cfg, **parts)
    d.ensure_loaded()
    d.start()
    d.stop()
    assert wait_idle(d)
    assert parts["injector"].inserted == []
    assert any("Too short" in t for t, _ in parts["notifier"].shown)
    d.shutdown()


def test_append_space_can_be_disabled(cfg, parts):
    cfg["append_space"] = False
    d = Dictator(cfg, **parts)
    d.ensure_loaded()
    d.start(); d.stop()
    assert wait_idle(d)
    assert parts["injector"].inserted == ["привет"]
    d.shutdown()


def test_microphone_failure_is_reported_and_recovers(cfg, parts):
    parts["audio"].fail_on_start = OSError("no such device")
    d = Dictator(cfg, **parts)
    d.ensure_loaded()
    assert d.start().startswith("error")
    assert d.state == Dictator.IDLE          # not wedged in RECORDING
    assert any("Microphone" in t for t, _ in parts["notifier"].shown)
    d.shutdown()


def test_transcription_failure_returns_to_idle(dictator, parts):
    parts["stt"].fail_with = RuntimeError("boom")
    dictator.start()
    dictator.stop()
    assert wait_idle(dictator)
    assert parts["injector"].inserted == []
    assert any("failed" in t.lower() for t, _ in parts["notifier"].shown)
    assert dictator.state == Dictator.IDLE


def test_empty_transcript_inserts_nothing(dictator, parts):
    parts["stt"].text = "   "
    dictator.start(); dictator.stop()
    assert wait_idle(dictator)
    assert parts["injector"].inserted == []
    assert any("Nothing" in t for t, _ in parts["notifier"].shown)


def test_unknown_command(dictator):
    assert dictator.handle("nonsense").startswith("error: unknown command")


def test_ping_and_backends(dictator):
    assert dictator.handle("ping") == "pong"
    assert "stt=fake-stt" in dictator.handle("backends")


def test_apply_swaps_backends_live(dictator, parts, cfg):
    from conftest import FakeInjector
    replacement = FakeInjector(cfg)
    dictator.apply(dict(cfg, append_space=False), injector=replacement)
    dictator.start(); dictator.stop()
    assert wait_idle(dictator)
    assert replacement.inserted == ["привет"]
    assert parts["injector"].inserted == []


def test_concurrent_toggles_do_not_double_start(dictator, parts):
    """Two hotkey presses racing must not open two recordings."""
    results = []
    barrier = threading.Barrier(2)

    def press():
        barrier.wait()
        results.append(dictator.toggle())

    threads = [threading.Thread(target=press) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert parts["audio"].started == 1
    assert sorted(results) == ["recording", "transcribing"]
    assert wait_idle(dictator)
    assert len(parts["injector"].inserted) == 1
