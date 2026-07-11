"""
Opinion-dynamics framework (the hybrid equation + LLM layer).

Each agent carries a numeric opinion in [-1, 1] per topic. A classical,
decades-validated dynamics equation governs how those numbers move when an
agent is exposed to another agent's content; the LLM then role-plays *given*
the numeric state (the state is rendered into the agent's prompt each round).
This decouples "does the crowd behave right?" (equation, calibratable) from
"does each agent sound right?" (LLM).

Supported models:

- deffuant:  pairwise bounded confidence. For exposure (listener i, author j):
             if |o_i - o_j| < eps:  o_i += mu * (o_j - o_i)
- hk:        Hegselmann-Krause. o_i <- mean of {o_i} U {o_j : |o_i - o_j| < eps}
             over all authors i saw this round.
- degroot:   o_i <- w * o_i + (1 - w) * mean(o_j seen), no confidence bound.

Psych/affect couplings (the irrationality-specific part):
- credulity widens the confidence bound (credulous agents are open to
  anything), conformity scales the convergence rate mu.
- arousal narrows the confidence bound (agitated agents stop listening to
  the other side) -- this coupling is what turns charged events into
  polarization instead of consensus.

Exposure topics come from a keyword TopicClassifier over post content, with
authors' own opinion values as the influence signal (classical ABM style --
no per-post LLM scoring cost).
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_TOPIC = "main_event"


@dataclass
class Exposure:
    """Listener saw a piece of content by author on a topic."""
    listener_id: int
    author_id: int
    topic: str
    weight: float = 1.0  # e.g. engagement-derived salience


class TopicClassifier:
    """Keyword-based post -> topic assignment.

    Topics come from irrationality_config.opinion.topics:
    [{"id": "...", "keywords": ["...", ...]}, ...]. Content matching no
    topic keywords falls back to the first topic (the main event) so no
    exposure is dropped.
    """

    def __init__(self, topics: Optional[List[Dict[str, Any]]] = None):
        self.topics: List[Tuple[str, List[str]]] = []
        for t in topics or []:
            topic_id = str(t.get("id") or t.get("topic") or "").strip()
            if not topic_id:
                continue
            keywords = [str(k).lower() for k in (t.get("keywords") or []) if k]
            self.topics.append((topic_id, keywords))
        if not self.topics:
            self.topics = [(DEFAULT_TOPIC, [])]

    @property
    def topic_ids(self) -> List[str]:
        return [tid for tid, _ in self.topics]

    def classify(self, text: str) -> str:
        if text:
            lower = str(text).lower()
            best_id, best_hits = None, 0
            for topic_id, keywords in self.topics:
                hits = sum(1 for k in keywords if k and k in lower)
                if hits > best_hits:
                    best_id, best_hits = topic_id, hits
        else:
            best_id = None
        return best_id or self.topics[0][0]


class OpinionDynamics:
    """Opinion state for the whole population + one update model."""

    MODELS = ("deffuant", "hk", "degroot")

    def __init__(
        self,
        model: str = "deffuant",
        epsilon: float = 0.4,
        mu: float = 0.3,
        self_weight: float = 0.7,
        affect_coupling: float = 0.5,
        topics: Optional[List[str]] = None,
    ):
        if model not in self.MODELS:
            raise ValueError(f"Unknown opinion model: {model!r} (use one of {self.MODELS})")
        self.model = model
        self.epsilon = float(epsilon)
        self.mu = float(mu)
        self.self_weight = float(self_weight)
        self.affect_coupling = float(affect_coupling)
        self.topic_ids = list(topics) if topics else [DEFAULT_TOPIC]
        # opinions[agent_id][topic] in [-1, 1]
        self.opinions: Dict[int, Dict[str, float]] = {}

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def init_agent(self, agent_id: int, initial: Dict[str, float]) -> None:
        self.opinions[agent_id] = {
            t: _clip(initial.get(t, 0.0)) for t in self.topic_ids
        }

    # ------------------------------------------------------------------
    # Dynamics step
    # ------------------------------------------------------------------

    def step(
        self,
        exposures: Iterable[Exposure],
        credulity: Optional[Dict[int, float]] = None,
        conformity: Optional[Dict[int, float]] = None,
        arousal: Optional[Dict[int, float]] = None,
    ) -> int:
        """Apply one round of exposures. Returns number of opinion updates.

        All three models consume the same exposure list; deffuant applies
        pairwise sequentially, hk/degroot aggregate per (listener, topic).
        """
        credulity = credulity or {}
        conformity = conformity or {}
        arousal = arousal or {}
        exposures = [e for e in exposures if self._valid(e)]
        updates = 0

        if self.model == "deffuant":
            for e in exposures:
                o_i = self.opinions[e.listener_id][e.topic]
                o_j = self.opinions[e.author_id][e.topic]
                eps = self._effective_epsilon(e.listener_id, credulity, arousal)
                if abs(o_i - o_j) < eps:
                    mu = self._effective_mu(e.listener_id, conformity) * min(1.0, e.weight)
                    self.opinions[e.listener_id][e.topic] = _clip(o_i + mu * (o_j - o_i))
                    updates += 1
            return updates

        # hk / degroot: aggregate neighbor opinions per listener+topic.
        buckets: Dict[Tuple[int, str], List[Tuple[float, float]]] = {}
        for e in exposures:
            o_j = self.opinions[e.author_id][e.topic]
            buckets.setdefault((e.listener_id, e.topic), []).append((o_j, e.weight))

        for (listener_id, topic), neighbor_ops in buckets.items():
            o_i = self.opinions[listener_id][topic]
            if self.model == "hk":
                eps = self._effective_epsilon(listener_id, credulity, arousal)
                in_bound = [(o, w) for o, w in neighbor_ops if abs(o - o_i) < eps]
                if not in_bound:
                    continue
                total_w = sum(w for _, w in in_bound) + 1.0  # self weight 1
                new = (o_i + sum(o * w for o, w in in_bound)) / total_w
            else:  # degroot
                total_w = sum(w for _, w in neighbor_ops)
                if total_w <= 0:
                    continue
                neighbor_mean = sum(o * w for o, w in neighbor_ops) / total_w
                w_self = self._effective_self_weight(listener_id, conformity)
                new = w_self * o_i + (1.0 - w_self) * neighbor_mean
            if abs(new - o_i) > 1e-9:
                self.opinions[listener_id][topic] = _clip(new)
                updates += 1
        return updates

    # ------------------------------------------------------------------
    # Couplings
    # ------------------------------------------------------------------

    def _effective_epsilon(
        self, agent_id: int,
        credulity: Dict[int, float],
        arousal: Dict[int, float],
    ) -> float:
        eps = self.epsilon
        # Credulous agents accept distant opinions; skeptics don't.
        eps *= 0.6 + 0.8 * credulity.get(agent_id, 0.5)
        # Agitation narrows the bound (motivated rejection of the other side).
        eps *= 1.0 - self.affect_coupling * arousal.get(agent_id, 0.0) * 0.7
        return max(0.05, min(2.0, eps))

    def _effective_mu(self, agent_id: int, conformity: Dict[int, float]) -> float:
        return max(0.02, min(0.5, self.mu * (0.5 + conformity.get(agent_id, 0.5))))

    def _effective_self_weight(self, agent_id: int, conformity: Dict[int, float]) -> float:
        # High conformity -> lower self weight.
        w = self.self_weight * (1.2 - 0.4 * conformity.get(agent_id, 0.5))
        return max(0.3, min(0.95, w))

    def _valid(self, e: Exposure) -> bool:
        return (
            e.listener_id in self.opinions
            and e.author_id in self.opinions
            and e.topic in self.opinions[e.listener_id]
            and e.listener_id != e.author_id
        )

    # ------------------------------------------------------------------
    # Rendering + metrics
    # ------------------------------------------------------------------

    def render_prompt_section(self, agent_id: int, topic_labels: Optional[Dict[str, str]] = None) -> str:
        """Verbalize an agent's current numeric opinions for its prompt."""
        ops = self.opinions.get(agent_id)
        if not ops:
            return ""
        topic_labels = topic_labels or {}
        parts = []
        for topic, value in ops.items():
            label = topic_labels.get(topic, topic.replace("_", " "))
            parts.append(f"On '{label}', {_verbalize(value)}.")
        return "Current convictions: " + " ".join(parts)

    def snapshot(self) -> Dict[str, Any]:
        return {
            str(aid): {t: round(v, 4) for t, v in ops.items()}
            for aid, ops in self.opinions.items()
        }


