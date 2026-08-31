#!/usr/bin/env bash
#
# whisper-dictate installer.
#
#   curl -fsSL <raw-url>/install.sh | bash
#   ./install.sh [--hotkey ctrl+alt+space] [--model large-v3-turbo] [--no-model]
#
# Idempotent: safe to re-run to upgrade or repair an existing install.
set -euo pipefail

REPO="${WHISPER_DICTATE_REPO:-}"
BRANCH="${WHISPER_DICTATE_BRANCH:-main}"
HOTKEY="${HOTKEY:-ctrl+alt+space}"
MODEL="${MODEL:-large-v3-turbo}"
FETCH_MODEL=1
UNIT="$HOME/.config/systemd/user/whisper-dictate.service"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/whisper-dictate/config.json"

while [ $# -gt 0 ]; do
  case "$1" in
    --hotkey) HOTKEY="$2"; shift 2 ;;
    --model)  MODEL="$2";  shift 2 ;;
    --no-model) FETCH_MODEL=0; shift ;;
    --dir)    TARGET="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m /!\\\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------
# 0. locate sources — running from a checkout, or piped from the web
# --------------------------------------------------------------------------
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
  DIR=""
fi

if [ -z "$DIR" ] || [ ! -d "$DIR/whisper_dictate" ]; then
  TARGET="${TARGET:-$HOME/.local/share/whisper-dictate}"
  [ -n "$REPO" ] || die "piped install needs a source: WHISPER_DICTATE_REPO=<git url> or run from a checkout"
  command -v git >/dev/null || die "git is required for a piped install"
  say "fetching sources into $TARGET"
  if [ -d "$TARGET/.git" ]; then
    git -C "$TARGET" fetch --depth 1 origin "$BRANCH"
    git -C "$TARGET" reset --hard "origin/$BRANCH"
  else
    mkdir -p "$(dirname "$TARGET")"
    git clone --depth 1 --branch "$BRANCH" "$REPO" "$TARGET"
  fi
  DIR="$TARGET"
fi
cd "$DIR"
say "installing from $DIR"

# --------------------------------------------------------------------------
# 1. platform checks
# --------------------------------------------------------------------------
[ "$(uname -s)" = "Linux" ] || die "this installer targets Linux (macOS needs its own backends)"
SESSION="${XDG_SESSION_TYPE:-unknown}"
say "session type: $SESSION"

PYBIN=""
for c in /usr/bin/python3 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
    PYBIN="$(command -v "$c")"; break
  fi
done
[ -n "$PYBIN" ] || die "python3 >= 3.9 not found"
"$PYBIN" -c 'import venv' 2>/dev/null || die "python venv module missing (apt install python3-venv)"

# --------------------------------------------------------------------------
# 2. system packages
# --------------------------------------------------------------------------
NEED=()
case "$SESSION" in
  wayland) for b in wl-copy;               do command -v $b >/dev/null || NEED+=("$b"); done ;;
  *)       for b in xdotool xclip xprop;   do command -v $b >/dev/null || NEED+=("$b"); done ;;
esac
for b in notify-send paplay gdbus; do command -v $b >/dev/null || NEED+=("$b"); done

if [ ${#NEED[@]} -gt 0 ]; then
  say "missing helpers: ${NEED[*]}"
  if command -v apt-get >/dev/null; then
    PKGS=(libnotify-bin pulseaudio-utils libglib2.0-bin)
    case "$SESSION" in
      wayland) PKGS+=(wl-clipboard) ;;
      *)       PKGS+=(xdotool xclip x11-utils) ;;
    esac
    sudo apt-get update -qq
    sudo apt-get install -y "${PKGS[@]}"
  else
    die "install these yourself, then re-run: ${NEED[*]}"
  fi
fi

# --------------------------------------------------------------------------
# 3. python environment
# --------------------------------------------------------------------------
PY="$DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  say "creating virtualenv"
  "$PYBIN" -m venv "$DIR/.venv"
fi
say "installing python dependencies (this pulls ~2.5 GB of CUDA wheels)"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r "$DIR/requirements.txt"
"$PY" -m pip cache purge >/dev/null 2>&1 || true

# ctranslate2 finds cuDNN/cuBLAS through the pip-installed nvidia wheels.
NVIDIA_LIBS="$("$PY" - <<'PY'
import glob, os, site
paths = []
for base in site.getsitepackages():
    paths += glob.glob(os.path.join(base, "nvidia", "*", "lib"))
print(os.pathsep.join(sorted(set(paths))))
PY
)"
if [ -n "$NVIDIA_LIBS" ] && command -v nvidia-smi >/dev/null; then
  say "NVIDIA GPU detected — using CUDA"
else
  warn "no NVIDIA GPU found; the daemon will fall back to CPU (much slower)"
fi

# --------------------------------------------------------------------------
# 4. model
# --------------------------------------------------------------------------
if [ "$FETCH_MODEL" = "1" ]; then
  say "fetching model $MODEL (cached in ~/.cache/huggingface)"
  MODEL="$MODEL" LD_LIBRARY_PATH="$NVIDIA_LIBS" "$PY" - <<'PY'
import os
from faster_whisper.utils import download_model
path = download_model(os.environ["MODEL"])
print(f"model cached at {path}")
PY
fi

# --------------------------------------------------------------------------
# 5. configuration
# --------------------------------------------------------------------------
mkdir -p "$(dirname "$CONFIG")"
HOTKEY="$HOTKEY" MODEL="$MODEL" CONFIG="$CONFIG" "$PY" - <<'PY'
import json, os, sys
sys.path.insert(0, os.getcwd())
from whisper_dictate import config
cfg = config.load()
cfg["hotkey"] = os.environ["HOTKEY"]
cfg["model"] = os.environ["MODEL"]
config._write(cfg)
print(f"config: {config.CONFIG_PATH}")
PY

# --------------------------------------------------------------------------
# 6. service
# --------------------------------------------------------------------------
say "installing user service"
mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<EOF
[Unit]
Description=whisper-dictate (resident speech-to-text daemon)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=$PY $DIR/dictate_daemon.py
Environment=DISPLAY=${DISPLAY:-:0}
Environment=XAUTHORITY=%t/gdm/Xauthority
Environment=LD_LIBRARY_PATH=$NVIDIA_LIBS
Environment=PYTHONUNBUFFERED=1
TimeoutStopSec=10
KillMode=mixed
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now whisper-dictate.service

# --------------------------------------------------------------------------
# 7. verify
# --------------------------------------------------------------------------
say "waiting for the daemon"
OK=0
for i in $(seq 1 60); do
  if "$DIR/dictate" ping >/dev/null 2>&1; then OK=1; break; fi
  sleep 1
done

echo
if [ "$OK" = "1" ]; then
  printf '\033[1;32m✓ done\033[0m\n'
  echo "  backends: $("$DIR/dictate" backends)"
  echo "  hotkey:   $HOTKEY"
else
  warn "daemon did not answer — check: journalctl --user -u whisper-dictate -n 50"
fi

if [ "$SESSION" = "wayland" ]; then
  echo
  warn "Wayland: grabbing a hotkey needs access to /dev/input"
  if ! id -nG | tr ' ' '\n' | grep -qx input; then
    echo "     sudo usermod -aG input $USER   # then log out and back in"
  fi
  command -v ydotool >/dev/null || \
    echo "     without ydotool the text is only copied — you paste it yourself"
fi

echo
echo "  ./dictate            toggle recording (same as the hotkey)"
echo "  ./dictate get        show settings"
echo "  ./dictate set K V    change a setting, applied live"
