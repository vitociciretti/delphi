"""
Scenario / domain layer.

This package makes the simulation engine adaptable to different scopes by
externalising *what kind of world* a simulation runs in into config-driven
presets, rather than hard-coding social-media (Twitter/Reddit, China-timezone)
assumptions into the generator.

Public API::

    from .scenarios import get_registry, ScenarioPreset

    registry = get_registry()
    preset = registry.get_or_default("financial_market")
"""

from .preset import ActivityRhythm, ChannelSpec, ScenarioPreset, ENGINE_PLATFORMS, KNOWN_DOMAINS
from .registry import (
    ScenarioRegistry,
    ScenarioLoadError,
    get_registry,
    reset_registry,
    DEFAULT_SCENARIO_ID,
)

__all__ = [
    "ActivityRhythm",
    "ChannelSpec",
    "ScenarioPreset",
    "ENGINE_PLATFORMS",
    "KNOWN_DOMAINS",
    "ScenarioRegistry",
    "ScenarioLoadError",
    "get_registry",
    "reset_registry",
    "DEFAULT_SCENARIO_ID",
]
