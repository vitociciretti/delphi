"""
Gunicorn config for Delphi (WS-4 production packaging).

**Single worker, many threads — this is deliberate, not a limitation.**
SimulationRunner tracks running simulations in class-level in-memory dicts
(`_processes`, `_process_launch_time`, …), the Flask-Limiter uses `memory://`
storage, and the reaper is one daemon thread. All of that is per-process state.
Multiple workers would each see only their own slice, so a /stop or a rate-limit
could hit the wrong worker. One worker keeps that state authoritative; threads
give us concurrency for the many in-request LLM calls and status polling.

If you outgrow one worker (WS-4b): externalize run-state + limiter to Redis and
move simulations to a task queue, then raise `workers`.
"""

import os

# Bind to localhost only — nginx terminates TLS and proxies to us.
bind = os.environ.get('GUNICORN_BIND', '127.0.0.1:8000')

# Keep in-memory state authoritative (see module docstring).
workers = 1
threads = int(os.environ.get('GUNICORN_THREADS', '16'))
worker_class = 'gthread'

# create_app() must run in the worker (starts the reaper thread; threads do not
# survive a fork), so do NOT preload.
preload_app = False

# In-request LLM calls (seed chat, ontology, report chat) can be slow; match the
# frontend's 300s axios timeout. Long simulations run in background threads/
# subprocesses, not in the request, so this does not bound them.
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '300'))
graceful_timeout = 30
keepalive = 5

# Worker recycling is DISABLED by default: recycling the single worker would drop
# the in-memory simulation tracking (`_processes`) mid-run, orphaning live
# subprocesses and killing their monitor threads. Leave at 0 unless you have
# externalized that state.
max_requests = int(os.environ.get('GUNICORN_MAX_REQUESTS', '0'))
max_requests_jitter = 0

accesslog = os.environ.get('GUNICORN_ACCESS_LOG', '-')   # stdout
errorlog = os.environ.get('GUNICORN_ERROR_LOG', '-')     # stderr
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
