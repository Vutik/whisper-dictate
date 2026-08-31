"""Remote recogniser: WAV encoding, multipart shape, and error handling.

Runs against a throwaway HTTP server, so it needs no API key and no network.
"""
import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from whisper_dictate.backends.stt_openai_api import (OpenAICompatibleSTT,
                                                     build_multipart,
                                                     encode_wav)


# --------------------------------------------------------------------- unit

def test_encode_wav_roundtrips():
    audio = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
    with wave.open(io.BytesIO(encode_wav(audio, 16000)), "rb") as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, 16000)
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    assert pcm.tolist() == [0, 16383, -16383, 32767, -32767]


def test_encode_wav_clips_out_of_range_samples():
    audio = np.array([9.0, -9.0], dtype=np.float32)
    with wave.open(io.BytesIO(encode_wav(audio, 8000)), "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    assert pcm.tolist() == [32767, -32767]


def test_multipart_skips_none_fields():
    body, ctype = build_multipart({"model": "m", "language": None}, "a.wav", b"XX")
    assert b'name="model"' in body
    assert b'name="language"' not in body
    assert b'name="file"; filename="a.wav"' in body
    assert body.rstrip().endswith(ctype.split("boundary=")[1].encode() + b"--")


# ---------------------------------------------------------------- integration

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.seen = {
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "ctype": self.headers.get("Content-Type", ""),
            "body": body,
        }
        if self.headers.get("Authorization") != "Bearer GOOD":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"bad key"}')
            return
        out = json.dumps({"text": " hello there ", "language": "english",
                          "duration": 1.5}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.seen = None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture
def cfg(server, monkeypatch):
    monkeypatch.setenv("TEST_STT_KEY", "GOOD")
    return {
        "remote_base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
        "remote_model": "whisper-large-v3-turbo",
        "remote_api_key_env": "TEST_STT_KEY",
        "remote_timeout": 5,
        "language": "ru",
        "initial_prompt": None,
        "vocabulary": ["macOS"],
    }


def test_successful_transcription(server, cfg):
    audio = np.zeros(16000, dtype=np.float32)
    result = OpenAICompatibleSTT(cfg).transcribe(audio, 16000)

    assert result.text == "hello there"          # stripped
    assert result.language == "english"
    assert result.duration == 1.5

    seen = server.seen
    assert seen["path"] == "/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer GOOD"
    assert seen["ctype"].startswith("multipart/form-data; boundary=")
    for field in (b"model", b"response_format", b"language", b"prompt", b"file"):
        assert b'name="' + field + b'"' in seen["body"]


def test_language_is_omitted_when_autodetecting(server, cfg):
    cfg["language"] = None
    OpenAICompatibleSTT(cfg).transcribe(np.zeros(100, dtype=np.float32), 16000)
    assert b'name="language"' not in server.seen["body"]


def test_vocabulary_reaches_the_prompt(server, cfg):
    OpenAICompatibleSTT(cfg).transcribe(np.zeros(100, dtype=np.float32), 16000)
    assert b"macOS" in server.seen["body"]


def test_http_error_is_reported_with_detail(server, cfg, monkeypatch):
    monkeypatch.setenv("TEST_STT_KEY", "WRONG")
    with pytest.raises(RuntimeError, match="401"):
        OpenAICompatibleSTT(cfg).transcribe(np.zeros(100, dtype=np.float32), 16000)


def test_unreachable_service_is_reported(cfg):
    cfg["remote_base_url"] = "http://127.0.0.1:1/v1"
    with pytest.raises(RuntimeError, match="cannot reach"):
        OpenAICompatibleSTT(cfg).transcribe(np.zeros(100, dtype=np.float32), 16000)


def test_missing_key_is_reported(cfg, monkeypatch):
    monkeypatch.delenv("TEST_STT_KEY", raising=False)
    assert OpenAICompatibleSTT.is_available(cfg) is False
    with pytest.raises(RuntimeError, match="TEST_STT_KEY"):
        OpenAICompatibleSTT(cfg).transcribe(np.zeros(100, dtype=np.float32), 16000)


def test_remote_backend_holds_no_state(cfg):
    stt = OpenAICompatibleSTT(cfg)
    assert stt.state == "ready"
    stt.load()
    stt.unload(deep=True)
    assert stt.state == "ready"          # nothing local to release
