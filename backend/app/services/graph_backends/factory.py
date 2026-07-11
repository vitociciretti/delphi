"""
Backend selection — like the LLM provider picker, but for the memory graph.

`get_graph_backend(creds)` returns the backend the user chose in Settings
(carried per request as `creds.graph_provider`). Zep is the default.
"""

from typing import Any, Dict, List

from .base import GraphBackend, GraphBackendError


def available_providers() -> List[Dict[str, Any]]:
    """Metadata for the Settings picker (kept in sync with the frontend)."""
    return [
        {
            "id": "zep",
            "label": "Zep Cloud",
            "needs_key": True,
            "note": "Managed temporal knowledge graph. Bring a free Zep key.",
        },
        {
            "id": "mnemosyne",
            "label": "Mnemosyne (local)",
            "needs_key": False,
            "note": "Self-hosted GraphRAG — no key, no free-tier limit. Uses your LLM key for extraction.",
        },
    ]


def get_graph_backend(creds, llm_client=None) -> GraphBackend:
    """Build the graph backend for this request from the user's creds."""
    provider = (getattr(creds, 'graph_provider', 'zep') or 'zep').lower()

    if provider == 'mnemosyne':
        from .mnemosyne import MnemosyneBackend
        return MnemosyneBackend(creds=creds, llm_client=llm_client)

    if provider in ('zep', ''):
        from .zep import ZepBackend
        return ZepBackend(api_key=getattr(creds, 'zep_api_key', ''), llm_client=llm_client)

    raise GraphBackendError(f"Unknown graph provider: {provider!r}")
