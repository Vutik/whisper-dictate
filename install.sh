#!/usr/bin/env bash
#
# whisper-dictate installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Vutik/whisper-dictate/main/install.sh | bash
#
# Three shapes:
#   ./install.sh                          local recognition on this machine
#   ./install.sh --server                 the same, plus an HTTP API for other machines
#   ./install.sh --client http://host:8760   thin client: no CUDA, no model, ~88 MB
#
# Options: --hotkey SPEC  --model NAME  --no-model  --api-key KEY
#          --api-host ADDR  --api-port N  --no-service  --dir PATH
#
# Idempotent: safe to re-run to upgrade or repair an existing install.
set -euo pipefail

REPO="${WHISPER_DICTATE_REPO:-https://github.com/Vutik/whisper-dictate.git}"
BRANCH="${WHISPER_DICTATE_BRANCH:-main}"
HOTKEY="${HOTKEY:-ctrl+alt+space}"
MODEL="${MODEL:-large-v3-turbo}"
FETCH_MODEL=1
MODE=local                       # local | server | client
SERVER_URL=""
API_KEY="${WHISPER_DICTATE_API_KEY:-}"
API_HOST=127.0.0.1
API_PORT=8760
WITH_SERVICE=1
CONFDIR="${XDG_CONFIG_HOME:-$HOME/.config}/whisper-dictate"
CONFIG="$CONFDIR/config.json"
ENVFILE="$CONFDIR/env"
UNITDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNITDIR/whisper-dictate.service"
API_UNIT="$UNITDIR/whisper-dictate-api.service"

while [ $# -gt 0 ]; do
  case "$1" in
    --hotkey) HOTKEY="$2"; shift 2 ;;
    --model)  MODEL="$2";  shift 2 ;;
    --no-model) FETCH_MODEL=0; shift ;;
    --dir)    TARGET="$2"; shift 2 ;;
    --server) MODE=server; shift ;;
    --client) MODE=client; SERVER_URL="$2"; FETCH_MODEL=0; shift 2 ;;
    --api-key)  API_KEY="$2";  shift 2 ;;
    --api-host) API_HOST="$2"; shift 2 ;;
    --api-port) API_PORT="$2"; shift 2 ;;
    --no-service) WITH_SERVICE=0; shift ;;
    -h|--help) awk 'NR>1{ if (/^#/) { sub(/^# ?/,""); print } else exit }' "$0"; exit 0 ;;
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
if [ "$MODE" = "client" ]; then
  say "installing thin-client dependencies (no CUDA, no model — about 88 MB)"
  REQ="$DIR/requirements-client.txt"
else
  say "installing python dependencies (this pulls ~2.5 GB of CUDA wheels)"
  REQ="$DIR/requirements.txt"
fi
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r "$REQ"
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
if [ "$MODE" = "client" ]; then
  say "recognition will run on $SERVER_URL"
elif [ -n "$NVIDIA_LIBS" ] && command -v nvidia-smi >/dev/null; then
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
mkdir -p "$CONFDIR"
HOTKEY="$HOTKEY" MODEL="$MODEL" MODE="$MODE" SERVER_URL="$SERVER_URL" \
API_HOST="$API_HOST" API_PORT="$API_PORT" API_KEY="$API_KEY" "$PY" - <<'PY'
import os, sys
sys.path.insert(0, os.getcwd())
from whisper_dictate import config

cfg = config.load()
mode = os.environ["MODE"]
cfg["hotkey"] = os.environ["HOTKEY"]

if mode == "client":
    cfg["backends"]["stt"] = "openai-api"
    cfg["remote_base_url"] = os.environ["SERVER_URL"].rstrip("/")
    cfg["remote_api_key_env"] = "WHISPER_DICTATE_API_KEY"
    cfg["remote_model"] = os.environ["MODEL"]
    cfg["api_enabled"] = False
else:
    cfg["backends"]["stt"] = "auto"
    cfg["model"] = os.environ["MODEL"]
    if mode == "server":
        cfg["api_enabled"] = True
        cfg["api_host"] = os.environ["API_HOST"]
        cfg["api_port"] = int(os.environ["API_PORT"])
        cfg["api_key"] = os.environ["API_KEY"] or None

config._write(cfg)
if cfg.get("api_key"):
    os.chmod(config.CONFIG_PATH, 0o600)
print(f"config: {config.CONFIG_PATH}")
PY

if [ -n "$API_KEY" ]; then
  printf 'WHISPER_DICTATE_API_KEY=%s\n' "$API_KEY" > "$ENVFILE"
  chmod 600 "$ENVFILE"
  say "api key stored in $ENVFILE (mode 600)"
fi

# --------------------------------------------------------------------------
# 6. service
# --------------------------------------------------------------------------
if [ "$WITH_SERVICE" = "0" ]; then
  say "skipping service installation (--no-service)"
else
say "installing user service"
mkdir -p "$UNITDIR"
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
EnvironmentFile=-$ENVFILE
TimeoutStopSec=10
KillMode=mixed
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

if [ "$MODE" = "server" ]; then
  say "installing standalone recognition server: $API_UNIT"
  cat > "$API_UNIT" <<UNITEOF
[Unit]
Description=whisper-dictate recognition API (OpenAI-compatible)
After=network-online.target

[Service]
Type=simple
ExecStart=$PY $DIR/dictate_api.py
Environment=LD_LIBRARY_PATH=$NVIDIA_LIBS
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-$ENVFILE
TimeoutStopSec=10
KillMode=mixed
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNITEOF
fi

systemctl --user daemon-reload
systemctl --user enable --now whisper-dictate.service
if [ "$MODE" = "server" ]; then
  systemctl --user enable --now whisper-dictate-api.service
fi
fi

# --------------------------------------------------------------------------
# 7. verify
# --------------------------------------------------------------------------
if [ "$WITH_SERVICE" = "0" ]; then
  printf '\033[1;32m✓ files installed\033[0m (no service started)\n'
  exit 0
fi

say "waiting for the daemon"
OK=0
for i in $(seq 1 60); do
  if "$DIR/dictate" ping >/dev/null 2>&1; then OK=1; break; fi
  sleep 1
done

echo
if [ "$OK" = "1" ]; then
  printf '\033[1;32m✓ done\033[0m\n'
  echo "  mode:     $MODE"
  echo "  backends: $("$DIR/dictate" backends)"
  echo "  hotkey:   $HOTKEY"
  if [ "$MODE" = "client" ]; then
    echo "  server:   $SERVER_URL"
  elif [ "$MODE" = "server" ]; then
    echo "  api:      http://$API_HOST:$API_PORT  (auth $([ -n "$API_KEY" ] && echo on || echo off))"
    echo "  clients:  ./install.sh --client http://<this-host>:$API_PORT --api-key ..."
  fi
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
