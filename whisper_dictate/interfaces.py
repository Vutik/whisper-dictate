"""Contracts every platform backend implements.

The core state machine talks only to these. Supporting a new platform means
adding a module under `backends/` that registers an implementation — no edit
to `core.py` is required.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

import numpy as np


# --------------------------------------------------------------------------
# speech to text
# --------------------------------------------------------------------------

READY, PARKED, UNLOADED = "ready", "parked", "unloaded"


@dataclass
class Transcript:
    text: str
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0
    extra: dict = field(default_factory=dict)


class SpeechToText(ABC):
    """A recogniser whose weights can be loaded and unloaded on demand."""

    #: set by @register
    backend_name: str = "?"

    #: True when the backend can park weights outside the accelerator cheaply
    supports_parking: bool = False

    #: True when the recogniser holds weights that idle unloading can free.
    #: A remote one holds nothing, and the idle watcher must leave it be
    #: rather than "release" it every couple of seconds, forever.
    holds_weights: bool = True

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        return True

    @property
    @abstractmethod
    def state(self) -> str:
        """One of READY / PARKED / UNLOADED."""

    @abstractmethod
    def load(self) -> None:
        """Make the model ready to transcribe. Must be idempotent."""

    @abstractmethod
    def unload(self, deep: bool = False) -> None:
        """Release accelerator memory. ``deep`` also releases host memory."""

    @abstractmethod
    def transcribe(self, audio: np.ndarray, samplerate: int) -> Transcript:
        ...

    def describe(self) -> str:
        return self.backend_name


# --------------------------------------------------------------------------
# audio capture
# --------------------------------------------------------------------------

class AudioCapture(ABC):
    backend_name: str = "?"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        return True

    @property
    @abstractmethod
    def samplerate(self) -> int:
        ...

    @abstractmethod
    def start(self) -> None:
        ...

    @abstractmethod
    def stop(self) -> np.ndarray:
        """Stop and return mono float32 samples."""

    @property
    @abstractmethod
    def elapsed(self) -> float:
        ...


# --------------------------------------------------------------------------
# text injection
# --------------------------------------------------------------------------

class TextInjector(ABC):
    backend_name: str = "?"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        return True

    @abstractmethod
    def insert(self, text: str) -> None:
        """Deliver text to whatever currently has keyboard focus."""


# --------------------------------------------------------------------------
# global hotkey
# --------------------------------------------------------------------------

class HotkeyBinder(ABC):
    backend_name: str = "?"

    def __init__(self, cfg: dict, spec: str, callback: Callable[[], None]):
        self.cfg = cfg
        self.spec = spec
        self.callback = callback
        self.error: str | None = None

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        return True

    @abstractmethod
    def start(self) -> None:
        """Begin listening. Should set ``self.error`` instead of raising."""

    @abstractmethod
    def stop(self) -> None:
        ...


# --------------------------------------------------------------------------
# user feedback
# --------------------------------------------------------------------------

class Notifier(ABC):
    backend_name: str = "?"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        return True

    @abstractmethod
    def show(self, title: str, body: str = "", timeout_ms: int = 4000,
             close_after: float | None = None) -> None:
        """Show or replace the single status banner."""

    @abstractmethod
    def close(self) -> None:
        """Dismiss the banner immediately."""


class SoundPlayer(ABC):
    backend_name: str = "?"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        return True

    @abstractmethod
    def play(self, event: str) -> None:
        """``event`` is one of start / done / error."""


# --------------------------------------------------------------------------
# text post-processing
# --------------------------------------------------------------------------

class TextProcessor(ABC):
    """Cleans up a raw transcript before it is injected.

    Platform-independent, but pluggable for the same reason the rest is: a
    rule table today, an LLM rewriter tomorrow, without touching the core.
    """

    backend_name: str = "?"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        return True

    @abstractmethod
    def process(self, transcript: Transcript) -> str:
        """Return the final text, or "" to insert nothing."""
