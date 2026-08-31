#!/usr/bin/env bash
set -uo pipefail
systemctl --user disable --now whisper-dictate.service 2>/dev/null
rm -f "$HOME/.config/systemd/user/whisper-dictate.service"
systemctl --user daemon-reload
echo "сервис удалён. модели остались в ~/.cache/huggingface (удалить: rm -rf ~/.cache/huggingface/hub/*whisper*)"
