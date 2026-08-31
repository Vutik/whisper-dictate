"""Optional HTTP server exposing the loaded model to other machines.

Speaks the OpenAI /v1/audio/transcriptions shape, so anything that already
talks to Whisper-as-a-service — including this project's own `openai-api`
backend — can point at it unchanged.

Off by default. When on, it binds to loopback unless a key is configured:
handing an unauthenticated GPU endpoint to the local network should be a
deliberate act, not a default.
"""
from __future__ import annotations

import hmac
import io
import json
import re
import socket
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from . import APP, __version__
from .log import log

LOOPBACK = ("127.0.0.1", "::1", "localhost")


# --------------------------------------------------------------------------
# request parsing
# --------------------------------------------------------------------------

def parse_multipart(body: bytes, boundary: bytes) -> dict[str, tuple[str | None, bytes]]:
    """Minimal multipart/form-data reader.

    Hand-rolled because `cgi` was removed in Python 3.13 and the alternatives
    are heavier than the ~30 lines this needs.
    """
    fields: dict[str, tuple[str | None, bytes]] = {}
    marker = b"--" + boundary
    for chunk in body.split(marker):
        if chunk in (b"", b"--", b"--\r\n") or b"\r\n\r\n" not in chunk:
            continue
        raw_headers, _, payload = chunk.partition(b"\r\n\r\n")
        disposition = ""
        for line in raw_headers.split(b"\r\n"):
            if line.lower().startswith(b"content-disposition:"):
                disposition = line.decode("utf-8", "replace")
        name = re.search(r'name="([^"]*)"', disposition)
        if not name:
            continue
        filename = re.search(r'filename="([^"]*)"', disposition)
        fields[name.group(1)] = (filename.group(1) if filename else None,
                                 payload[:-2] if payload.endswith(b"\r\n") else payload)
    return fields


def decode_audio(data: bytes, samplerate: int) -> np.ndarray:
    """Uploaded bytes → mono float32 at ``samplerate``.

    PyAV (a faster-whisper dependency) handles mp3/m4a/ogg/webm and resamples;
    plain WAV is read with the standard library so the endpoint still works
    when only a remote recogniser is installed.
    """
    try:
        from faster_whisper.audio import decode_audio as _decode
        return _decode(io.BytesIO(data), sampling_rate=samplerate)
    except Exception:
        pass

    with wave.open(io.BytesIO(data), "rb") as wf:
        channels, width, rate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError(f"unsupported WAV sample width: {width * 8} bit")
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != samplerate:                      # crude, but only a fallback
        idx = np.round(np.arange(0, len(audio), rate / samplerate)).astype(int)
        audio = audio[idx[idx < len(audio)]]
    return audio


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = f"{APP}/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt, *args):
        log(f"api: {self.address_string()} {fmt % args}")

    def _send(self, code: int, obj: dict):
        payload = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, code: int, message: str):
        # Rejecting a POST leaves its body unread on a keep-alive connection,
        # and the next parse would choke on the leftover multipart bytes.
        self.close_connection = True
        self._send(code, {"error": {"message": message, "type": "invalid_request_error"}})

    def _authorised(self) -> bool:
        key = self.server.api_key
        if not key:
            return True
        header = self.headers.get("Authorization", "")
        offered = header[7:] if header.startswith("Bearer ") else ""
        return hmac.compare_digest(offered, key)

    # -- routes ------------------------------------------------------------

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/v1/health"):
            state = self.server.engine.stt.state
            self._send(200, {"status": "ok", "model_state": state,
                             "version": __version__})
        elif path == "/v1/models":
            if not self._authorised():
                return self._error(401, "invalid api key")
            name = self.server.engine.cfg.get("model", "whisper")
            self._send(200, {"object": "list",
                             "data": [{"id": name, "object": "model",
                                       "owned_by": APP}]})
        else:
            self._error(404, f"unknown path {path}")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/v1/audio/transcriptions", "/audio/transcriptions"):
            return self._error(404, f"unknown path {path}")
        if not self._authorised():
            return self._error(401, "invalid api key")

        ctype = self.headers.get("Content-Type", "")
        match = re.search(r"boundary=([^;]+)", ctype)
        if not ctype.startswith("multipart/form-data") or not match:
            return self._error(400, "expected multipart/form-data with a boundary")

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._error(400, "empty request body")
        if length > self.server.max_upload:
            return self._error(413, f"upload exceeds {self.server.max_upload} bytes")

        boundary = match.group(1).strip('"').encode()
        fields = parse_multipart(self.rfile.read(length), boundary)
        if "file" not in fields:
            return self._error(400, 'missing "file" field')

        def text_field(name):
            item = fields.get(name)
            return item[1].decode("utf-8", "replace").strip() if item else None

        try:
            self.server.transcribe(self, fields["file"][1],
                                   language=text_field("language"),
                                   prompt=text_field("prompt"),
                                   fmt=text_field("response_format") or "json")
        except Exception as exc:                       # noqa: BLE001
            log(f"api: transcription failed: {exc}")
            self._error(500, str(exc))


