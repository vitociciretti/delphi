"""
Irrationality modeling package for Delphi simulations.

Adds explicit, tunable models of non-rational human behavior on top of the
LLM-driven OASIS agents:

- traits:       per-agent psychological trait vectors (biases, credulity,
                conformity, impulsivity, need-for-cognition) rendered into
                agent system prompts
- affect:       dynamic emotion state (arousal / valence / fatigue) updated
                from feed exposure, coupled to activation probability and
                prompt context
- opinion:      opinion-dynamics equations (Deffuant / Hegselmann-Krause /
                DeGroot) governing numeric per-topic opinion states that are
                rendered back into prompts
- perception:   biased feed perception (confirmation / negativity re-ranking)
                via a SocialEnvironment subclass -- no OASIS fork required
- dual_process: System-1 (fast, hot, instinctive) vs System-2 (deliberate)
                routing per activation
- probes:       bias-probe measurement harness (anchoring, conformity,
                framing, rumor chain) run through INTERVIEW actions
- engine:       orchestrator exposing three hooks for the runner scripts:
                before_round / build_actions / after_round

Everything is opt-in via the `irrationality_config` block of
simulation_config.json (master switch `enabled`, default off).
"""

from .engine import IrrationalityEngine
from .traits import PsychProfile, BIAS_LIBRARY
from .affect import AffectState, score_emotional_charge
from .opinion import OpinionDynamics, polarization_metrics

__all__ = [
    "IrrationalityEngine",
    "PsychProfile",
    "BIAS_LIBRARY",
    "AffectState",
    "score_emotional_charge",
    "OpinionDynamics",
    "polarization_metrics",
]
