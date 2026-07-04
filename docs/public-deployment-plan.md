# Delphi — Public BYO-Key Deployment Plan

Turning Delphi from a single-tenant personal tool into the public, bring-your-own-key
multi-user site described in the deployment decision.

**Status:** planning. Nothing here is implemented yet.

## Guiding principle

Delphi custodies **nothing** it doesn't have to. The user brings their LLM key; the
operator pays only for hosting compute. Every design choice below defaults to the
option that minimizes what the server stores and what it can be liable for.

---

## Key findings from the current code (why this plan is shaped this way)

1. **Global-key design is baked into subprocess launch.** The simulation runs as a
   `subprocess.Popen` (`simulation_runner.py:438`) whose env is `os.environ.copy()`
   (line 432). Today that carries the one global `LLM_API_KEY`. Per-user isolation
   means injecting the *request's* key into *that subprocess's* env only.

2. **Zep Cloud is operator-funded.** `GraphBuilderService(api_key=Config.ZEP_API_KEY)`
   (`graph.py:471,662,690`) uses the operator's Zep key on user traffic. BYO-key covers
   the LLM but not the memory graph → an unbounded operator cost. **Must be resolved
   before public launch** (see WS-2).

3. **The scarce resource is server compute, not tokens.** Users pay their own tokens;
   the operator's exposure is CPU/RAM from concurrent simulation subprocesses. Rate
   limiting is about capping concurrent subprocesses, not token spend.

4. **Simulation state is in-memory and per-process.** `SimulationRunner._processes` etc.
   are class-level dicts. Multi-worker gunicorn breaks cross-worker visibility/stop.

---

## WS-1 — Per-user key isolation  ✅ IMPLEMENTED (Jul 4 2026)

Done: `utils/llm_creds.py` (LlmCreds + `creds_from_request()` + subprocess env
helpers); stateless `llm_settings.py`/`settings.py` (save+persist removed, startup
hook removed); every LLM/Zep site now takes per-request creds — sync routes
(seed chat/draft, ontology, graph data/delete, entities, generate-profiles, report
chat), background threads (graph build, report generate, prepare), and the sim
subprocess (parent LLM/Zep vars stripped, request creds injected into the child env;
Zep memory updater takes the request key). Frontend keeps keys in `sessionStorage`
and an axios interceptor sends `X-LLM-*`/`X-Zep-Api-Key` per request; SettingsModal
gained a Zep-key field and saves locally. **Fail-closed verified**: a request with no
creds raises "LLM not configured" instead of falling back to the operator's `.env`.
Smoke-tested via Flask test client (creds parse, subprocess env, child-env stripping,
route-level fail-closed, removed save endpoint → 404). Not yet done: WS-2/3/4.

### Original design (for reference)

**Decision: stateless / client-held key.** The browser holds the key
(`sessionStorage`), and sends it per request. The server never persists it to disk and
never mutates global env. Chosen over a server-side encrypted store because it removes
key-custody liability entirely — the whole point of BYO-key.

Changes:
- **Frontend:** settings panel stores `{provider, api_key, base_url, model}` in
  `sessionStorage` (not server). An axios request interceptor attaches them as headers
  (`X-LLM-Api-Key`, `X-LLM-Base-Url`, `X-LLM-Model`, `X-LLM-Provider`) on every `/api/*`
  call. `sessionStorage` (not `localStorage`) so the key dies with the tab.
- **Backend request context:** a small helper `llm_creds_from_request()` reads those
  headers and returns a per-request creds object. Synchronous LLM paths
  (seed-assistant, ontology generation) build `LLMClient(**creds)` from it — no global
  mutation.
- **Delete the global mutation path.** Remove `apply_settings()` writing to
  `os.environ`/`Config`, remove `apply_persisted_on_startup()` (`__init__.py:67`), and
  remove `llm_settings.json` persistence. `POST /api/settings/llm` becomes a stateless
  connectivity test only (the existing `/llm/test`); the panel no longer "saves" server-side.
- **Simulation subprocess:** capture creds at `POST /simulation/start` time. Build the
  child env as `os.environ.copy()` **minus** any inherited LLM vars, **plus** this
  request's creds. The key lives only in that child's environment for its lifetime.
- **Config-file leak check:** ensure the generated simulation config file
  (`--config config_path`) does **not** embed the key on disk. If it currently does,
  move the key strictly to env and scrub it from the written config.

Risks / notes:
- Key is readable via `/proc/<pid>/environ` by the same OS user. Acceptable on a
  single-tenant VPS; document it. Do not log the env.
- Long-running sim holds the key in memory for its duration — unavoidable and fine.

