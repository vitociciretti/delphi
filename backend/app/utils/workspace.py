"""
Anonymous per-user workspaces (WS-2 of the public-deployment plan).

Delphi ships public with no accounts. Each browser is given a random
`workspace_id` carried in a signed cookie (`delphi_ws`, signed with SECRET_KEY
so it can't be forged). All on-disk data — projects, simulations, reports — is
namespaced under `uploads/workspaces/<workspace_id>/…`, so one visitor can never
list, read, or delete another's data.

Resolution mirrors utils/locale.py:
- Inside a request: `before_request` resolves the cookie (or mints a new id) and
  stashes it on `flask.g`; `after_request` sets the cookie for new visitors.
- Inside a background thread (which has no request context): the spawning route
  captures `get_workspace_id()` and the thread calls `set_workspace_id(...)` at
  its start — exactly how locale is propagated.

`get_workspace_id()` fails loud (RuntimeError) when neither a request nor a
thread-local workspace is set, so a mis-wired background path is caught in tests
rather than silently writing to the wrong place.
"""

import os
import threading
import uuid

from flask import request, g, has_request_context
from itsdangerous import URLSafeSerializer, BadSignature

from ..config import Config

COOKIE_NAME = 'delphi_ws'
_SALT = 'delphi-workspace-v1'
# One year; the cookie is the only handle to anonymous data, so keep it long.
_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

_thread_local = threading.local()

_serializer = URLSafeSerializer(Config.SECRET_KEY, salt=_SALT)


def _new_id() -> str:
    return uuid.uuid4().hex


def _sign(workspace_id: str) -> str:
    return _serializer.dumps(workspace_id)


def _unsign(signed: str):
    """Return the workspace_id from a signed cookie, or None if invalid."""
    try:
        return _serializer.loads(signed)
    except BadSignature:
        return None


# ---- request lifecycle -----------------------------------------------------

def init_request_workspace() -> None:
    """before_request hook: resolve (or mint) this request's workspace_id.

    Stores it on `g._workspace_id` and flags `g._workspace_is_new` so
    after_request knows whether to (re)issue the cookie.
    """
    signed = request.cookies.get(COOKIE_NAME)
    workspace_id = _unsign(signed) if signed else None
    g._workspace_is_new = workspace_id is None
    g._workspace_id = workspace_id or _new_id()


def attach_workspace_cookie(response):
    """after_request hook: issue the signed cookie for a new visitor."""
    if getattr(g, '_workspace_is_new', False) and getattr(g, '_workspace_id', None):
        response.set_cookie(
            COOKIE_NAME,
            _sign(g._workspace_id),
            max_age=_COOKIE_MAX_AGE,
            httponly=True,               # not readable by JS — it's an opaque handle
            samesite='Lax',
            secure=Config.COOKIE_SECURE,  # True in production (COOKIE_SECURE=true, behind TLS)
        )
    return response


# ---- accessors -------------------------------------------------------------

def set_workspace_id(workspace_id: str) -> None:
    """Set the workspace for the current thread. Call at the start of any
    background thread, right next to set_locale()."""
    _thread_local.workspace_id = workspace_id


def get_workspace_id() -> str:
    if has_request_context():
        ws = getattr(g, '_workspace_id', None)
        if ws:
            return ws
        # Request context without the before_request hook having run
        # (e.g. a raw test_request_context) — resolve on the fly.
        init_request_workspace()
        return g._workspace_id
    ws = getattr(_thread_local, 'workspace_id', None)
    if ws:
        return ws
    raise RuntimeError(
        "No workspace in context. A background thread must call "
        "set_workspace_id(get_workspace_id()) captured from its spawning request."
    )


def workspace_root() -> str:
    """Filesystem root for the current workspace's data."""
    return os.path.join(Config.UPLOAD_FOLDER, 'workspaces', get_workspace_id())
