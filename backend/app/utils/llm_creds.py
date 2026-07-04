"""
Per-request LLM / Zep credentials (Bring-Your-Own-Key).

Delphi is a public BYO-key tool: every user brings their own keys, and the
server persists **nothing**. Each browser stores its config in sessionStorage
and sends it on every request as headers:

    X-LLM-Api-Key   X-LLM-Base-Url   X-LLM-Model   X-LLM-Provider   X-Zep-Api-Key

This module turns those headers into an `LlmCreds` object and provides the two
ways the rest of the app consumes them:

- `to_llm_client()` — an OpenAI-compatible `LLMClient` for in-process calls
  (seed assistant, ontology generation, report agent).
- `subprocess_env()` — the env-var dict injected into a simulation subprocess,
  which reads config via `Config`/`os.environ` in its own fresh process.

There is deliberately no global mutation and no on-disk key. A request that
forgets to pass creds fails closed ("LLM_API_KEY 未配置") rather than silently
falling back to some other user's key.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional

from flask import request

# Header names — kept in sync with frontend/src/api/index.js
H_API_KEY = 'X-LLM-Api-Key'
H_BASE_URL = 'X-LLM-Base-Url'
H_MODEL = 'X-LLM-Model'
H_PROVIDER = 'X-LLM-Provider'
H_ZEP_KEY = 'X-Zep-Api-Key'

# Env vars the simulation subprocess (and Config) read config from.
_LLM_ENV_VARS = ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL_NAME', 'ZEP_API_KEY')


@dataclass
class LlmCreds:
    api_key: str = ''
    base_url: str = ''
    model: str = ''
    provider: str = ''
    zep_api_key: str = ''

    def has_llm(self) -> bool:
        # base_url + model are enough for keyless local providers (Ollama/LM Studio).
        return bool(self.base_url and self.model)

    def to_llm_client(self):
        """Build an OpenAI-compatible client scoped to this request's key.

        Fails closed: if the request didn't carry base_url + model we raise
        rather than silently falling back to the operator's Config/.env (which
        would use the operator's key/endpoint on a user's traffic).
        """
        if not self.has_llm():
            raise ValueError(
                "LLM not configured — open Settings and set Base URL + Model "
                "(bring your own key). The server holds no default key."
            )
        from .llm_client import LLMClient
        return LLMClient(
            api_key=self.api_key or 'not-needed',  # local providers (Ollama) need no key
            base_url=self.base_url,                 # explicit — never Config fallback
            model=self.model,                       # explicit — never Config fallback
        )

    def subprocess_env(self) -> Dict[str, str]:
        """Env vars to inject into a simulation subprocess for this request."""
        env: Dict[str, str] = {}
        if self.api_key:
            env['LLM_API_KEY'] = self.api_key
        if self.base_url:
            env['LLM_BASE_URL'] = self.base_url
        if self.model:
            env['LLM_MODEL_NAME'] = self.model
        if self.zep_api_key:
            env['ZEP_API_KEY'] = self.zep_api_key
        return env


def creds_from_request() -> LlmCreds:
    """Extract per-request creds from the incoming request headers."""
    h = request.headers
    return LlmCreds(
        api_key=(h.get(H_API_KEY) or '').strip(),
        base_url=(h.get(H_BASE_URL) or '').strip(),
        model=(h.get(H_MODEL) or '').strip(),
        provider=(h.get(H_PROVIDER) or '').strip(),
        zep_api_key=(h.get(H_ZEP_KEY) or '').strip(),
    )


def child_env_without_inherited_keys(base_env: Dict[str, str]) -> Dict[str, str]:
    """Copy an env dict with any inherited LLM/Zep vars removed.

    The parent Flask process must not leak its own (dev-time) keys into a user's
    simulation subprocess. Strip them, then the caller merges in the request's
    `subprocess_env()`.
    """
    return {k: v for k, v in base_env.items() if k not in _LLM_ENV_VARS}
