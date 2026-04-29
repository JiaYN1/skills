#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: scripts/deploy.sh <ssh-host> <remote-dir>" >&2
  exit 1
fi

SSH_HOST="$1"
REMOTE_DIR="$2"

ssh "$SSH_HOST" "mkdir -p '$REMOTE_DIR'"

rsync -az --delete \
  --exclude '.git' \
  --exclude '.env' \
  --exclude 'node_modules' \
  --exclude 'dist' \
  --exclude 'data' \
  --exclude 'coverage' \
  ./ "$SSH_HOST:$REMOTE_DIR/"

ssh "$SSH_HOST" "cd '$REMOTE_DIR' && docker compose up -d --build"
