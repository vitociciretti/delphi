"""
Simulation engine abstraction.

The scenario preset layer (``backend/app/scenarios``) already carries an
``engine`` field on every channel, but until now ``"oasis"`` was the only
value the code could actually run — every domain preset was re-framed
social media. This package makes the field real:

- :class:`SimulationEngine` is the contract every engine implements.
- :class:`OasisEngine` (``oasis_engine.py``) adapts the existing
  subprocess-based OASIS runner unchanged.
- :class:`MarketEngine` (``market_engine.py``) is a genuinely different
  substrate: an in-process, seeded order-flow/price-impact simulation in
  which sentiment moves price and price feeds back into sentiment.

Engines are resolved through ``registry.get_engine(engine_id)`` using the
``engine`` value of the scenario preset's channels, so a preset (or a
user-supplied JSON preset) can switch substrate without code changes.

All engines speak the same on-disk protocol the rest of the app already
understands: a simulation directory under ``uploads/simulations/<id>``
containing ``simulation_config.json``, one ``<channel>/actions.jsonl``
action log per channel (same line schema as the OASIS scripts emit), and a
run-state JSON for status polling. That keeps the action feed, the opinion
tracker and the frontend engine-agnostic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class EngineCapabilities:
    """What a concrete engine can do, so API layers can degrade gracefully."""

    live_interventions: bool = False   # can inject events into a running sim
    interviews: bool = False           # can query agents mid-run
    deterministic_seed: bool = False   # same seed => identical run
    in_process: bool = False           # runs inside the Flask process (fast, no external deps)
    ensembles: bool = False            # can cheaply run N seeded variants


@dataclass
class EngineRunHandle:
    """Uniform result of starting a run."""

    simulation_id: str
    engine_id: str
    status: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "engine_id": self.engine_id,
            "status": self.status,
            **self.detail,
        }


class SimulationEngine(ABC):
    """
    Contract for a simulation substrate.

    Engines are stateless facades over a simulation directory; any run
    state they need must live on disk (or in module-level registries the
    way ``SimulationRunner`` already does) so status survives restarts.
    """

    #: unique engine id, matches the ``engine`` field of scenario channels
    engine_id: str = ""

    @property
    @abstractmethod
    def capabilities(self) -> EngineCapabilities:
        """Static capability description."""

    @abstractmethod
    def start(
        self,
        simulation_id: str,
        *,
        seed: Optional[int] = None,
        max_rounds: Optional[int] = None,
        **options: Any,
    ) -> EngineRunHandle:
        """Start (or restart) a run for a prepared simulation directory."""

    @abstractmethod
    def stop(self, simulation_id: str) -> Dict[str, Any]:
        """Stop a running simulation."""

    @abstractmethod
    def get_status(self, simulation_id: str) -> Dict[str, Any]:
        """Current run status in the shared run-state shape."""

    def inject_event(
        self,
        simulation_id: str,
        event: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Inject an intervention into a running simulation.

        ``event`` is engine-interpreted; common keys:
          - ``text``: natural-language event content (a post / news item)
          - ``magnitude`` / ``direction``: numeric shock (market engines)
          - ``channel``: target channel id
        Engines that cannot do this raise ``NotImplementedError`` (check
        ``capabilities.live_interventions`` first).
        """
        raise NotImplementedError(
            f"engine '{self.engine_id}' does not support live interventions"
        )


class EngineError(RuntimeError):
    """Raised for engine-level failures (unknown engine, bad state, ...)."""