class TranscriptionAPI:
    """Serves the daemon's loaded model over HTTP."""

    def __init__(self, cfg: dict, engine):
        self.cfg = cfg
        self.engine = engine
        self.httpd: ThreadingHTTPServer | None = None
        self.error: str | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        cfg = self.cfg
        host = cfg.get("api_host", "127.0.0.1")
        port = int(cfg.get("api_port", 8760))
        key = cfg.get("api_key") or None

        if host not in LOOPBACK and not key:
            self.error = (f"refusing to listen on {host} without api_key — "
                          f"set one, or bind to 127.0.0.1")
            log(f"api: {self.error}")
            return False

        try:
            httpd = ThreadingHTTPServer((host, port), _Handler)
        except OSError as exc:
            self.error = f"cannot bind {host}:{port}: {exc}"
            log(f"api: {self.error}")
            return False

        httpd.daemon_threads = True
        httpd.engine = self.engine
        httpd.api_key = key
        httpd.max_upload = int(cfg.get("api_max_upload_mb", 25)) * 1024 * 1024
        httpd.transcribe = self._transcribe
        self.httpd = httpd

        threading.Thread(target=httpd.serve_forever, daemon=True,
                         name="http-api").start()
        reach = "loopback only" if host in LOOPBACK else f"reachable at {self._lan_hint()}"
        log(f"api: listening on http://{host}:{port} "
            f"({reach}, auth {'on' if key else 'off'})")
        return True

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            log("api: stopped")

    @staticmethod
    def _lan_hint() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            addr = s.getsockname()[0]
            s.close()
            return addr
        except Exception:
            return "this host"

    # -- work --------------------------------------------------------------

    def _transcribe(self, handler, payload: bytes, language, prompt, fmt):
        import time

        engine = self.engine
        samplerate = engine.samplerate
        audio = decode_audio(payload, samplerate)
        duration = len(audio) / samplerate

        # One model, one GPU: serialise with local dictation rather than
        # letting a network request race the user's own hotkey.
        with engine.model_lock:
            engine.ensure_loaded()
            overrides = {}
            if language:
                overrides["language"] = language
            if prompt:
                overrides["initial_prompt"] = prompt
            previous = engine.stt.cfg
            if overrides:
                engine.stt.cfg = dict(previous, **overrides)
            t0 = time.monotonic()
            try:
                transcript = engine.stt.transcribe(audio, samplerate)
            finally:
                engine.stt.cfg = previous
            elapsed = time.monotonic() - t0
            engine.last_used = time.monotonic()

        text = engine.postprocessor.process(transcript)
        log(f"api: {duration:.1f}s audio → {elapsed:.1f}s decode "
            f"[{transcript.language}]: {text[:60]}")

        if fmt == "text":
            body = text.encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            return

        result = {"text": text}
        if fmt == "verbose_json":
            result.update(task="transcribe", language=transcript.language,
                          duration=round(duration, 3))
        handler._send(200, result)
