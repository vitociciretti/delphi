"""
Request rate limiting (WS-3).

A single Limiter instance defined here (not bound to an app) so both the app
factory and the blueprint modules can import it without a circular import.

Limits are keyed by **workspace_id** (the anonymous per-browser identity from
WS-2), falling back to client IP before the workspace cookie is resolved. So a
single browser — not the whole world behind one NAT — is what a limit governs.

Storage is in-memory (`memory://`), which is correct for the single-worker
deployment (WS-4a). If we ever scale to multiple workers, point
`RATELIMIT_STORAGE_URI` at Redis so counters are shared.
"""

import os

from flask import request
from flask_limiter import Limiter

from ..config import Config


def rate_key() -> str:
    """Limit per anonymous workspace; fall back to IP if not yet resolved."""
    try:
        from .workspace import get_workspace_id
        return get_workspace_id()
    except Exception:
        return request.remote_addr or 'anon'


limiter = Limiter(
    key_func=rate_key,
    default_limits=[Config.RATELIMIT_DEFAULT] if Config.RATELIMIT_ENABLED else [],
    storage_uri=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'),
    enabled=Config.RATELIMIT_ENABLED,
    headers_enabled=True,  # emit X-RateLimit-* headers
)
