# whisper-dictate

Dictate text into any window — locally, offline, on your GPU. A superwhisper
equivalent for Linux.

Press the hotkey, speak, press it again, and the text lands wherever your
cursor was. Nothing leaves the machine.

---

## Install

### One command

```bash
curl -fsSL https://raw.githubusercontent.com/Vutik/whisper-dictate/main/install.sh | bash
```

That is all — the script fetches the sources, installs everything and starts
the service. Point it elsewhere with `WHISPER_DICTATE_REPO` and
`WHISPER_DICTATE_BRANCH` if you are working from a fork.

### From a checkout

```bash
git clone https://github.com/Vutik/whisper-dictate.git
cd whisper-dictate
./install.sh
```

### Three shapes

| Command | What you get |
|---|---|
| `./install.sh` | local recognition on this machine |
| `./install.sh --server --api-key SECRET --api-host 0.0.0.0` | the same, plus an HTTP API other machines can use |
| `./install.sh --client http://gpu-box:8760 --api-key SECRET` | thin client: **88 MB**, no CUDA, no model weights |

The thin client installs `numpy`, `sounddevice`, `python-xlib` and `evdev` and
nothing else — recognition happens on the server, so a laptop with no GPU gets
the same quality and speed as the machine holding the model.

### Installer options

| Flag | Default | Effect |
|---|---|---|
| `--hotkey <spec>` | `ctrl+alt+space` | combination that toggles recording |
| `--model <name>` | `large-v3-turbo` | which model to download |
| `--no-model` | — | skip the download; the daemon fetches it on first use |
| `--dir <path>` | `~/.local/share/whisper-dictate` | install location for piped installs |
| `--server` | off | also install the recognition API daemon |
| `--client <url>` | off | thin client pointed at a server |
| `--api-key <key>` | none | bearer token, stored in `~/.config/whisper-dictate/env` (mode 600) |
| `--api-host <addr>` | `127.0.0.1` | anything but loopback requires a key |
| `--api-port <n>` | `8760` | |
| `--no-service` | off | install files only, do not touch systemd |

The script is **idempotent** — re-running it upgrades or repairs an existing
install without breaking anything.

### What it does

1. Detects the session type (X11 or Wayland) and installs the matching helpers
2. Checks for an NVIDIA GPU and warns if there is none
3. Creates a virtualenv and installs dependencies (~2.5 GB of CUDA wheels)
4. Downloads the model (~1.6 GB into `~/.cache/huggingface`)
5. Installs and starts a `systemd --user` service
6. Waits for the daemon to answer and prints the selected backends

### Requirements

| | |
|---|---|
| OS | Linux, X11 or Wayland |
| Python | 3.9+ with the `venv` module |
| GPU | NVIDIA strongly preferred; CPU works but is roughly 15× slower |
| Disk | ~4.5 GB (environment plus model) |
| Autostart | `systemd --user` service, starts with your graphical session |

---

## Use

1. Put the cursor in any text field
2. **Ctrl+Alt+Space** — recording starts (sound plus a "🎤 Recording…" banner)
3. Speak; mixing languages is fine
4. **Ctrl+Alt+Space** — about a second later the text is inserted

```bash
./dictate                  # same as the hotkey
./dictate start|stop|cancel
./dictate status           # idle / recording / busy, plus model state
./dictate backends         # which implementations are in use
```

---

## Settings

Applied live — the daemon watches the config file and re-reads it within two
seconds. Even changing the model or the hotkey needs no restart.

```bash
./dictate get                    # everything
./dictate get model              # one value
./dictate set model small        # change and apply
./dictate set backends.inject x11
./dictate edit                   # open in $EDITOR
./dictate reload                 # force a re-read
```

File: `~/.config/whisper-dictate/config.json`.

### Recognition

| Key | Default | Notes |
|---|---|---|
| `model` | `large-v3-turbo` | `large-v3` is more accurate and twice as slow; `medium`, `small`, `base`, `tiny` are lighter and worse |
| `device` | `cuda` | `cpu` works, but takes ~10 s per phrase instead of 0.6 s |
| `compute_type` | `int8_float16` | 1.1 GB VRAM. `float16` costs 2.2 GB and is no faster |
| `language` | `null` | autodetect, which handles mixed-language speech. Pin with `"ru"`, `"en"`, … |
| `beam_size` | `5` | higher is more accurate and slower |
| `vad_filter` | `true` | strip silence before decoding |
| `initial_prompt` | `null` | free-form hint for the decoder |
| `no_speech_threshold` | `0.9` | drop segments this likely to be silence |

### Remote recognition (optional)

