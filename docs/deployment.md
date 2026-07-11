# Delphi — Production Deployment (WS-4)

Delphi ships as a **public BYO-key tool**: every visitor brings their own LLM +
Zep keys (per request, never stored), gets an anonymous per-browser workspace,
and the operator pays only for hosting compute. This doc covers running it.

## Architecture in production

```
            HTTPS                      127.0.0.1:8000
 browser ──────────► nginx (TLS) ──────────────────► gunicorn ──► Flask app
   │  delphi_ws cookie   │  static SPA (dist/)          1 worker      │
   │  X-LLM-*, X-Zep-*   │  proxy /api, /health         N threads     ├─ sim subprocesses
                                                                       └─ reaper thread
```

**Why a single gunicorn worker?** Running-simulation tracking, the rate limiter,
and the reaper are per-process in-memory state. One worker keeps them
authoritative; threads (default 16) provide concurrency. See
`backend/gunicorn.conf.py`. Scaling past one worker requires externalizing that
state (Redis + a task queue) — deliberately deferred.

## Required environment

Put these in `backend/.env.production` (loaded by systemd or compose):

| Var | Required | Notes |
|-----|----------|-------|
| `SECRET_KEY` | **yes** | Strong random string. Signs the workspace cookie — a default/weak value lets cookies be forged. `python -c "import secrets;print(secrets.token_hex(32))"` |
| `FLASK_DEBUG` | yes | `false` in production. |
| `COOKIE_SECURE` | yes | `true` once behind HTTPS (cookie sent only over TLS). |
| `TRUST_PROXY` | behind nginx | `true` (default) so client IP/scheme come from `X-Forwarded-*`. |
| `MAX_CONCURRENT_SIMULATIONS` | no | Global cap (default 4) — tune to your box's CPU/RAM. |
| `MAX_CONCURRENT_SIMULATIONS_PER_WORKSPACE` | no | Default 1. |
| `MAX_SIMULATION_ROUNDS` / `MAX_SIMULATION_AGENTS` | no | Compute ceilings (default 50/50). |
| `SIMULATION_MAX_WALLCLOCK_SECONDS` | no | Reaper timeout (default 3600). |
| `RATELIMIT_*` | no | Request-rate caps (see `config.py`). |
| `GUNICORN_THREADS` / `GUNICORN_TIMEOUT` | no | Defaults 16 / 300s. |

The server needs **no** `LLM_API_KEY` / `ZEP_API_KEY` — those are bring-your-own.

> `Config.validate()` refuses to start (via `run.py`) in non-debug mode if
> `SECRET_KEY` is still the default.

---

## Path A — bare VPS (Hetzner etc.): systemd + nginx

**Automated:** on a fresh Ubuntu 22.04/24.04 box, `deploy/bootstrap.sh` does all of
the steps below (deps, service user, build, `.env.production` with a fresh
`SECRET_KEY`, systemd, nginx, Let's Encrypt):

```bash
sudo REPO=<git-url> DOMAIN=delphi.example.com EMAIL=you@example.com bash deploy/bootstrap.sh
# redeploy after changes:
sudo bash deploy/update.sh
```

The manual equivalent, step by step:


```bash
# 0. System deps: python3.11, nodejs>=18, nginx, certbot, uv
# 1. Get the code
sudo git clone <repo> /opt/delphi && cd /opt/delphi

# 2. Backend deps (creates /opt/delphi/backend/.venv)
cd backend && uv sync --frozen && cd ..

# 3. Build the SPA (uses .env.production -> same-origin API)
cd frontend && npm ci && npm run build && cd ..
#    -> serves from /opt/delphi/frontend/dist
#    Point nginx `root` there, or copy to /var/www/delphi/dist.

# 4. Config
sudo -u delphi tee backend/.env.production >/dev/null <<EOF
SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))")
FLASK_DEBUG=false
COOKIE_SECURE=true
TRUST_PROXY=true
EOF

# 5. gunicorn under systemd
sudo cp deploy/delphi.service /etc/systemd/system/delphi.service
#    (edit User/paths/EnvironmentFile to match)
sudo systemctl daemon-reload && sudo systemctl enable --now delphi
curl -s localhost:8000/health    # {"status":"ok",...}

# 6. nginx + TLS
sudo cp deploy/nginx.conf /etc/nginx/sites-available/delphi
#    (edit server_name + root to /opt/delphi/frontend/dist or /var/www/delphi/dist)
sudo ln -s /etc/nginx/sites-available/delphi /etc/nginx/sites-enabled/
sudo certbot --nginx -d delphi.example.com   # fills in the ssl_certificate lines
sudo nginx -t && sudo systemctl reload nginx
```

Update: `git pull`, `uv sync --frozen`, rebuild SPA, `systemctl restart delphi`.

---

## Path B — Docker (single container, API + SPA)

```bash
echo "SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_hex(32))')" > backend/.env.production
echo "FLASK_DEBUG=false" >> backend/.env.production

COOKIE_SECURE=true docker compose -f docker-compose.prod.yml up -d --build
curl -s localhost:8000/health
```

This container serves everything on `:8000` over plain HTTP. For a public deploy
put a TLS terminator (nginx/Caddy or your platform) in front and set
`COOKIE_SECURE=true`. Per-workspace data persists in the `backend/uploads` volume.

---

## Post-deploy checklist

- [ ] `SECRET_KEY` is strong & unique; `COOKIE_SECURE=true`; `FLASK_DEBUG=false`.
- [ ] Open the site in two different browsers → two isolated workspaces (WS-2).
- [ ] Paste an LLM key + Zep key in Settings; run a seed-chat / graph build.
- [ ] Confirm the `delphi_ws` cookie is `Secure; HttpOnly` in devtools.
- [ ] Hammer an endpoint to see a `429` (WS-3 rate limit).
- [ ] Back up `backend/uploads/workspaces/` (all user data lives there).

## Notes / limits

- Pre-existing single-tenant data under `uploads/{projects,simulations,reports}`
  (from local dev) is orphaned — not served. Only `uploads/workspaces/<id>/…` is.
- Single worker: don't run multiple gunicorn workers without externalizing
  run-state + limiter to Redis (would split in-memory state).
