# Delphi — Status

**As of 2026-07-04**

**Headline:** the BYO-key public-deployment work is **code-complete and verified**;
only real-infra provisioning remains. Currently **running locally** (backend :5001,
frontend :3000).

## Workstreams

| # | Workstream | Status |
|---|------------|--------|
| WS-1 | Per-user key isolation (stateless BYO-key, fail-closed) | ✅ done + verified |
| WS-2 | Anonymous per-workspace data isolation (signed cookie) | ✅ done + verified |
| WS-3 | Compute caps: concurrency, size ceilings, reaper, rate limits | ✅ done + verified |
| WS-4 | Production packaging: gunicorn + nginx + Docker + runbook | ✅ done + verified |
| — | Launch prep: `.env.production`, bootstrap script, clean data slate | ✅ done |
| WS-6 | Irrationality modeling (traits, affect, opinion dynamics, S1/S2, probes) | ✅ Reddit runner done + tested · Twitter/parallel port pending — see `docs/irrationality-modeling.md` |

## Verified

- Server holds **zero secrets** — LLM + Zep keys are per-request, never stored.
- No-key request fails closed ("LLM not configured"), never uses a server default.
- Two browsers → two isolated workspaces; can't read/stop each other's data by ID.
- Rate-limit 429s, concurrency caps, wall-clock reaper all fire.
- Runs under gunicorn: serves API + SPA, `Secure; HttpOnly` workspace cookie.

## Left to do (operator action on real infra)

1. Provision a VPS (Hetzner) + domain.
2. Run `deploy/bootstrap.sh` (installs deps, builds, systemd + nginx + TLS).
3. Walk the post-deploy checklist in `docs/deployment.md`.

## Decisions locked

BYO **both** LLM + Zep keys · anonymous signed-session workspaces · single-worker
(low-tens scale) · Zep/LLM free tiers fine for testing (small seed docs).

## Key files

- `docs/public-deployment-plan.md` — full plan + per-WS implementation detail
- `docs/deployment.md` — deploy runbook (VPS + Docker)
- `deploy/` — `bootstrap.sh`, `update.sh`, `nginx.conf`, `delphi.service`
- `backend/.env.production` — real SECRET_KEY, gitignored
