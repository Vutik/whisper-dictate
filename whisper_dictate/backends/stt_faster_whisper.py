"""faster-whisper / CTranslate2 recogniser."""
from __future__ import annotations

import numpy as np

from ..interfaces import PARKED, READY, SpeechToText, Transcript, UNLOADED
from ..log import log
from ..registry import register


@register("stt", "faster-whisper", priority=100)
class FasterWhisperSTT(SpeechToText):
    supports_parking = True

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._model = None
        self._state = UNLOADED

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def state(self) -> str:
        return self._state

    def describe(self) -> str:
        return (f"{self.backend_name}:{self.cfg['model']}"
                f"/{self.cfg['compute_type']}@{self.cfg['device']}")

    # -- lifecycle ---------------------------------------------------------

    def load(self) -> None:
        if self._state == READY:
            return
        if self._model is not None:
            # Weights parked in RAM, or dropped but the object still knows
            # where to read them from — CTranslate2 restores either way.
            self._model.model.load_model()
            self._state = READY
            return

        from faster_whisper import WhisperModel
        cfg = self.cfg
        log(f"loading {cfg['model']} on {cfg['device']} ({cfg['compute_type']})…")
        try:
            self._model = WhisperModel(cfg["model"], device=cfg["device"],
                                       compute_type=cfg["compute_type"])
        except Exception as exc:
            log(f"{cfg['device']} load failed ({exc}); falling back to CPU int8")
            self._model = WhisperModel(cfg["model"], device="cpu",
                                       compute_type="int8", cpu_threads=8)
        self._state = READY

    def unload(self, deep: bool = False) -> None:
        if self._model is None or self._state == UNLOADED:
            return
        if self._state == PARKED and not deep:
            return
        self._model.model.unload_model(to_cpu=not deep)
        self._state = UNLOADED if deep else PARKED

    # -- inference ---------------------------------------------------------

    def _prompt(self) -> str | None:
        parts = []
        if self.cfg.get("initial_prompt"):
            parts.append(str(self.cfg["initial_prompt"]))
        vocab = self.cfg.get("vocabulary") or []
        if vocab:
            # Seeding the decoder with the exact spellings makes Whisper emit
            # "macOS" rather than transliterating it into Cyrillic.
            parts.append("Термины: " + ", ".join(vocab) + ".")
        return " ".join(parts) or None

    def transcribe(self, audio: np.ndarray, samplerate: int) -> Transcript:
        cfg = self.cfg
        kwargs = dict(
            language=cfg["language"],
            beam_size=cfg["beam_size"],
            vad_filter=cfg["vad_filter"],
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            initial_prompt=self._prompt(),
        )
        hotwords = cfg.get("vocabulary") or []
        if hotwords and cfg.get("use_hotwords", True):
            try:
                segments, info = self._model.transcribe(
                    audio, hotwords=" ".join(hotwords), **kwargs)
            except TypeError:      # older faster-whisper without hotwords
                segments, info = self._model.transcribe(audio, **kwargs)
        else:
            segments, info = self._model.transcribe(audio, **kwargs)

        parts = []
        for seg in segments:
            if seg.no_speech_prob > cfg["no_speech_threshold"]:
                continue
            chunk = seg.text.strip()
            if chunk:
                parts.append(chunk)

        return Transcript(
            text=" ".join(parts).strip(),
            language=info.language,
            language_probability=info.language_probability,
            duration=len(audio) / samplerate,
        )
