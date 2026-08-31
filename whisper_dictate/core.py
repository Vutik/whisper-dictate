"""The dictation state machine.

Deliberately free of platform detail: everything it touches arrives through
the interfaces in `interfaces.py`, so the same logic runs wherever a set of
backends exists.
"""
from __future__ import annotations

import threading
import time

import numpy as np

from .interfaces import (AudioCapture, HotkeyBinder, Notifier, PARKED, READY,
                         SoundPlayer, SpeechToText, TextInjector, TextProcessor,
                         UNLOADED)
from .log import log


class Dictator:
    IDLE, RECORDING, BUSY = "idle", "recording", "busy"

    def __init__(self, cfg: dict, *, stt: SpeechToText, audio: AudioCapture,
                 injector: TextInjector, notifier: Notifier,
                 sound: SoundPlayer, postprocessor: TextProcessor):
        self.cfg = cfg
        self.stt = stt
        self.audio = audio
        self.injector = injector
        self.notifier = notifier
        self.sound = sound
        self.postprocessor = postprocessor

        self.state = self.IDLE
        self.lock = threading.Lock()
        self.model_lock = threading.RLock()
        self.last_used = time.monotonic()
        self.watchdog: threading.Timer | None = None
        self._stop_watcher = threading.Event()

    # -- lifecycle ---------------------------------------------------------

    def start_background(self):
        threading.Thread(target=self._idle_watcher, daemon=True,
                         name="idle-watcher").start()

    def shutdown(self):
        self._stop_watcher.set()
        if self.watchdog:
            self.watchdog.cancel()
        self.notifier.close()

    def apply(self, cfg: dict, **backends) -> None:
        """Swap configuration and any rebuilt backends into the live daemon."""
        with self.lock:
            self.cfg = cfg
        for attr, obj in backends.items():
            if obj is not None:
                setattr(self, attr, obj)
        for part in (self.stt, self.audio, self.injector, self.notifier,
                     self.sound, self.postprocessor):
            part.cfg = cfg

    def ensure_loaded(self):
        with self.model_lock:
            self.last_used = time.monotonic()
            if self.stt.state == READY:
                return
            t0 = time.monotonic()
            self.stt.load()
            log(f"model ready in {time.monotonic() - t0:.2f} s")

    def _idle_watcher(self):
        cfg = self.cfg
        while not self._stop_watcher.wait(2.0):
            with self.lock:
                if self.state != self.IDLE:
                    continue
            with self.model_lock:
                idle = time.monotonic() - self.last_used
                try:
                    if (self.stt.state == READY and cfg["idle_unload_seconds"]
                            and idle >= cfg["idle_unload_seconds"]):
                        t0 = time.monotonic()
                        self.stt.unload(deep=not self.stt.supports_parking)
                        log(f"idle {idle:.0f} s → accelerator memory released "
                            f"in {time.monotonic() - t0:.2f} s")
                    elif (self.stt.state == PARKED and cfg["deep_unload_seconds"]
                            and idle >= cfg["deep_unload_seconds"]):
                        self.stt.unload(deep=True)
                        log(f"idle {idle:.0f} s → weights dropped from RAM")
                except Exception as exc:
                    log(f"idle unload failed: {exc}")

    # -- commands ----------------------------------------------------------

    def handle(self, cmd: str) -> str:
        cmd = cmd.strip().lower()
        if cmd in ("toggle", ""):
            return self.toggle()
        if cmd == "start":
            return self.start()
        if cmd == "stop":
            return self.stop()
        if cmd == "cancel":
            return self.cancel()
        if cmd == "status":
            with self.model_lock:
                return f"{self.state} (model: {self.stt.state})"
        if cmd == "backends":
            return " ".join(
                f"{k}={v.backend_name}" for k, v in (
                    ("stt", self.stt), ("audio", self.audio),
                    ("inject", self.injector), ("notify", self.notifier),
                    ("sound", self.sound), ("postprocess", self.postprocessor)))
        if cmd == "ping":
            return "pong"
        return f"error: unknown command {cmd!r}"

    def toggle(self) -> str:
        with self.lock:
            state = self.state
        if state == self.IDLE:
            return self.start()
        if state == self.RECORDING:
            return self.stop()
        self.notifier.show("🕐 Расшифровка…", "Подождите завершения", 4000,
                           close_after=4.0)
        return "busy"

    def start(self) -> str:
        with self.lock:
            if self.state != self.IDLE:
                return f"error: state is {self.state}"
            self.state = self.RECORDING
        try:
            self.audio.start()
        except Exception as exc:
            with self.lock:
                self.state = self.IDLE
            log(f"capture failed: {exc}")
            self.sound.play("error")
            self.notifier.show("⚠️ Микрофон недоступен", str(exc), 6000,
                               close_after=6.0)
            return f"error: {exc}"

        self.sound.play("start")
        # Warm the model back up while the user is still speaking, so an idle
        # unload never costs any perceptible latency.
        threading.Thread(target=self._preload, daemon=True).start()
        self.notifier.show("🎤 Запись…", "Нажмите хоткей ещё раз",
                           self.cfg["max_seconds"] * 1000)
        self.watchdog = threading.Timer(self.cfg["max_seconds"], self._timeout)
        self.watchdog.daemon = True
        self.watchdog.start()
        return "recording"

    def _preload(self):
        try:
            self.ensure_loaded()
        except Exception as exc:
            log(f"preload failed: {exc}")

    def _timeout(self):
        log("watchdog: max_seconds reached")
        self.stop()

    def cancel(self) -> str:
        with self.lock:
            if self.state != self.RECORDING:
                return f"error: state is {self.state}"
            self.state = self.BUSY
        if self.watchdog:
            self.watchdog.cancel()
        self.audio.stop()
        with self.lock:
            self.state = self.IDLE
        self.notifier.show("✖ Отменено", "", 1500, close_after=1.5)
        return "cancelled"

    def stop(self) -> str:
        with self.lock:
            if self.state != self.RECORDING:
                return f"error: state is {self.state}"
            self.state = self.BUSY
        if self.watchdog:
            self.watchdog.cancel()
        audio = self.audio.stop()
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()
        return "transcribing"

    # -- transcription -----------------------------------------------------

    def _process(self, audio: np.ndarray):
        cfg = self.cfg
        try:
            samplerate = self.audio.samplerate
            duration = len(audio) / samplerate
            if duration < cfg["min_seconds"]:
                self.notifier.show("✖ Слишком коротко", f"{duration:.1f} с",
                                   1500, close_after=1.5)
                return

            self.notifier.show("🕐 Расшифровка…", f"{duration:.1f} с", 30000)
            self.ensure_loaded()

            t0 = time.monotonic()
            transcript = self.stt.transcribe(audio, samplerate)
            elapsed = time.monotonic() - t0

            text = self.postprocessor.process(transcript)
            if not text:
                self.sound.play("error")
                self.notifier.show("✖ Ничего не распознано", "", 2000,
                                   close_after=2.0)
                return

            log(f"[{transcript.language} {transcript.language_probability:.2f}] "
                f"{duration:.1f}s audio → {elapsed:.1f}s decode: {text}")

            if cfg["append_space"]:
                text += " "
            self.injector.insert(text)
            self.sound.play("done")

            preview = text.strip()
            if len(preview) > 90:
                preview = preview[:90] + "…"
            self.notifier.show(
                f"✓ {transcript.language} · {elapsed:.1f} с", preview,
                2500, close_after=2.5)
        except Exception as exc:
            log(f"transcription failed: {exc}")
            self.sound.play("error")
            self.notifier.show("⚠️ Ошибка расшифровки", str(exc), 6000,
                               close_after=6.0)
        finally:
            self.last_used = time.monotonic()
            with self.lock:
                self.state = self.IDLE
