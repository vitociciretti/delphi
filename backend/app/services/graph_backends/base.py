"""
GraphBackend — the abstract interface every memory-graph provider implements.

The surface mirrors what Delphi actually does with a graph today (derived from
the Zep call sites in graph.py / simulation.py / report_agent.py):

  build:   create_graph → set_ontology → ingest_chunks → get_graph_data
  read:    filter_defined_entities / get_entities_by_type / get_entity_with_context
  query:   search (ranked temporal facts) + get_graph_statistics
  memory:  remember / (recall via search)

Return shapes deliberately match the existing services (FilteredEntities, the
{nodes, edges} dict, EntityWithContext) so callers don't change when the backend
does. A backend that can't do something raises GraphBackendError.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional


class GraphBackendError(RuntimeError):
    """Raised when a backend can't satisfy an operation (misconfig, unsupported)."""


class GraphBackend(ABC):
    #: short id, e.g. "zep" / "mnemosyne"
    provider_name: str = "base"

    # ------------------------------------------------------------------ build
    @abstractmethod
    def create_graph(self, name: str) -> str:
        """Create a graph/namespace and return its id."""

    @abstractmethod
    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]) -> None:
        """Register project-specific entity/edge types for extraction."""

    @abstractmethod
    def ingest_chunks(
        self,
        graph_id: str,
        chunks: List[str],
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> None:
        """Extract entities+edges from text chunks into the graph.

        Blocks until the graph is queryable. `progress_callback(msg, ratio)` is
        called with ratio in [0,1]. (Zep polls async episodes; Mnemosyne is
        synchronous and simply reports 1.0 at the end.)
        """

    @abstractmethod
    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """Return {'nodes': [...], 'edges': [...]} for visualization."""

    @abstractmethod
    def delete_graph(self, graph_id: str) -> None:
        ...

    # ------------------------------------------------------------------- read
    @abstractmethod
    def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: Optional[List[str]] = None,
        enrich_with_edges: bool = True,
    ):
        """Return a FilteredEntities (entities matching the ontology types)."""

    @abstractmethod
    def get_entities_by_type(
        self, graph_id: str, entity_type: str, enrich_with_edges: bool = True
    ) -> List[Any]:
        ...

    @abstractmethod
    def get_entity_with_context(self, graph_id: str, entity_uuid: str):
        ...

    # ------------------------------------------------------------------ query
    @abstractmethod
    def search(self, graph_id: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Ranked temporal facts: [{'fact', 'valid_at', 'invalid_at', 'source'}]."""

    @abstractmethod
    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        ...

    # ----------------------------------------------------------------- memory
    def remember(self, graph_id: str, content: str, actor: str = "system") -> None:
        """Write an agent-memory event. Optional; default is a no-op."""
        return None

    # ----------------------------------------------------------------- report
    @abstractmethod
    def report_tools(self):
        """Return an object duck-typing ZepToolsService for the report agent
        (insight_forge / panorama_search / quick_search / interview_agents /
        get_graph_statistics / get_entity_summary / get_entities_by_type)."""
