"""
Engine registry.

Maps the ``engine`` value of scenario-preset channels onto concrete
:class:`~.base.SimulationEngine` implementations. Engines register lazily
on first access so importing the package stays cheap and side-effect free.
"""

from typing import Dict, List

from .base import EngineError, SimulationEngine

_engines: Dict[str, SimulationEngine] = {}
_initialized = False


def _ensure_builtin_engines() -> None:
    global _initialized
    if _initialized:
        return
    # Imported here (not at module top) to avoid import cycles with the
    # services package, which the concrete engines depend on.
    from .oasis_engine import OasisEngine
    from .market_engine import MarketEngine

    for engine in (OasisEngine(), MarketEngine()):
        _engines.setdefault(engine.engine_id, engine)
    _initialized = True


def register_engine(engine: SimulationEngine) -> None:
    """Register a custom engine (overrides a builtin with the same id)."""
    if not engine.engine_id:
        raise EngineError("engine_id must be a non-empty string")
    _engines[engine.engine_id] = engine


def get_engine(engine_id: str) -> SimulationEngine:
    """Resolve an engine by id, raising EngineError for unknown ids."""
    _ensure_builtin_engines()
    engine = _engines.get(engine_id)
    if engine is None:
        raise EngineError(
            f"unknown engine '{engine_id}' (available: {', '.join(sorted(_engines))})"
        )
    return engine


def list_engines() -> List[Dict[str, object]]:
    """Describe registered engines (id + capabilities) for the API layer."""
    _ensure_builtin_engines()
    return [
        {"engine_id": eid, "capabilities": vars(engine.capabilities)}
        for eid, engine in sorted(_engines.items())
    ]


def engine_for_scenario(scenario) -> SimulationEngine:
    """
    Resolve the engine for a scenario preset.

    Presets declare an engine per channel; mixed-engine presets are not
    supported yet, so the first channel's engine wins and a mismatch is an
    error rather than a silent surprise.
    """
    engines = {ch.engine for ch in scenario.channels}
    if len(engines) > 1:
        raise EngineError(
            f"scenario '{scenario.id}' mixes engines {sorted(engines)}; "
            "one engine per scenario is required"
        )
    return get_engine(next(iter(engines)) if engines else "oasis")