**This is the path to take when the machine has no NVIDIA card.** A second
recogniser talks to any service that copies the OpenAI `/audio/transcriptions`
API — Groq's free tier, OpenAI, Mistral, or a self-hosted `whisper-server`. It
holds no VRAM, downloads no weights and runs on any hardware, including a
laptop with integrated graphics only. The price is that the audio leaves the
machine and network latency replaces the local decode.

#### Getting a key

Groq is the free option, and the default the config already points at.

1. Open **console.groq.com** and sign in with Google or GitHub.
2. Go to **API Keys** → **Create API Key**.
3. Copy it — it looks like `gsk_…` and is shown exactly once.

No card is asked for; the free tier is rate-limited per minute rather than
billed. Any other provider works the same way once `remote_base_url` and
`remote_api_key_env` point at it.

#### Wiring it in

A `systemd --user` service does not read your shell profile, so exporting the
key in `~/.profile` has no effect on the daemon. Give it an environment file
and a drop-in instead, and it survives every restart:

```bash
printf 'GROQ_API_KEY=gsk_…\n' > ~/.config/whisper-dictate/env
chmod 600 ~/.config/whisper-dictate/env

mkdir -p ~/.config/systemd/user/whisper-dictate.service.d
printf '[Service]\nEnvironmentFile=-%%h/.config/whisper-dictate/env\n' \
  > ~/.config/systemd/user/whisper-dictate.service.d/override.conf

systemctl --user daemon-reload
systemctl --user restart whisper-dictate     # the key is read at process start
./dictate set backends.stt openai-api
./dictate backends                           # expect stt=openai-api
```

Order matters: restart first, then switch. Switching earlier leaves the
running daemon without the key in its environment, the backend reports itself
unavailable, and the log says `reload: keeping current stt`.

| Key | Default | Notes |
|---|---|---|
| `remote_base_url` | `https://api.groq.com/openai/v1` | any OpenAI-compatible endpoint |
| `remote_model` | `whisper-large-v3-turbo` | provider's model name |
| `remote_api_key_env` | `GROQ_API_KEY` | environment variable holding the key |
| `remote_timeout` | `30` | seconds |

The local backend keeps a much higher priority, so `auto` never picks the
remote one by accident — selecting it is always explicit. Back to local:
`./dictate set backends.stt auto`.

If every request comes back as `403 … error code: 1010`, the key is fine and
the CDN in front of the provider is refusing the client: that is what a
missing or default `User-Agent` earns. This backend sends its own.

### Serving recognition to other machines

Off by default. The daemon can expose its loaded model over an
OpenAI-compatible endpoint, and there is a separate daemon
(`dictate_api.py`, unit `whisper-dictate-api.service`) that serves it without
a microphone, hotkey or clipboard — for a headless GPU box.

```bash
./dictate set api_enabled true
./dictate set api_key "$(openssl rand -hex 16)"
./dictate set api_host 0.0.0.0          # refused unless api_key is set
```

| Key | Default | Notes |
|---|---|---|
| `api_enabled` | `false` | must be turned on deliberately |
| `api_host` | `127.0.0.1` | binding anywhere else without `api_key` is refused |
| `api_port` | `8760` | |
| `api_key` | `null` | bearer token |
| `api_max_upload_mb` | `25` | rejects larger uploads with 413 |

Endpoints:

```
GET  /health                     no auth; reports model state
GET  /v1/models
POST /v1/audio/transcriptions    multipart: file, language?, prompt?, response_format?
```

```bash
curl -s http://gpu-box:8760/v1/audio/transcriptions \
  -H "Authorization: Bearer $KEY" \
  -F file=@speech.wav -F response_format=verbose_json
```

Any OpenAI-compatible client works, this project's own `openai-api` backend
included. WAV is decoded by the standard library; mp3, m4a, ogg and webm go
through PyAV when the local recogniser is installed.

**It is plain HTTP, with no TLS.** The bearer token crosses the network in
clear, so treat it as safe only on a network you trust. For anything else, put
it behind an SSH tunnel (`ssh -L 8760:localhost:8760 gpu-box`, then point the
client at `127.0.0.1`), a WireGuard/Tailscale link, or a TLS-terminating
reverse proxy.

### Memory

| Key | Default | Notes |
|---|---|---|
| `idle_unload_seconds` | `30` | idle time before VRAM is released. Coming back takes 0.15 s and is hidden behind the start of recording. `0` disables |
| `deep_unload_seconds` | `900` | idle time before the weights leave RAM too. Coming back takes 0.75 s. `0` disables |

Measured: 1.1 GB VRAM while working, **110 MiB** after 30 s idle, ~690 MB RSS
after 15 minutes.

### Capture

