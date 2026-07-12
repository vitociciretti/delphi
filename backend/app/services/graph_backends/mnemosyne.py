"""
MnemosyneBackend — GraphBackend over the self-hosted Mnemosyne GraphRAG engine.

Runs every operation in a short-lived subprocess under **Mnemosyne's own venv**
(see mnemosyne_worker.py for why). Delphi does NOT need mnemosyne/loomstate in
its own venv — it shells out. Per-project isolation: each graph gets its own data
dir under the current workspace (`<workspace>/mnemosyne_graphs/<graph_id>`), so
WS-2 isolation extends to Mnemosyne graphs for free.

Config (env, optional):
  MNEMOSYNE_VENV_PYTHON  path to mnemosyne venv python
                         (default: ~/projects/mnemosyne/.venv/bin/python)
  MNEMOSYNE_EXTRACTOR    'auto'|'llm'|'rules' (default 'auto' → LLM if a key is present)

Returns Zep-compatible objects (EntityNode / FilteredEntities / EntityWithContext)
so the rest of Delphi is backend-agnostic.
"""

import json
import os
import subprocess
import uuid
from typing import Any, Callable, Dict, List, Optional

from .base import GraphBackend, GraphBackendError

_DEFAULT_VENV = os.path.expanduser('~/projects/mnemosyne/.venv/bin/python')
_WORKER = os.path.join(os.path.dirname(__file__), 'mnemosyne_worker.py')


def _venv_python() -> str:
    return os.environ.get('MNEMOSYNE_VENV_PYTHON', _DEFAULT_VENV)


def mnemosyne_available() -> bool:
    return os.path.isfile(_venv_python()) and os.path.isfile(_WORKER)


