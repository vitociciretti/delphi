"""
OASIS engine adapter.

Wraps the existing subprocess-based :class:`SimulationRunner` behind the
:class:`SimulationEngine` contract without changing its behaviour. This is
the default engine and reproduces exactly what the app did before the
engine layer existed.
"""

from typing import Any, Dict, Optional

from ..utils.logger import get_logger
from .base import EngineCapabilities, EngineRunHandle, SimulationEngine

logger = get_logger('mirofish.engines.oasis')


class OasisEngine(SimulationEngine):
    """Adapter over the OASIS subprocess runner (twitter/reddit feeds)."""

    engine_id = "oasis"

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            live_interventions=True,   # via the IPC inject_event command
            interviews=True,
            deterministic_seed=False,  # LLM-driven; runs are stochastic
            in_process=False,
            ensembles=False,           # variants must be run one by one
        )

    def start(
        self,
        simulation_id: str,
        *,
        seed: Optional[int] = None,
        max_rounds: Optional[int] = None,
        **options: Any,
    ) -> EngineRunHandle:
        from ..services.simulation_runner import SimulationRunner

        if seed is not None:
            logger.warning("OASIS engine ignores seed=%s (LLM-driven runs are stochastic)", seed)

        state = SimulationRunner.start_simulation(
            simulation_id=simulation_id,
            platform=options.get("platform", "parallel"),
            max_rounds=max_rounds,
            enable_graph_memory_update=options.get("enable_graph_memory_update", False),
            graph_id=options.get("graph_id"),
        )
        return EngineRunHandle(
            simulation_id=simulation_id,
            engine_id=self.engine_id,
            status=state.runner_status.value,
            detail={"process_pid": state.process_pid},
        )

    def stop(self, simulation_id: str) -> Dict[str, Any]:
        from ..services.simulation_runner import SimulationRunner

        state = SimulationRunner.stop_simulation(simulation_id)
        return state.to_dict()

    def get_status(self, simulation_id: str) -> Dict[str, Any]:
        from ..services.simulation_runner import SimulationRunner

        state = SimulationRunner.get_run_state(simulation_id)
        if state is None:
            return {"simulation_id": simulation_id, "runner_status": "idle"}
        return state.to_dict()

    def inject_event(self, simulation_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Queue an intervention for the running sim's next round.

        OASIS runs in a subprocess whose IPC command channel is only
        polled after the run completes, so mid-run injection goes through
        the file queue drained by the round loop instead
        (``backend/scripts/interventions.py``).
        """
        from ..services.intervention_service import queue_intervention

        channel = event.get("channel") or event.get("platform")
        record = queue_intervention(
            simulation_id=simulation_id,
            text=event.get("text", ""),
            agent_id=event.get("agent_id"),
            channels=[channel] if channel else None,
        )
        return {"success": True, "result": record}