| Key | Default | Notes |
|---|---|---|
| `samplerate` | `16000` | Whisper works at 16 kHz regardless |
| `input_device` | `null` | system default microphone; accepts a name or index |
| `max_seconds` | `300` | recording stops by itself |
| `min_seconds` | `0.35` | anything shorter counts as a stray keypress |
| `tail_seconds` | `0.25` | keep capturing this long after the hotkey; `0` cuts immediately |

The tail exists because the hotkey is pressed *as* the last word ends, and
PortAudio hands over whole blocks: whatever is still in flight at that instant
reaches no callback. Measured on the developer's machine, stopping without a
tail keeps 35 ms past the keypress; with the default it keeps 291 ms. If the
last word of a dictation is sometimes clipped, raise this before suspecting
the recogniser.

### Hotkey

| Key | Default | Notes |
|---|---|---|
| `hotkey` | `ctrl+alt+space` | modifiers `ctrl`, `alt`, `shift`, `super` |
| `hotkey_grab` | `true` | `false` leaves only `./dictate` |

`dictate set hotkey` checks the combination against the desktop's own
shortcuts and refuses to take one that is already used (override with
`--force`). That check earns its keep: an X11 grab of an occupied combination
*succeeds*, yet GNOME still acts on it — Ctrl+Alt+D would dictate and minimise
every window at the same time.

### Text insertion

| Key | Default | Notes |
|---|---|---|
| `insert_method` | `paste` | clipboard plus Ctrl+V. `type` synthesises keystrokes, `clipboard` only copies |
| `type_delay_ms` | `4` | inter-key delay for `type` |
| `append_space` | `true` | trailing space, so dictating in sequence reads naturally |
| `restore_clipboard` | `true` | put the previous clipboard back after 0.6 s |
| `terminal_classes` | 12 entries | windows where paste is Ctrl+**Shift**+V |

`paste` is the default for a reason: with a non-Latin keyboard layout active,
synthesised typing on X11 produces garbage, while the clipboard path does not
depend on the layout at all. This appears to be exactly where the off-the-shelf
alternatives fall over.

### Technical terms in non-English speech

Whisper transliterates technical words — Russian dictation turns "macOS" into
"мохоз". Two mechanisms fix it:

| Key | Purpose |
|---|---|
| `vocabulary` | 53 correct spellings, fed to the decoder as `initial_prompt` and `hotwords` — prevents the problem |
| `use_hotwords` | whether to pass the vocabulary as `hotwords` |
| `replacements` | 51 whole-word, case-insensitive rules — repairs whatever slipped through |
| `hallucination_phrases` | phrases Whisper invents over silence; dropped when they are the entire result |

```
Открой сеттингс и нажми аксепт    →  Открой settings и нажми accept
Отправь пул реквест в гитхаб      →  Отправь pull request в GitHub
```

Extend with `./dictate edit` or `./dictate set replacements '{...}'`.

### Feedback

| Key | Default |
|---|---|
| `notifications` | `true` |
| `sounds` | `true` |
| `sound_start` · `sound_done` · `sound_error` | files under `/usr/share/sounds/freedesktop/stereo/` |

### Choosing implementations

| Key | Default |
|---|---|
| `backends.stt` · `audio` · `inject` · `hotkey` · `notify` · `sound` · `postprocess` | `auto` |

`auto` means every registered implementation is asked whether it works here,
and the highest-priority usable one wins. Nothing needs configuring by hand —
moving from X11 to Wayland switches text insertion on its own. Pin one with
`./dictate set backends.inject x11`.

---

## Platform support

```bash
./dictate backends                                  # what is in use
.venv/bin/python dictate_daemon.py --list-backends  # what exists
```

| | X11 | Wayland (GNOME) | macOS |
|---|---|---|---|
| Recognition | ✅ CUDA | ✅ CUDA | ⚠️ CPU only |
| Microphone | ✅ | ✅ | ✅ |
| Text insertion | ✅ `xdotool` + `xclip` | ⚠️ `wl-copy` + `ydotool` | ❌ backend needed |
| Hotkey | ✅ `XGrabKey` | ⚠️ `evdev` | ❌ backend needed |
| Notifications | ✅ DBus | ✅ DBus | ❌ backend needed |

### Wayland prerequisites

Wayland deliberately denies applications both key grabbing and key synthesis,
so both go through the kernel:

```bash
sudo usermod -aG input $USER     # read /dev/input for the hotkey; needs re-login
sudo apt install ydotool          # synthesise Ctrl+V through /dev/uinput
```

Caveats worth knowing up front:

- `evdev` does not **consume** the key — the focused application receives it
  too. Pick a combination applications ignore.
- The `input` group grants access to the entire keyboard stream. That is a real
  weakening of isolation; decide deliberately.
- `wtype` is useless on GNOME: Mutter does not implement
  `zwp_virtual_keyboard_v1`.
- Without `ydotool` everything still works — the text is copied and you paste
  it yourself.
