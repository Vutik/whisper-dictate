#!/usr/bin/env bash
# Removes the user service. Models and settings are left in place.
set -uo pipefail
systemctl --user disable --now whisper-dictate.service 2>/dev/null
rm -f "$HOME/.config/systemd/user/whisper-dictate.service"
systemctl --user daemon-reload
echo "service removed."
echo "  models:   rm -rf ~/.cache/huggingface/hub/*whisper*"
echo "  settings: rm -rf ~/.config/whisper-dictate"
