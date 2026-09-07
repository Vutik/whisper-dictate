"""Defaults and config-file handling."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import APP
from .log import log

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP
CONFIG_PATH = CONFIG_DIR / "config.json"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
SOCKET_PATH = RUNTIME_DIR / f"{APP}.sock"

DEFAULTS = {
    # Which implementation to use for each replaceable part. "auto" picks the
    # best one that reports itself usable on this machine.
    "backends": {
        "stt": "auto",
        "audio": "auto",
        "inject": "auto",
        "hotkey": "auto",
        "notify": "auto",
        "sound": "auto",
        "postprocess": "auto",
    },

    # --- model ---
    "model": "large-v3-turbo",
    "device": "cuda",
    "compute_type": "int8_float16",
    "language": None,                 # None = autodetect, good for ru/en mixing
    "beam_size": 5,
    "vad_filter": True,
    "initial_prompt": None,
    "no_speech_threshold": 0.9,

    # Remote recogniser (backends.stt = "openai-api"). Works with any service
    # that copies the OpenAI /audio/transcriptions shape.
    "remote_base_url": "https://api.groq.com/openai/v1",
    "remote_model": "whisper-large-v3-turbo",
    "remote_api_key_env": "GROQ_API_KEY",
    "remote_timeout": 30,

    # Idle handling: park the weights outside VRAM (0.25 s to come back), then
    # drop them from RAM after a longer idle. 0 disables either stage.
    "idle_unload_seconds": 30,
    "deep_unload_seconds": 900,

    # --- HTTP API (off by default; see dictate_api.py for a standalone daemon) ---
    "api_enabled": False,
    "api_host": "127.0.0.1",          # anything else demands api_key
    "api_port": 8760,
    "api_key": None,                  # bearer token; required to leave loopback
    "api_max_upload_mb": 25,

    # --- capture ---
    "samplerate": 16000,
    "input_device": None,             # None = system default source
    "max_seconds": 300,
    "min_seconds": 0.35,
    # People press the hotkey as they finish the last word, so the closing
    # syllable is still in the air when the stream would otherwise shut. Keep
    # capturing this long past the request. 0 restores the abrupt cut.
    "tail_seconds": 0.25,

    # --- hotkey ---
    "hotkey": "ctrl+alt+space",
    "hotkey_grab": True,

    # --- output ---
    "insert_method": "paste",         # paste | type | clipboard
    "type_delay_ms": 4,
    "append_space": True,
    "restore_clipboard": True,
    "terminal_classes": [
        "gnome-terminal-server", "Alacritty", "kitty", "konsole", "xterm",
        "terminator", "Tilix", "org.wezfurlong.wezterm", "st-256color",
        "URxvt", "guake", "xfce4-terminal",
    ],

    # --- feedback ---
    "notifications": True,
    "sounds": True,
    "sound_start": "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga",
    "sound_done": "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "sound_error": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",

    # Exact spellings fed to the decoder as a prompt (and as hotwords), so
    # English technical terms come out in Latin instead of transliterated.
    "vocabulary": [
        "macOS", "Windows", "Linux", "Ubuntu", "Android", "iOS",
        "settings", "accept", "reject", "approve", "assign", "review",
        "commit", "merge", "rebase", "branch", "pull request", "issue",
        "deploy", "build", "release", "rollback", "staging", "production",
        "backend", "frontend", "endpoint", "request", "response",
        "API", "JSON", "YAML", "SQL", "HTML", "CSS", "HTTP",
        "Docker", "Kubernetes", "Git", "GitHub", "Python", "JavaScript",
        "TypeScript", "Rust", "timeout", "cache", "token", "thread",
        "debug", "refactor", "feature", "bugfix", "merge request",
    ],
    "use_hotwords": True,

    # Safety net for terms the prompt did not save. Keys are matched as whole
    # words, case-insensitively; add your own freely.
    "replacements": {
        "мохоз": "macOS", "макос": "macOS", "мак ос": "macOS",
        "мак-ос": "macOS", "макось": "macOS", "макоси": "macOS",
        "виндовс": "Windows", "виндоус": "Windows",
        "линукс": "Linux", "линекс": "Linux",
        "убунту": "Ubuntu",
        "сеттингс": "settings", "сэттингс": "settings",
        "сеттинги": "settings", "сэттинги": "settings",
        "аксепт": "accept", "эксепт": "accept",
        "реджект": "reject", "риджект": "reject",
        "апрув": "approve", "аппрув": "approve", "эпрув": "approve",
        "ассайн": "assign", "асайн": "assign", "эссайн": "assign",
        "мёрдж": "merge", "мердж": "merge",
        "пул реквест": "pull request", "пулл реквест": "pull request",
        "гитхаб": "GitHub", "гит хаб": "GitHub",
        "macos": "macOS", "mac os": "macOS",
        "windows": "Windows", "linux": "Linux", "ubuntu": "Ubuntu",
        "github": "GitHub", "gitlab": "GitLab", "docker": "Docker",
        "kubernetes": "Kubernetes", "python": "Python",
        "javascript": "JavaScript", "typescript": "TypeScript",
        "json": "JSON", "yaml": "YAML", "api": "API", "sql": "SQL",
        "html": "HTML", "css": "CSS", "http": "HTTP", "https": "HTTPS",
    },

    # Whisper likes to invent these over silence; drop them when they are the
    # entire transcript.
    "hallucination_phrases": [
        "продолжение следует",
        "субтитры сделал dimatorzok",
        "субтитры делал dimatorzok",
        "субтитры и перевод",
        "редактор субтитров",
        "спасибо за просмотр",
        "подписывайтесь на канал",
        "thanks for watching",
        "thank you for watching",
        "please subscribe",
    ],
}


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text())
        except Exception as exc:
            log(f"config: ignoring {CONFIG_PATH}: {exc}")
            stored = {}
        backends = dict(cfg["backends"], **(stored.get("backends") or {}))
        cfg.update(stored)
        cfg["backends"] = backends
        missing = [k for k in DEFAULTS if k not in stored]
        if missing:
            _write(cfg)
            log(f"config: added new keys {missing}")
    else:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _write(cfg)
        log(f"config: wrote defaults to {CONFIG_PATH}")
    return cfg


def _write(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
