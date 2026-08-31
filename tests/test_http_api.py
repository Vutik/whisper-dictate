"""The recognition API: parsing, routing, auth and its refusal to be careless."""
import io
import json
import threading
import urllib.error
import urllib.request
import wave

import numpy as np
import pytest

from whisper_dictate.backends.stt_openai_api import build_multipart, encode_wav
from whisper_dictate.core import ModelHost
from whisper_dictate.http_api import (TranscriptionAPI, decode_audio,
                                      parse_multipart)


# ------------------------------------------------------------------ parsing

def test_parse_multipart_reads_fields_and_file():
    body, ctype = build_multipart({"model": "m", "language": "ru"},
                                  "a.wav", b"AUDIOBYTES")
    boundary = ctype.split("boundary=")[1].encode()
    fields = parse_multipart(body, boundary)

    assert fields["model"] == (None, b"m")
    assert fields["language"] == (None, b"ru")
    assert fields["file"] == ("a.wav", b"AUDIOBYTES")


def test_parse_multipart_preserves_binary_payloads():
    payload = bytes(range(256))
    body, ctype = build_multipart({}, "x.wav", payload)
    fields = parse_multipart(body, ctype.split("boundary=")[1].encode())
    assert fields["file"][1] == payload


def test_parse_multipart_ignores_junk():
    assert parse_multipart(b"not multipart at all", b"xyz") == {}


def test_decode_audio_reads_plain_wav():
    original = np.array([0.0, 0.5, -0.5], dtype=np.float32)
    audio = decode_audio(encode_wav(original, 16000), 16000)
    assert len(audio) == 3
    assert np.allclose(audio, original, atol=1e-4)


def test_decode_audio_downmixes_stereo():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(np.array([16384, -16384, 0, 0], dtype="<i2").tobytes())
    audio = decode_audio(buf.getvalue(), 16000)
    assert len(audio) == 2
    assert abs(audio[0]) < 1e-6          # +0.5 and -0.5 average to zero


# ------------------------------------------------------------------- server

class StubSTT:
    backend_name = "stub"
    state = "ready"

    def __init__(self, cfg):
        self.cfg = cfg
        self.seen = []

    def load(self):
        pass

    def unload(self, deep=False):
        pass

    def transcribe(self, audio, samplerate):
        from whisper_dictate.interfaces import Transcript
        self.seen.append(dict(self.cfg))
        return Transcript(text="recognised", language="en",
                          language_probability=1.0,
                          duration=len(audio) / samplerate)


class StubPost:
    def __init__(self, cfg):
        self.cfg = cfg

    def process(self, transcript):
        return transcript.text


@pytest.fixture
def api(request):
    from whisper_dictate import config
    cfg = dict(config.DEFAULTS)
    cfg.update(getattr(request, "param", {}) or {})
    cfg["api_port"] = 0                    # let the OS choose
    engine = ModelHost(cfg, stt=StubSTT(cfg), postprocessor=StubPost(cfg))
    server = TranscriptionAPI(cfg, engine)
    assert server.start(), server.error
    server.port = server.httpd.server_address[1]
    server.engine_stt = engine.stt
    yield server
    server.stop()


def call(api, path, method="GET", key=None, fields=None, audio=None):
    url = f"http://127.0.0.1:{api.port}{path}"
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = None
    if method == "POST":
        body, ctype = build_multipart(fields or {}, "a.wav",
                                      encode_wav(audio, 16000))
        data, headers["Content-Type"] = body, ctype
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


@pytest.fixture
def tone():
    return np.zeros(16000, dtype=np.float32)


def test_health_needs_no_key(api):
    status, body = call(api, "/health")
    assert status == 200
    assert json.loads(body)["status"] == "ok"


def test_transcription_round_trip(api, tone):
    status, body = call(api, "/v1/audio/transcriptions", "POST", audio=tone)
    assert status == 200
    assert json.loads(body) == {"text": "recognised"}


def test_verbose_json_adds_metadata(api, tone):
    _, body = call(api, "/v1/audio/transcriptions", "POST", audio=tone,
                   fields={"response_format": "verbose_json"})
    payload = json.loads(body)
    assert payload["language"] == "en"
    assert payload["duration"] == pytest.approx(1.0)


def test_text_format_returns_plain_text(api, tone):
    _, body = call(api, "/v1/audio/transcriptions", "POST", audio=tone,
                   fields={"response_format": "text"})
    assert body == b"recognised"


def test_request_overrides_do_not_leak_into_config(api, tone):
    call(api, "/v1/audio/transcriptions", "POST", audio=tone,
         fields={"language": "de", "prompt": "hint"})
    assert api.engine_stt.seen[-1]["language"] == "de"
    assert api.engine_stt.cfg["language"] == api.cfg["language"]   # restored


def test_unknown_path_is_404(api):
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(api, "/nope")
    assert exc.value.code == 404


def test_missing_file_field_is_400(api):
    with pytest.raises(urllib.error.HTTPError) as exc:
        url = f"http://127.0.0.1:{api.port}/v1/audio/transcriptions"
        body, ctype = build_multipart({"model": "m"}, "ignored", b"")
        body = body.replace(b'name="file"', b'name="notfile"')
        urllib.request.urlopen(
            urllib.request.Request(url, data=body, method="POST",
                                   headers={"Content-Type": ctype}), timeout=10)
    assert exc.value.code == 400


@pytest.mark.parametrize("api", [{"api_key": "s3cret"}], indirect=True)
def test_key_is_enforced(api, tone):
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(api, "/v1/audio/transcriptions", "POST", audio=tone, key="wrong")
    assert exc.value.code == 401

    status, _ = call(api, "/v1/audio/transcriptions", "POST", audio=tone,
                     key="s3cret")
    assert status == 200


@pytest.mark.parametrize("api", [{"api_key": "s3cret"}], indirect=True)
def test_rejected_request_does_not_poison_the_connection(api, tone):
    """A 401 leaves the body unread; the next request must still parse."""
    for _ in range(3):
        with pytest.raises(urllib.error.HTTPError):
            call(api, "/v1/audio/transcriptions", "POST", audio=tone, key="no")
    status, _ = call(api, "/v1/audio/transcriptions", "POST", audio=tone,
                     key="s3cret")
    assert status == 200


@pytest.mark.parametrize("api", [{"api_max_upload_mb": 0}], indirect=True)
def test_oversized_upload_is_refused(api, tone):
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(api, "/v1/audio/transcriptions", "POST", audio=tone)
    assert exc.value.code == 413


def test_refuses_to_leave_loopback_without_a_key():
    from whisper_dictate import config
    cfg = dict(config.DEFAULTS, api_host="0.0.0.0", api_key=None, api_port=0)
    engine = ModelHost(cfg, stt=StubSTT(cfg), postprocessor=StubPost(cfg))
    server = TranscriptionAPI(cfg, engine)
    assert server.start() is False
    assert "api_key" in server.error


def test_binding_a_busy_port_reports_cleanly(api):
    from whisper_dictate import config
    cfg = dict(config.DEFAULTS, api_host="127.0.0.1", api_port=api.port)
    engine = ModelHost(cfg, stt=StubSTT(cfg), postprocessor=StubPost(cfg))
    second = TranscriptionAPI(cfg, engine)
    assert second.start() is False
    assert "cannot bind" in second.error
