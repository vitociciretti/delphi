"""
Pluggable memory-graph backends (WS-5 Part A).

Delphi treats the memory graph as a swappable, user-chosen provider — exactly
like the BYO-LLM provider picker. Callers code against `GraphBackend`; the
concrete backend is chosen per request from the user's settings.

- `ZepBackend`       — Zep Cloud (managed, BYO key). Current default.
- `MnemosyneBackend` — self-hosted temporal GraphRAG (no key, no free-tier cap).

Use `get_graph_backend(creds)` (factory) to obtain the right one for a request.
"""

from .base import GraphBackend, GraphBackendError
from .factory import get_graph_backend, available_providers

__all__ = [
    'GraphBackend',
    'GraphBackendError',
    'get_graph_backend',
    'available_providers',
]
