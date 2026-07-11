#!/usr/bin/env bash
# Delphi — redeploy after a code change (WS-4). Run as root/sudo ON THE SERVER.
#   APP_DIR=/opt/delphi bash deploy/update.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/delphi}"
APP_USER="${APP_USER:-delphi}"

echo "==> Pulling latest"
sudo -u "$APP_USER" bash -lc "cd '$APP_DIR' && git pull --ff-only"

echo "==> Backend deps"
sudo -u "$APP_USER" bash -lc "cd '$APP_DIR/backend' && uv sync --frozen"

echo "==> Rebuild SPA"
sudo -u "$APP_USER" bash -lc "cd '$APP_DIR/frontend' && npm ci && npm run build"

echo "==> Restart backend (in-memory sim tracking resets — avoid during a running sim)"
systemctl restart delphi
sleep 2
curl -fsS localhost:8000/health && echo "  healthy"
echo "==> Done."