- The `GlobalShortcuts` portal, the clean answer, needs
  xdg-desktop-portal ≥ 1.17; Ubuntu 22.04 ships 1.14.

---

## Architecture

Everything platform-specific sits behind an interface; the core imports
neither X11, nor CUDA, nor DBus. Implementations register themselves.

```
whisper_dictate/
  interfaces.py   SpeechToText · AudioCapture · TextInjector · HotkeyBinder
                  Notifier · SoundPlayer · TextProcessor
  registry.py     @register(kind, name, priority) and "auto" resolution
  core.py         the dictation state machine, platform-independent
  server.py       dependency wiring, control socket, live config reload
  backends/       stt_faster_whisper · audio_sounddevice
                  inject_x11 · inject_wayland
                  hotkey_x11 · hotkey_evdev
                  notify_dbus · notify_libnotify · sound_paplay · feedback_null
                  postprocess_rules
```

### Supporting a new system

Drop a file into `backends/`; nothing else changes:

```python
@register("inject", "macos", priority=120)
class MacInjector(TextInjector):
    @classmethod
    def is_available(cls, cfg):
        return sys.platform == "darwin"

    def insert(self, text):
        subprocess.run(["pbcopy"], input=text.encode())
        subprocess.run(["osascript", "-e",
                        'tell app "System Events" to keystroke "v" using command down'])
```

The module is imported automatically, and `is_available()` keeps it from
winning off its own platform. A module whose dependencies are missing is
skipped with a log line rather than taking the daemon down.

---

## Tests

```bash
pip install numpy pytest
pytest
```

123 tests, ~9 seconds. They need **no GPU, microphone, display server or
network**: the interfaces let fakes stand in for every platform backend, so
the state machine is exercised directly.

| File | Covers |
|---|---|
| `test_core.py` | the dictation state machine: full cycle, toggle, cancel, races, and every failure path |
| `test_lifecycle.py` | model load / park / drop, and that an active recording is never unloaded |
| `test_registry.py` | priority ordering, `is_available` filtering, explicit pinning, error messages |
| `test_config.py` | defaults, merging, per-key `backends` merge, corrupt files |
| `test_postprocess.py` | term restoration, whole-word matching, hallucination filtering |
| `test_remote_stt.py` | WAV encoding, multipart shape and error handling, against a throwaway HTTP server |
| `test_injectors.py` | paste-vs-type routing and terminal detection, with subprocess replaced |
| `test_hotkey_spec.py` | hotkey parsing and its error reporting |
| `test_capture.py` | the trailing capture window, with PortAudio replaced by a fake |
| `test_diagnose.py` | classifying a captured buffer, so an empty result names its cause |

CI runs them on Python 3.9–3.13 (`.github/workflows/tests.yml`).

## Troubleshooting

```bash
systemctl --user status whisper-dictate
journalctl --user -u whisper-dictate -f     # shows recognised text and timings
./dictate ping
```

A dictation that produces no text names its own cause, in the log and in the
notification. The three silent failures look alike from the outside and are
told apart by the level of the captured waveform, which every one of these
lines carries as `peak`/`rms`:

| What you see | What it means |
|---|---|
| `Microphone is silent` | every sample is zero — this is not a real capture device. Usually the sound card fell out of PipeWire: check `pactl list short sources`, and if the only entry is `auto_null.monitor`, run `systemctl --user restart wireplumber` **and then restart this daemon**, because PortAudio caches the device list at process start |
| `Microphone almost silent` | a signal path with nothing on it: `pactl get-source-mute @DEFAULT_SOURCE@` and `pactl get-source-volume @DEFAULT_SOURCE@` |
| `Microphone produced no audio` | the stream opened but delivered zero blocks — the device disappeared mid-recording |
| `Nothing recognised` | the audio was audible and the recogniser still returned no words; the line quotes the raw transcript before post-processing |

| Symptom | Where to look |
|---|---|
| Hotkey does nothing | the `hotkey: grabbed` line in the log |
| Text recognised but not inserted | the `insert failed via …` line, which carries `xdotool`'s own stderr; then `./dictate get insert_method` |
| Wrong device picked | the `audio: capturing from …` line, logged whenever the device changes; `./dictate set input_device "..."` |
| Choppy audio | `audio: PortAudio reported N× …` — blocks were dropped |
| Slow first run | model load, ~2 s; afterwards it stays resident |
| GPU memory occupied | `systemctl --user stop whisper-dictate`, or `./dictate set idle_unload_seconds 10` |

---

## Uninstall

```bash
./uninstall.sh                                  # the service
rm -rf ~/.cache/huggingface/hub/*whisper*       # models
rm -rf ~/.config/whisper-dictate                # settings
```
