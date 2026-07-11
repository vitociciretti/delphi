#!/usr/bin/env bash
# Delphi — one-shot provisioner for a fresh Ubuntu 22.04/24.04 VPS (WS-4).
# Idempotent: safe to re-run. Run as root (or with sudo) ON THE SERVER.
#
#   DOMAIN=delphi.example.com EMAIL=you@example.com bash deploy/bootstrap.sh
#
# What it does: installs system deps, creates a service user, builds the app,
# writes .env.production (fresh SECRET_KEY) if missing, installs the systemd
# unit + nginx site, and obtains a Let's Encrypt cert.
set -euo pipefail

DOMAIN="${DOMAIN:?set DOMAIN=your.domain}"
EMAIL="${EMAIL:?set EMAIL=you@example.com for Lets Encrypt}"
APP_DIR="${APP_DIR:-/opt/delphi}"
APP_USER="${APP_USER:-delphi}"
REPO="${REPO:-}"   # optional: git URL to clone if APP_DIR doesn't exist yet

log() { echo -e "\n\033[1;36m==> $*\033[0m"; }

log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  git curl ca-certificates nginx python3 python3-venv nodejs npm certbot python3-certbot-nginx

log "Installing uv (Python package manager)"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

log "Service user: $APP_USER"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"

log "Source at $APP_DIR"
if [ ! -d "$APP_DIR/.git" ] && [ -n "$REPO" ]; then
  git clone "$REPO" "$APP_DIR"
fi
[ -d "$APP_DIR/backend" ] || { echo "ERROR: $APP_DIR/backend not found. Set REPO=... or place the code there."; exit 1; }
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

log "Backend deps (uv sync)"
sudo -u "$APP_USER" bash -lc "cd '$APP_DIR/backend' && uv sync --frozen"

log "Frontend build (production, same-origin API)"
sudo -u "$APP_USER" bash -lc "cd '$APP_DIR/frontend' && npm ci && npm run build"

log "Config: $APP_DIR/backend/.env.production"
ENV_FILE="$APP_DIR/backend/.env.production"
if [ ! -f "$ENV_FILE" ]; then
  KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  cat > "$ENV_FILE" <<EOF
SECRET_KEY=$KEY
FLASK_DEBUG=false
COOKIE_SECURE=true
TRUST_PROXY=true
EOF
  chown "$APP_USER:$APP_USER" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "  generated a fresh SECRET_KEY"
else
  echo "  keeping existing $ENV_FILE"
fi

log "systemd unit"
sed -e "s#/opt/delphi#$APP_DIR#g" -e "s/^User=.*/User=$APP_USER/" -e "s/^Group=.*/Group=$APP_USER/" \
  "$APP_DIR/deploy/delphi.service" > /etc/systemd/system/delphi.service
systemctl daemon-reload
systemctl enable --now delphi
sleep 2
curl -fsS localhost:8000/health && echo "  backend healthy"

log "nginx site for $DOMAIN"
SITE=/etc/nginx/sites-available/delphi
sed -e "s/delphi.example.com/$DOMAIN/g" \
    -e "s#/var/www/delphi/dist#$APP_DIR/frontend/dist#g" \
  "$APP_DIR/deploy/nginx.conf" > "$SITE"
ln -sf "$SITE" /etc/nginx/sites-enabled/delphi
mkdir -p /var/www/certbot
nginx -t && systemctl reload nginx

log "TLS via Let's Encrypt"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect
nginx -t && systemctl reload nginx

log "Done. https://$DOMAIN is live."
echo "Post-deploy: open in two browsers (isolated workspaces), paste an LLM+Zep key, run a graph build."
