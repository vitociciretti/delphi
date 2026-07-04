"""
Scenario preset registry.

Loads built-in presets bundled next to this module (``presets/*.json``) and,
optionally, user-supplied presets from an external directory. This is the
pluggability seam: adding a new simulation domain is as simple as dropping a
JSON file into the user presets directory — no code change required.

User presets override built-ins with the same ``id``, so a deployment can
retune a shipped scenario without editing the source tree.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Dict, List, Optional

from .preset import ScenarioPreset

# Directory holding the bundled built-in presets.
_BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "presets")

# The scenario used when a caller does not specify one. Chosen to reproduce
# the engine's original social-media behaviour for full backward compatibility.
DEFAULT_SCENARIO_ID = "social_media"


class ScenarioRegistry:
    """In-memory registry of scenario presets, loaded from JSON on disk."""

    def __init__(
        self,
        builtin_dir: Optional[str] = None,
        user_dir: Optional[str] = None,
        default_id: str = DEFAULT_SCENARIO_ID,
    ) -> None:
        self.builtin_dir = builtin_dir or _BUILTIN_DIR
        self.user_dir = user_dir
        self.default_id = default_id
        self._presets: Dict[str, ScenarioPreset] = {}
        self._lock = threading.Lock()
        self.reload()

    # ---- loading -------------------------------------------------------
    def reload(self) -> None:
        """(Re)load presets from disk. Built-ins first, then user overrides."""
        presets: Dict[str, ScenarioPreset] = {}
        for preset in self._load_dir(self.builtin_dir, builtin=True):
            presets[preset.id] = preset
        if self.user_dir and os.path.isdir(self.user_dir):
            for preset in self._load_dir(self.user_dir, builtin=False):
                presets[preset.id] = preset  # user overrides built-in
        with self._lock:
            self._presets = presets

    @staticmethod
    def _load_dir(path: str, builtin: bool) -> List[ScenarioPreset]:
        results: List[ScenarioPreset] = []
        if not path or not os.path.isdir(path):
            return results
        for fname in sorted(os.listdir(path)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(path, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                raise ScenarioLoadError(f"failed to read preset '{fpath}': {exc}") from exc
            try:
                preset = ScenarioPreset.from_dict(data, builtin=builtin)
            except (KeyError, TypeError, ValueError) as exc:
                raise ScenarioLoadError(f"malformed preset '{fpath}': {exc}") from exc
            errors = preset.validate()
            if errors:
                raise ScenarioLoadError(
                    f"invalid preset '{fpath}': " + "; ".join(errors)
                )
            results.append(preset)
        return results

    # ---- access --------------------------------------------------------
    def all(self) -> List[ScenarioPreset]:
        with self._lock:
            return sorted(self._presets.values(), key=lambda p: (p.domain, p.id))

    def ids(self) -> List[str]:
        with self._lock:
            return sorted(self._presets.keys())

    def has(self, preset_id: str) -> bool:
        with self._lock:
            return preset_id in self._presets

    def get(self, preset_id: str) -> ScenarioPreset:
        with self._lock:
            if preset_id not in self._presets:
                raise KeyError(
                    f"unknown scenario '{preset_id}'; available: {sorted(self._presets)}"
                )
            return self._presets[preset_id]

    def default(self) -> ScenarioPreset:
        with self._lock:
            if self.default_id in self._presets:
                return self._presets[self.default_id]
            # Fall back to any preset rather than crash a fresh deployment.
            if self._presets:
                return sorted(self._presets.values(), key=lambda p: p.id)[0]
        raise KeyError("no scenario presets are available")

    def get_or_default(self, preset_id: Optional[str]) -> ScenarioPreset:
        """Resolve a preset id, falling back to the default when None/blank."""
        if not preset_id:
            return self.default()
        with self._lock:
            if preset_id in self._presets:
                return self._presets[preset_id]
        return self.default()

    def register(self, preset: ScenarioPreset) -> None:
        """Register an in-memory preset (mainly for tests / programmatic use)."""
        errors = preset.validate()
        if errors:
            raise ScenarioLoadError("invalid preset: " + "; ".join(errors))
        with self._lock:
            self._presets[preset.id] = preset


class ScenarioLoadError(Exception):
    """Raised when a preset file cannot be read, parsed, or validated."""


# Process-wide singleton, lazily initialised so importing this module never
# fails even before configuration is loaded.
_registry: Optional[ScenarioRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ScenarioRegistry:
    """Return the shared registry, honouring ``Config.SCENARIO_PRESETS_DIR``."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                user_dir = None
                default_id = DEFAULT_SCENARIO_ID
                try:  # Config import is deferred to avoid import cycles at startup.
                    from ..config import Config

                    user_dir = getattr(Config, "SCENARIO_PRESETS_DIR", None)
                    default_id = getattr(Config, "SCENARIO_DEFAULT", DEFAULT_SCENARIO_ID)
                except Exception:  # pragma: no cover - config optional at import time
                    pass
                _registry = ScenarioRegistry(user_dir=user_dir, default_id=default_id)
    return _registry


def reset_registry() -> None:
    """Drop the cached singleton (used by tests)."""
    global _registry
    with _registry_lock:
        _registry = None