---

## WS-2 — Auth, per-user data isolation, Zep cost  *(blocks launch)*

Currently all projects/graphs/simulations/reports live under a single global
`UPLOAD_FOLDER` with no user scoping.

- **Identity:** lightweight accounts (email+password or OAuth) OR anonymous
  signed-session tokens. Recommendation: start with anonymous per-session workspaces
  (a signed cookie → a `workspace_id`), add real accounts only if persistence-across-
  devices is wanted. Keeps friction low, matches the stateless-key ethos.
- **Namespacing:** every filesystem path and every lookup gets a `workspace_id` prefix:
  `uploads/<workspace_id>/projects/...`. Enforce it in one place (a path helper) so no
  route can read another workspace's data.
- **Zep isolation + cost (the real blocker):** pick one —
  - (a) **BYO Zep key too** — cleanest for cost, more onboarding friction (user needs a
    second key). Extend the creds object to carry `zep_api_key`.
  - (b) **Operator Zep key behind hard quotas** — simpler UX, but you eat the cost;
    only viable with strict per-workspace graph-build limits.
  - (c) **Self-host / swap** the memory layer to remove the dependency.
  Recommendation: (a) for launch (consistent with BYO ethos), (b) as a "lite" fallback
  with a tight free quota. **Decision needed from you.**
- **Zep namespacing:** use per-workspace Zep graph IDs so users never see each other's
  memory even under an operator key.

---

## WS-3 — Rate limiting & quotas  *(blocks launch)*

Framed around server compute, not tokens.

- **Hard cap on concurrent simulations** globally and per workspace (e.g. 1 running sim
  per workspace, N total across the box). Reject with 429 + "server busy" when exceeded.
- **Cap simulation size:** max rounds / max agents per sim (already partly via
  `OASIS_DEFAULT_MAX_ROUNDS`; enforce a hard ceiling users can't exceed).
- **Request rate limiting** on the API (per IP / per workspace) — `Flask-Limiter`.
- **Quota on operator-funded resources** (Zep builds if using option 2b).
- **Timeouts / reaping:** kill runaway or abandoned subprocesses after a max wall-clock.

---

## WS-4 — Production packaging  *(blocks launch)*

- **Frontend:** `vite build` → static bundle served by a CDN/static host or nginx.
  Free/near-free.
- **Backend server:** gunicorn — but **in-memory sim state forces a decision:**
  - (a) **Single gunicorn worker + threads** — simplest, preserves the current
    in-memory `SimulationRunner`. Fine for modest concurrency; the subprocess cap in
    WS-3 keeps load sane. **Recommended for launch.**
  - (b) **Externalize state** (Redis/DB for run registry, a real task queue —
    Celery/RQ/Dramatiq) — proper horizontal scaling, much more work. Defer until traffic
    justifies it.
- **Reverse proxy:** nginx for TLS termination (Let's Encrypt), routing `/api` → gunicorn.
  TLS is mandatory — keys travel in headers.
- **Host:** always-on VPS (Hetzner) for the backend compute, per the deployment note.
- **Ops:** structured logging with **key redaction**, `/health` already exists, a
  systemd unit or Docker for the gunicorn process, backups of workspace data.

---

## Recommended sequencing

1. **WS-1** (key isolation) — foundational; everything else assumes per-request creds.
2. **WS-2** (workspaces + Zep decision) — needs the creds plumbing from WS-1.
3. **WS-3** (limits) — needs workspace identity from WS-2.
4. **WS-4** (packaging) — last; deploy once the app is safe to serve two strangers.

## Decisions (locked 2026-07-04)

- **Zep:** **BYO Zep key too.** User brings both an LLM key and a Zep key. Operator cost
  → hosting only, fully. Extend the creds object + settings panel with `zep_api_key`
  (header `X-Zep-Api-Key`); `GraphBuilderService` takes the request's Zep key, not
  `Config.ZEP_API_KEY`. Onboarding docs must cover getting a Zep key.
- **Identity:** **anonymous signed sessions.** A signed cookie → `workspace_id`; no
  signup. No password/account surface to secure. Data is per-browser, not cross-device.
- **Scale:** **low tens of concurrent users.** WS-4 option (a): single gunicorn worker +
  threads, in-memory `SimulationRunner` preserved, subprocess cap (WS-3) keeps the box
  safe. Externalized state deferred until traffic justifies it.

Consequence: with both keys BYO and anonymous sessions, the server persists **no
secrets at all** — no LLM key, no Zep key, no credentials. That's the strongest possible
posture and removes encryption-at-rest and breach-liability concerns entirely.