def polarization_metrics(values: List[float]) -> Dict[str, float]:
    """Population-level metrics for one topic: mean, variance/std, and the
    bimodality coefficient (BC > ~0.555 suggests a bimodal i.e. polarized
    distribution)."""
    n = len(values)
    if n < 3:
        return {"n": n, "mean": _mean(values), "std": 0.0, "bimodality": 0.0}
    mean = _mean(values)
    m2 = _mean([(v - mean) ** 2 for v in values])
    std = math.sqrt(m2)
    if std < 1e-9:
        return {"n": n, "mean": mean, "std": 0.0, "bimodality": 0.0}
    m3 = _mean([(v - mean) ** 3 for v in values])
    m4 = _mean([(v - mean) ** 4 for v in values])
    skew = m3 / (std ** 3)
    excess_kurt = m4 / (std ** 4) - 3.0  # BC formula uses EXCESS kurtosis
    denom = excess_kurt + 3.0 * ((n - 1) ** 2) / max((n - 2) * (n - 3), 1)
    bimodality = (skew ** 2 + 1.0) / denom if denom > 0 else 0.0
    return {
        "n": n,
        "mean": round(mean, 4),
        "std": round(std, 4),
        "bimodality": round(bimodality, 4),
    }


def _verbalize(value: float) -> str:
    if value <= -0.75:
        return "you are firmly, vocally opposed"
    if value <= -0.4:
        return "you are opposed"
    if value <= -0.15:
        return "you lean skeptical"
    if value < 0.15:
        return "you are undecided"
    if value < 0.4:
        return "you lean supportive"
    if value < 0.75:
        return "you are supportive"
    return "you are firmly, vocally supportive"


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _clip(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))
