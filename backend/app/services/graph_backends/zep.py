"""
ZepBackend — GraphBackend over Zep Cloud (the current default).

A thin, behavior-preserving wrapper around the existing services
(GraphBuilderService, ZepEntityReader, ZepToolsService). Wrapping rather than
rewriting means the Zep path behaves exactly as before; the abstraction only
exists so a different backend (Mnemosyne) can be swapped in.
"""

from typing import Any, Callable, Dict, List, Optional

from .base import GraphBackend, GraphBackendError


class ZepBackend(GraphBackend):
    provider_name = "zep"

    def __init__(self, api_key: str, llm_client=None):
        if not api_key:
            raise GraphBackendError(
                "Zep backend selected but no Zep API key provided. Add one in "
                "Settings, or switch the memory graph to Mnemosyne (no key)."
            )
        self.api_key = api_key
        self._llm_client = llm_client
        from ..graph_builder import GraphBuilderService
        from ..zep_entity_reader import ZepEntityReader
        self._builder = GraphBuilderService(api_key=api_key)
        self._reader = ZepEntityReader(api_key=api_key)
        self._tools_cache = None

    def _tools(self):
        if self._tools_cache is None:
            from ..zep_tools import ZepToolsService
            self._tools_cache = ZepToolsService(api_key=self.api_key, llm_client=self._llm_client)
        return self._tools_cache

    # ------------------------------------------------------------------ build
    def create_graph(self, name: str) -> str:
        return self._builder.create_graph(name)

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]) -> None:
        self._builder.set_ontology(graph_id, ontology)

    def ingest_chunks(
        self,
        graph_id: str,
        chunks: List[str],
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> None:
        # Zep: submit episodes (0–0.6), then poll until processed (0.6–1.0).
        def add_cb(msg, ratio):
            if progress_callback:
                progress_callback(msg, ratio * 0.6)

        def wait_cb(msg, ratio):
            if progress_callback:
                progress_callback(msg, 0.6 + ratio * 0.4)

        episode_uuids = self._builder.add_text_batches(
            graph_id, chunks, batch_size=3, progress_callback=add_cb
        )
        self._builder._wait_for_episodes(episode_uuids, wait_cb)
        if progress_callback:
            progress_callback("done", 1.0)

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        return self._builder.get_graph_data(graph_id)

    def delete_graph(self, graph_id: str) -> None:
        self._builder.delete_graph(graph_id)

    # ------------------------------------------------------------------- read
    def filter_defined_entities(self, graph_id, defined_entity_types=None, enrich_with_edges=True):
        return self._reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=defined_entity_types,
            enrich_with_edges=enrich_with_edges,
        )

    def get_entities_by_type(self, graph_id, entity_type, enrich_with_edges=True):
        return self._reader.get_entities_by_type(
            graph_id=graph_id, entity_type=entity_type, enrich_with_edges=enrich_with_edges
        )

    def get_entity_with_context(self, graph_id, entity_uuid):
        return self._reader.get_entity_with_context(graph_id, entity_uuid)

    # ------------------------------------------------------------------ query
    def search(self, graph_id: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        result = self._tools().quick_search(graph_id, query, limit=limit)
        # SearchResult -> list of fact dicts (best-effort; used by report path)
        d = result.to_dict() if hasattr(result, 'to_dict') else {}
        facts = d.get('facts') or d.get('results') or []
        return facts if isinstance(facts, list) else []

    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        return self._tools().get_graph_statistics(graph_id)

    # For the report agent: expose the full Zep toolkit unchanged.
    def report_tools(self):
        return self._tools()
