"""
Simulation engine layer: pluggable substrates behind the scenario presets'
``engine`` field. See ``base.py`` for the contract and design notes.
"""

from .base import (
    EngineCapabilities,
    EngineError,
    EngineRunHandle,
    SimulationEngine,
)
from .registry import (
    engine_for_scenario,
    get_engine,
    list_engines,
    register_engine,
)

__all__ = [
    "EngineCapabilities",
    "EngineError",
    "EngineRunHandle",
    "SimulationEngine",
    "engine_for_scenario",
    "get_engine",
    "list_engines",
    "register_engine",
]
