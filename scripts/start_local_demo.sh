#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v ollama >/dev/null && command -v systemctl >/dev/null; then
  mkdir -p "$HOME/.config/systemd/user"
  cp deploy/local/zhiyi-ollama.service "$HOME/.config/systemd/user/zhiyi-ollama.service"
  systemctl --user daemon-reload
  systemctl --user enable --now zhiyi-ollama.service
fi
docker compose --env-file deploy/local-demo.env \
  -f docker-compose.yml -f docker-compose.local-demo.yml \
  up -d --build redis api worker web
printf '%s\n' 'Zhiyi: http://localhost:5174/test-client' \
  'API health: http://localhost:8008/api/health'
