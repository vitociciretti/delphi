"""
Dual-process (System 1 / System 2) routing.

Per activation, an agent either reacts on the fast path (System 1: hot
sampling temperature, truncated feed, explicit gut-reaction instruction) or
deliberates on the default path (System 2). The routing probability is a
function of the agent's current arousal (state) and its impulsivity /
need-for-cognition traits -- agitated, impulsive agents think less.

Mechanically: a shared high-temperature model backend is swapped into
`agent.model_backend` for the round (camel's ChatAgent reads that attribute
on every call), and the agent's PsychSocialEnvironment gets `s1_mode=True`
so the prompt side changes too. Both are restored for System-2 rounds.
"""

import random
from typing import Any, Dict, Optional

from camel.models import ModelFactory, ModelManager
from camel.types import ModelPlatformType


class DualProcessRouter:
    def __init__(
        self,
        llm_model: str,
        base_s1_prob: float = 0.3,
        arousal_weight: float = 0.5,
        impulsivity_weight: float = 0.3,
        cognition_weight: float = 0.4,
        s1_temperature: float = 1.2,
        intensity: float = 1.0,
        rng: Optional[random.Random] = None,
    ):
        self.base_s1_prob = float(base_s1_prob)
        self.arousal_weight = float(arousal_weight)
        self.impulsivity_weight = float(impulsivity_weight)
        self.cognition_weight = float(cognition_weight)
        self.intensity = float(intensity)
        self.rng = rng or random.Random()
        # One shared hot backend for every agent's System-1 rounds (agents
        # already share the default backend, so sharing is the norm here).
        s1_backend = ModelFactory.create(
            model_platform=ModelPlatformType.OPENAI,
            model_type=llm_model,
            model_config_dict={"temperature": s1_temperature},
        )
        self._s1_manager = ModelManager([s1_backend])
        # agent_id -> original ModelManager, captured on first swap.
        self._original_backends: Dict[int, Any] = {}

    # ------------------------------------------------------------------

    def s1_probability(self, psych, affect) -> float:
        """P(System 1) for this activation. Pure function -- unit-testable."""
        p = (
            self.base_s1_prob
            + self.arousal_weight * affect.arousal
            + self.impulsivity_weight * psych.impulsivity
            - self.cognition_weight * psych.need_for_cognition
        )
        return max(0.0, min(0.95, p * self.intensity))

    def route(self, psych, affect) -> str:
        return "s1" if self.rng.random() < self.s1_probability(psych, affect) else "s2"

    def apply(self, agent, mode: str) -> None:
        """Swap model backend + env flag for this round."""
        agent_id = getattr(agent, "social_agent_id", None)
        if agent_id is None:
            return
        if agent_id not in self._original_backends:
            self._original_backends[agent_id] = agent.model_backend
        if mode == "s1":
            agent.model_backend = self._s1_manager
        else:
            agent.model_backend = self._original_backends[agent_id]
        env = getattr(agent, "env", None)
        if env is not None and hasattr(env, "s1_mode"):
            env.s1_mode = (mode == "s1")
