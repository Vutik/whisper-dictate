"""Remote recogniser speaking the OpenAI /audio/transcriptions API.

One backend covers every service that copied that shape — Groq (free tier,
whisper-large-v3-turbo), OpenAI, Mistral, and a self-hosted whisper-server —
so switching providers is a base-URL change, not new code.

Uses only the standard library: the daemon should not grow an HTTP stack for
an optional path.

Trade-offs against the local backend, in the open: the audio leaves the
machine, it needs network and an API key, and round-trip latency replaces the
0.6 s local decode. In exchange it costs no VRAM and runs on any hardware.
"""
from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np

from ..interfaces import READY, SpeechToText, Transcript
from ..log import log
from ..registry import register


def encode_wav(audio: np.ndarray, samplerate: int) -> bytes:
    """float32 mono in [-1, 1] → 16-bit PCM WAV."""
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def build_multipart(fields: dict[str, str], filename: str,
                    payload: bytes) -> tuple[bytes, str]:
    boundary = f"----whisperdictate{uuid.uuid4().hex}"
    sep = f"--{boundary}".encode()
    parts = []
    for name, value in fields.items():
        if value is None:
            continue
        parts += [sep,
                  f'Content-Disposition: form-data; name="{name}"'.encode(),
                  b"", str(value).encode()]
    parts += [sep,
              (f'Content-Disposition: form-data; name="file"; '
               f'filename="{filename}"').encode(),
              b"Content-Type: audio/wav",
              b"", payload,
              f"--{boundary}--".encode(), b""]
    return b"\r\n".join(parts), f"multipart/form-data; boundary={boundary}"


@register("stt", "openai-api", priority=10)
class OpenAICompatibleSTT(SpeechToText):
    supports_parking = False

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        return bool(os.environ.get(cfg.get("remote_api_key_env", "GROQ_API_KEY")))

    @property
    def state(self) -> str:
        return READY          # nothing is held locally

    def describe(self) -> str:
        return f"{self.backend_name}:{self.cfg.get('remote_model')}"

    def load(self) -> None:
        pass

    def unload(self, deep: bool = False) -> None:
        pass

    # ----------------------------------------------------------------------

    def transcribe(self, audio: np.ndarray, samplerate: int) -> Transcript:
        cfg = self.cfg
        key_env = cfg.get("remote_api_key_env", "GROQ_API_KEY")
        api_key = os.environ.get(key_env)
        if not api_key:
            raise RuntimeError(f"{key_env} is not set")

        base = cfg.get("remote_base_url", "https://api.groq.com/openai/v1").rstrip("/")
        url = f"{base}/audio/transcriptions"

        fields = {
            "model": cfg.get("remote_model", "whisper-large-v3-turbo"),
            "response_format": "verbose_json",
            "temperature": "0",
        }
        if cfg.get("language"):
            fields["language"] = cfg["language"]
        prompt = self._prompt()
        if prompt:
            fields["prompt"] = prompt

        body, content_type = build_multipart(
            fields, "audio.wav", encode_wav(audio, samplerate))

        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": content_type})

        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(
                    request, timeout=cfg.get("remote_timeout", 30)) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise RuntimeError(f"{exc.code} from {base}: {detail}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"cannot reach {base}: {exc.reason}") from None

        log(f"remote transcription took {time.monotonic() - t0:.2f} s")
        return Transcript(
            text=(data.get("text") or "").strip(),
            language=data.get("language", "") or "",
            language_probability=1.0,
            duration=float(data.get("duration") or len(audio) / samplerate),
        )

    def _prompt(self) -> str | None:
        parts = []
        if self.cfg.get("initial_prompt"):
            parts.append(str(self.cfg["initial_prompt"]))
        vocab = self.cfg.get("vocabulary") or []
        if vocab:
            parts.append(", ".join(vocab) + ".")
        return " ".join(parts) or None