class MnemosyneBackend(GraphBackend):
    provider_name = "mnemosyne"

    def __init__(self, creds=None, llm_client=None):
        self.creds = creds
        self._llm_client = llm_client
        if not mnemosyne_available():
            raise GraphBackendError(
                "Mnemosyne backend selected but its venv wasn't found at "
                f"{_venv_python()}. Install Mnemosyne (and loomstate) and/or set "
                "MNEMOSYNE_VENV_PYTHON, or switch the memory graph back to Zep. "
                "See docs/ws5-graph-backend-and-live-view.md."
            )
        # BYO-LLM: Mnemosyne's OpenAI-compatible extraction path reads these env vars.
        self._llm_env = {}
        if creds is not None:
            if getattr(creds, 'base_url', ''):
                self._llm_env['LLM_BASE_URL'] = creds.base_url
            if getattr(creds, 'api_key', ''):
                self._llm_env['LLM_API_KEY'] = creds.api_key
            if getattr(creds, 'model', ''):
                self._llm_env['LLM_MODEL'] = creds.model
        # Keyless local providers (Ollama/LM Studio) are still LLM-capable:
        # base_url + model suffice; the OpenAI client just needs a non-empty token.
        if not self._llm_env.get('LLM_API_KEY') and self._llm_env.get('LLM_BASE_URL') and self._llm_env.get('LLM_MODEL'):
            self._llm_env['LLM_API_KEY'] = 'not-needed'
        # No usable LLM config → fall back to the deterministic rules extractor.
        self._extractor = 'auto' if self._llm_env.get('LLM_API_KEY') else 'rules'
        self._ontology_cache: Dict[str, Dict[str, Any]] = {}

    # ----------------------------------------------------------- infra
    def _data_dir(self, graph_id: str) -> str:
        from ...utils.workspace import workspace_root
        return os.path.join(workspace_root(), 'mnemosyne_graphs', graph_id)

    def _run(self, cmd: Dict[str, Any], timeout: int = 900) -> Dict[str, Any]:
        try:
            proc = subprocess.run(
                [_venv_python(), _WORKER],
                input=json.dumps(cmd),
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise GraphBackendError("Mnemosyne worker timed out.")
        if not proc.stdout.strip():
            raise GraphBackendError(f"Mnemosyne worker returned nothing. stderr: {proc.stderr[-400:]}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise GraphBackendError(f"Mnemosyne worker bad output: {proc.stdout[:300]}")
        if not payload.get('ok'):
            raise GraphBackendError(f"Mnemosyne worker error: {payload.get('error')}")
        return payload['result']

    # ------------------------------------------------------------ build
    def create_graph(self, name: str) -> str:
        graph_id = f"mnem_{uuid.uuid4().hex[:12]}"
        os.makedirs(self._data_dir(graph_id), exist_ok=True)
        return graph_id

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]) -> None:
        # Persist so ingest (possibly a later call) can inject it into extraction.
        self._ontology_cache[graph_id] = ontology
        path = os.path.join(self._data_dir(graph_id), 'ontology.json')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(ontology, f, ensure_ascii=False)

    def _load_ontology(self, graph_id: str) -> Dict[str, Any]:
        if graph_id in self._ontology_cache:
            return self._ontology_cache[graph_id]
        path = os.path.join(self._data_dir(graph_id), 'ontology.json')
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def ingest_chunks(self, graph_id, chunks, progress_callback=None) -> None:
        if progress_callback:
            progress_callback("Extracting entities & relations (Mnemosyne)…", 0.1)
        self._run({
            'op': 'ingest',
            'data_dir': self._data_dir(graph_id),
            'chunks': chunks,
            'ontology': self._load_ontology(graph_id),
            'extractor': self._extractor,
            'llm_env': self._llm_env,
        })
        if progress_callback:
            progress_callback("done", 1.0)

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        return self._run({'op': 'graph_data', 'data_dir': self._data_dir(graph_id)})

    def delete_graph(self, graph_id: str) -> None:
        import shutil
        d = self._data_dir(graph_id)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------- read
    def _entity_nodes(self, graph_id: str, types=None):
        from ..zep_entity_reader import EntityNode
        res = self._run({'op': 'entities', 'data_dir': self._data_dir(graph_id),
                         'types': types or []})
        nodes = []
        for e in res.get('entities', []):
            nodes.append(EntityNode(
                uuid=e['uuid'], name=e['name'], labels=e.get('labels') or ['Entity'],
                summary=e.get('summary', ''), attributes=e.get('attributes') or {},
                related_edges=e.get('related_edges') or [],
                related_nodes=e.get('related_nodes') or [],
            ))
        return nodes

    def filter_defined_entities(self, graph_id, defined_entity_types=None, enrich_with_edges=True):
        from ..zep_entity_reader import FilteredEntities
        nodes = self._entity_nodes(graph_id, defined_entity_types)
        types = set()
        for n in nodes:
            t = n.get_entity_type()
            if t:
                types.add(t)
        return FilteredEntities(
            entities=nodes, entity_types=types,
            total_count=len(nodes), filtered_count=len(nodes),
        )

    def get_entities_by_type(self, graph_id, entity_type, enrich_with_edges=True):
        return self._entity_nodes(graph_id, [entity_type])

    def get_entity_with_context(self, graph_id, entity_uuid):
        for n in self._entity_nodes(graph_id):
            if n.uuid == entity_uuid:
                return n
        return None

    # ------------------------------------------------------------ query
    def search(self, graph_id: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        res = self._run({'op': 'search', 'data_dir': self._data_dir(graph_id),
                         'query': query, 'limit': limit, 'llm_env': self._llm_env})
        return res.get('facts', [])

    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        data = self.get_graph_data(graph_id)
        return {'node_count': len(data.get('nodes', [])),
                'edge_count': len(data.get('edges', []))}

    def _llm_client_ref(self):
        return self._llm_client

    # ----------------------------------------------------------- report
    def report_tools(self):
        """Duck-typed replacement for ZepToolsService, backed by this graph."""
        return MnemosyneReportTools(self)


class MnemosyneReportTools:
    """Implements the ZepToolsService surface the report agent calls, on top of
    a MnemosyneBackend. Graph tools use `backend.search()`; interview_agents is
    sim-side and delegated to the existing (graph-agnostic) implementation."""

    def __init__(self, backend: 'MnemosyneBackend'):
        self.backend = backend
        self._interview_shim = None

    def _facts(self, graph_id, query, limit):
        try:
            rows = self.backend.search(graph_id, query, limit=limit)
        except Exception:
            rows = []
        return [r.get('fact') or '' for r in rows if r.get('fact')]

    def quick_search(self, graph_id, query, limit=10):
        from ..zep_tools import SearchResult
        facts = self._facts(graph_id, query, limit)
        return SearchResult(facts=facts, edges=[], nodes=[], query=query, total_count=len(facts))

    def panorama_search(self, graph_id, query, include_expired=True):
        from ..zep_tools import SearchResult
        facts = self._facts(graph_id, query, 30)
        return SearchResult(facts=facts, edges=[], nodes=[], query=query, total_count=len(facts))

    def insight_forge(self, graph_id, query, simulation_requirement="", report_context=""):
        from ..zep_tools import InsightForgeResult
        facts = self._facts(graph_id, query, 30)
        return InsightForgeResult(
            query=query, simulation_requirement=simulation_requirement,
            sub_queries=[query], semantic_facts=facts, total_facts=len(facts),
        )

    def get_graph_statistics(self, graph_id):
        return self.backend.get_graph_statistics(graph_id)

    def get_entities_by_type(self, graph_id, entity_type):
        return self.backend.get_entities_by_type(graph_id, entity_type)

    def get_entity_summary(self, graph_id, entity_name):
        for n in self.backend._entity_nodes(graph_id):
            if n.name == entity_name:
                return {'entity': n.name, 'type': n.get_entity_type(),
                        'summary': n.summary, 'edges': n.related_edges}
        return {'entity': entity_name, 'summary': '', 'edges': []}

    def interview_agents(self, simulation_id, interview_requirement,
                         simulation_requirement="", max_agents=5, custom_questions=None):
        # graph-agnostic (uses SimulationRunner + LLM); reuse the existing impl
        # with a placeholder Zep key (its interview path never touches the client).
        if self._interview_shim is None:
            from ..zep_tools import ZepToolsService
            self._interview_shim = ZepToolsService(
                api_key='not-needed', llm_client=self.backend._llm_client_ref())
        return self._interview_shim.interview_agents(
            simulation_id=simulation_id, interview_requirement=interview_requirement,
            simulation_requirement=simulation_requirement, max_agents=max_agents,
            custom_questions=custom_questions)
