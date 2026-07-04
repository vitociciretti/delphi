"""
Scenario / domain preset model.

A ``ScenarioPreset`` describes *what kind of world* a simulation runs in:
its activity rhythm (when agents are active), its time horizon, the
communication channels available to agents, the stance vocabulary that fits
the domain, and the natural-language framing injected into LLM prompts.

Presets are the mechanism that makes the engine adaptable to different scopes
(social media, financial markets, organisational decision-making, narrative
"what-if" worlds, or a fully custom domain) *without* touching engine code.
Built-in presets ship as JSON files under ``presets/``; users can add their
own by dropping JSON files into ``Config.SCENARIO_PRESETS_DIR``.

The model deliberately maps onto the fields the existing OASIS-based
generator/runner already understand (``engine_platform`` is ``twitter`` or
``reddit``), so a preset can broaden the *framing* of a simulation while still
running on the current engine. The ``engine`` field leaves room for
alternative simulation backends to be plugged in later.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# Engine platforms understood by the current OASIS-backed runner.
ENGINE_PLATFORMS = ("twitter", "reddit")

# Known high-level domains. This list is advisory (used for grouping / UI); an
# unknown domain is allowed so custom presets are never blocked.
KNOWN_DOMAINS = (
    "social_media",
    "market",
    "organization",
    "narrative",
    "custom",
)


@dataclass
class ActivityRhythm:
    """When agents are active over a 24-hour cycle.

    Replaces the previously hard-coded ``CHINA_TIMEZONE_CONFIG`` so different
    scenarios can model different populations (a global audience, a trading
    desk, an office, a fictional world with no diurnal cycle at all).
    """

    timezone: str = "UTC"
    peak_hours: List[int] = field(default_factory=lambda: [19, 20, 21, 22])
    peak_multiplier: float = 1.5
    work_hours: List[int] = field(default_factory=lambda: list(range(9, 19)))
    work_multiplier: float = 0.7
    morning_hours: List[int] = field(default_factory=lambda: [6, 7, 8])
    morning_multiplier: float = 0.4
    off_peak_hours: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    off_peak_multiplier: float = 0.05

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ActivityRhythm":
        data = data or {}
        base = cls()
        return cls(
            timezone=data.get("timezone", base.timezone),
            peak_hours=list(data.get("peak_hours", base.peak_hours)),
            peak_multiplier=float(data.get("peak_multiplier", base.peak_multiplier)),
            work_hours=list(data.get("work_hours", base.work_hours)),
            work_multiplier=float(data.get("work_multiplier", base.work_multiplier)),
            morning_hours=list(data.get("morning_hours", base.morning_hours)),
            morning_multiplier=float(data.get("morning_multiplier", base.morning_multiplier)),
            off_peak_hours=list(data.get("off_peak_hours", base.off_peak_hours)),
            off_peak_multiplier=float(data.get("off_peak_multiplier", base.off_peak_multiplier)),
        )


@dataclass
class ChannelSpec:
    """A communication channel available to agents in this scenario.

    ``engine_platform`` maps the (possibly domain-specific) channel onto a
    platform the underlying engine can actually run — currently ``twitter`` or
    ``reddit`` for OASIS. This is what keeps novel domains runnable on the
    existing engine while presenting domain-appropriate framing to users.
    """

    id: str
    label: str = ""
    engine: str = "oasis"
    engine_platform: str = "twitter"
    recency_weight: float = 0.4
    popularity_weight: float = 0.3
    relevance_weight: float = 0.3
    viral_threshold: int = 10
    echo_chamber_strength: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChannelSpec":
        base = cls(id=str(data.get("id", "")))
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", data.get("id", ""))),
            engine=str(data.get("engine", base.engine)),
            engine_platform=str(data.get("engine_platform", base.engine_platform)),
            recency_weight=float(data.get("recency_weight", base.recency_weight)),
            popularity_weight=float(data.get("popularity_weight", base.popularity_weight)),
            relevance_weight=float(data.get("relevance_weight", base.relevance_weight)),
            viral_threshold=int(data.get("viral_threshold", base.viral_threshold)),
            echo_chamber_strength=float(data.get("echo_chamber_strength", base.echo_chamber_strength)),
        )


@dataclass
class ScenarioPreset:
    """A complete, config-driven description of a simulation domain."""

    id: str
    name: str = ""
    description: str = ""
    domain: str = "custom"
    activity_rhythm: ActivityRhythm = field(default_factory=ActivityRhythm)
    default_total_hours: int = 72
    default_minutes_per_round: int = 60
    channels: List[ChannelSpec] = field(default_factory=list)
    stances: List[str] = field(default_factory=lambda: ["supportive", "opposing", "neutral", "observer"])
    default_stance: str = "neutral"
    prompt_framing: str = ""
    tags: List[str] = field(default_factory=list)
    builtin: bool = False

    # ---- serialisation -------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "activity_rhythm": self.activity_rhythm.to_dict(),
            "default_total_hours": self.default_total_hours,
            "default_minutes_per_round": self.default_minutes_per_round,
            "channels": [c.to_dict() for c in self.channels],
            "stances": list(self.stances),
            "default_stance": self.default_stance,
            "prompt_framing": self.prompt_framing,
            "tags": list(self.tags),
            "builtin": self.builtin,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], builtin: bool = False) -> "ScenarioPreset":
        preset = cls(id=str(data["id"]))
        preset.name = str(data.get("name", data["id"]))
        preset.description = str(data.get("description", ""))
        preset.domain = str(data.get("domain", "custom"))
        preset.activity_rhythm = ActivityRhythm.from_dict(data.get("activity_rhythm"))
        preset.default_total_hours = int(data.get("default_total_hours", 72))
        preset.default_minutes_per_round = int(data.get("default_minutes_per_round", 60))
        preset.channels = [ChannelSpec.from_dict(c) for c in data.get("channels", [])]
        preset.stances = list(data.get("stances", preset.stances))
        preset.default_stance = str(data.get("default_stance", "neutral"))
        preset.prompt_framing = str(data.get("prompt_framing", ""))
        preset.tags = list(data.get("tags", []))
        preset.builtin = bool(data.get("builtin", builtin))
        return preset

    # ---- validation ----------------------------------------------------
    def validate(self) -> List[str]:
        """Return a list of human-readable problems (empty == valid)."""
        errors: List[str] = []
        if not self.id:
            errors.append("preset id is required")
        if not self.channels:
            errors.append(f"preset '{self.id}' has no channels")
        seen_ids = set()
        for ch in self.channels:
            if not ch.id:
                errors.append(f"preset '{self.id}' has a channel with no id")
            elif ch.id in seen_ids:
                errors.append(f"preset '{self.id}' has duplicate channel id '{ch.id}'")
            seen_ids.add(ch.id)
            if ch.engine_platform not in ENGINE_PLATFORMS:
                errors.append(
                    f"preset '{self.id}' channel '{ch.id}' has unsupported "
                    f"engine_platform '{ch.engine_platform}' (expected one of {ENGINE_PLATFORMS})"
                )
        if self.default_total_hours <= 0:
            errors.append(f"preset '{self.id}' default_total_hours must be > 0")
        if self.default_minutes_per_round <= 0:
            errors.append(f"preset '{self.id}' default_minutes_per_round must be > 0")
        if self.stances and self.default_stance not in self.stances:
            errors.append(
                f"preset '{self.id}' default_stance '{self.default_stance}' "
                f"is not in stances {self.stances}"
            )
        return errors

    # ---- engine-platform helpers --------------------------------------
    def engine_platforms(self) -> List[str]:
        """Distinct engine platforms this scenario maps onto, ordered."""
        ordered: List[str] = []
        for ch in self.channels:
            if ch.engine_platform not in ordered:
                ordered.append(ch.engine_platform)
        return ordered

    def uses_platform(self, engine_platform: str) -> bool:
        return any(ch.engine_platform == engine_platform for ch in self.channels)

    def channel_for_platform(self, engine_platform: str) -> Optional[ChannelSpec]:
        """First channel mapping onto the given engine platform, if any."""
        for ch in self.channels:
            if ch.engine_platform == engine_platform:
                return ch
        return None
