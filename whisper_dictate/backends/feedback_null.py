"""No-op feedback, for headless runs and tests."""
from __future__ import annotations

from ..interfaces import Notifier, SoundPlayer
from ..registry import register


@register("notify", "none", priority=-100)
class NullNotifier(Notifier):
    def show(self, title: str, body: str = "", timeout_ms: int = 4000,
             close_after: float | None = None) -> None:
        pass

    def close(self) -> None:
        pass


@register("sound", "none", priority=-100)
class NullSound(SoundPlayer):
    def play(self, event: str) -> None:
        pass
