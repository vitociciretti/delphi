"""
Opinion tracker: turns raw action logs into evolving stance distributions.

The engine (OASIS or market) produces a chronological action log per
channel. This module replays those actions through a lightweight,
deterministic opinion-dynamics model and emits a per-round snapshot of the
population's stance distribution — the data behind "where is consensus,
what is controversial, which minority views are growing".

Model
-----
Every agent carries a continuous stance ``s ∈ [-1, 1]`` (initialised from
its categorical stance label in ``simulation_config.json`` via the
scenario vocabulary) plus a confidence ``c ∈ [0, 1]``.

Replaying the log:

- **Expression** (CREATE_POST / CREATE_COMMENT / PLACE_ORDER, ...):
  the agent re-commits to its view — confidence rises, stance drifts
  slightly back toward its prior (people rarely argue themselves away
  from their own posts). If the action carries an explicit ``sentiment``
  in its args (market engine does this), the stance snaps to it.
- **Endorsement** (LIKE / UPVOTE / REPOST / RETWEET, ...): the actor moves
  toward the endorsed author's stance — but only within a bounded-
  confidence window (you aren't persuaded by people too far from you).
  When the target author cannot be resolved from the log, the actor moves
  toward the round's *expressed mean* instead (mean-field herding).
- **Rejection** (DISLIKE / DOWNVOTE, ...): the actor moves *away* from the
  target (or the expressed mean), a small backfire step.

After each round we snapshot: histogram, mean, std, a polarization index,
a consensus score, opinion clusters (1-D gap clustering) and which of them
are minorities. Cluster centroids are matched across rounds so the
frontend can draw minority trajectories.

The model is a deliberate heuristic — deterministic, free, and testable —
not an LLM judgment. It measures the *shape* of opinion flow through the
interaction structure, which is what the distribution charts need. An
LLM-scored variant can be layered on later via the ``sentiment`` hook
that the market engine already uses.
"""

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..utils.logger import get_logger

logger = get_logger('mirofish.opinion')

# Categorical stance label -> (stance value, initial confidence).
# Covers every builtin scenario vocabulary; unknown labels -> (0, 0.3).
STANCE_MAP: Dict[str, Tuple[float, float]] = {
    "supportive": (0.7, 0.7), "opposing": (-0.7, 0.7), "neutral": (0.0, 0.4),
    "observer": (0.0, 0.2),
    "bullish": (0.7, 0.7), "bearish": (-0.7, 0.7), "hedging": (-0.15, 0.4),
    "advocate": (0.7, 0.8), "skeptic": (-0.5, 0.6), "blocker": (-0.85, 0.9),
    "allied": (0.7, 0.7), "opposed": (-0.7, 0.7), "conflicted": (0.0, 0.3),
    "detached": (0.0, 0.2),
}

EXPRESSION_ACTIONS = {
    "CREATE_POST", "CREATE_COMMENT", "PLACE_ORDER", "QUOTE_POST", "REPLY",
}
ENDORSE_ACTIONS = {
    "LIKE_POST", "LIKE_COMMENT", "UPVOTE_POST", "UPVOTE_COMMENT",
    "LIKE", "UPVOTE", "REPOST", "RETWEET", "SHARE",
}
REJECT_ACTIONS = {
    "DISLIKE_POST", "DISLIKE_COMMENT", "DOWNVOTE_POST", "DOWNVOTE_COMMENT",
    "DISLIKE", "DOWNVOTE",
}

HIST_BINS = 21  # odd -> a true center bin at 0


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class _AgentOpinion:
    agent_id: int
    name: str
    prior: float
    stance: float
    confidence: float
    expressed: int = 0


@dataclass
class OpinionSnapshot:
    """Distribution state at the end of one round."""

    round_num: int
    mean: float
    std: float
    polarization: float          # 0 (single peak) .. 1 (two opposed camps)
    consensus: float             # 0 (fragmented) .. 1 (everyone together)
    histogram: List[int]         # HIST_BINS counts over [-1, 1]
    clusters: List[Dict[str, Any]] = field(default_factory=list)
    minorities: List[Dict[str, Any]] = field(default_factory=list)
    interventions: List[Dict[str, Any]] = field(default_factory=list)
    agent_stances: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "mean": round(self.mean, 4),
            "std": round(self.std, 4),
            "polarization": round(self.polarization, 4),
            "consensus": round(self.consensus, 4),
            "histogram": self.histogram,
            "clusters": self.clusters,
            "minorities": self.minorities,
            "interventions": self.interventions,
            "agent_stances": self.agent_stances,
        }


class OpinionTracker:
    """Replays an action log into per-round opinion snapshots."""

    ETA_ENDORSE = 0.15        # pull toward an endorsed stance
    ETA_REJECT = 0.08         # push away from a rejected stance
    ETA_EXPRESS = 0.10        # drift back toward own prior when expressing
    CONFIDENCE_STEP = 0.05
    BOUNDED_CONFIDENCE = 0.9  # max distance at which persuasion still works
    CLUSTER_GAP = 0.22        # 1-D gap that separates opinion clusters
    MINORITY_SHARE = 0.25     # clusters below this share are minorities

    def __init__(self, agent_configs: List[Dict[str, Any]]):
        self.agents: Dict[int, _AgentOpinion] = {}
        for cfg in agent_configs:
            agent_id = cfg.get("agent_id")
            if agent_id is None:
                continue
            label = str(cfg.get("stance", "neutral")).lower()
            stance, confidence = STANCE_MAP.get(label, (0.0, 0.3))
            self.agents[int(agent_id)] = _AgentOpinion(
                agent_id=int(agent_id),
                name=cfg.get("agent_name", cfg.get("name", f"agent_{agent_id}")),
                prior=stance,
                stance=stance,
                confidence=confidence,
            )

    # ------------------------------------------------------------------
    # replay
    # ------------------------------------------------------------------

    def replay(
        self,
        actions: List[Dict[str, Any]],
        events: Optional[List[Dict[str, Any]]] = None,
    ) -> List[OpinionSnapshot]:
        """
        Replay a chronological action log.

        ``actions`` are agent-action dicts (the non-event lines of
        actions.jsonl); ``events`` may carry intervention markers.
        Returns one snapshot per round seen in the log.
        """
        if not self.agents:
            return []

        by_round: Dict[int, List[Dict[str, Any]]] = {}
        for action in actions:
            by_round.setdefault(int(action.get("round", 0)), []).append(action)

        interventions_by_round: Dict[int, List[Dict[str, Any]]] = {}
        for ev in events or []:
            if ev.get("event_type") == "intervention":
                interventions_by_round.setdefault(int(ev.get("round", 0)), []).append({
                    "text": ev.get("text", ""),
                    "magnitude": ev.get("magnitude"),
                    "timestamp": ev.get("timestamp"),
                })

        snapshots: List[OpinionSnapshot] = []
        prev_clusters: List[Dict[str, Any]] = []
        # authorship map: post/comment id -> author agent_id, built as we go
        authors: Dict[str, int] = {}

        max_round = max(
            list(by_round.keys()) + list(interventions_by_round.keys()) + [0]
        )
        for round_num in range(0, max_round + 1):
            round_actions = by_round.get(round_num, [])
            expressed_stances: List[float] = []

            for action in round_actions:
                agent = self.agents.get(int(action.get("agent_id", -1)))
                if agent is None:
                    continue
                a_type = str(action.get("action_type", "")).upper()
                args = action.get("action_args") or {}

                if a_type in EXPRESSION_ACTIONS:
                    explicit = args.get("sentiment")
                    if explicit is not None:
                        try:
                            agent.stance = _clamp(float(explicit))
                        except (TypeError, ValueError):
                            pass
                    else:
                        agent.stance = _clamp(
                            agent.stance + self.ETA_EXPRESS * (agent.prior - agent.stance)
                        )
                    agent.confidence = _clamp(
                        agent.confidence + self.CONFIDENCE_STEP, 0.0, 1.0
                    )
                    agent.expressed += 1
                    expressed_stances.append(agent.stance)
                    self._remember_authorship(authors, action, agent.agent_id)

                elif a_type in ENDORSE_ACTIONS or a_type in REJECT_ACTIONS:
                    target = self._resolve_target_stance(
                        authors, args, expressed_stances
                    )
                    if target is None:
                        continue
                    diff = target - agent.stance
                    if a_type in ENDORSE_ACTIONS:
                        if abs(diff) <= self.BOUNDED_CONFIDENCE:
                            # low-confidence agents move more
                            step = self.ETA_ENDORSE * (1.2 - agent.confidence)
                            agent.stance = _clamp(agent.stance + step * diff)
                            if abs(diff) > 0.3:
                                # endorsing content far from your own view is
                                # a signal of openness: confidence drops,
                                # keeping the agent persuadable instead of
                                # freezing mid-journey
                                agent.confidence = _clamp(
                                    agent.confidence - self.CONFIDENCE_STEP, 0.0, 1.0
                                )
                            else:
                                agent.confidence = _clamp(
                                    agent.confidence + self.CONFIDENCE_STEP / 2, 0.0, 1.0
                                )
                    else:
                        agent.stance = _clamp(
                            agent.stance - self.ETA_REJECT * math.copysign(1.0, diff)
                            * min(1.0, abs(diff))
                        )
                        agent.confidence = _clamp(
                            agent.confidence + self.CONFIDENCE_STEP / 2, 0.0, 1.0
                        )

            snapshot = self._snapshot(
                round_num,
                interventions_by_round.get(round_num, []),
                prev_clusters,
            )
            prev_clusters = snapshot.clusters
            snapshots.append(snapshot)

        return snapshots

    @staticmethod
    def _remember_authorship(
        authors: Dict[str, int], action: Dict[str, Any], agent_id: int
    ) -> None:
        args = action.get("action_args") or {}
        result = action.get("result")
        for key in ("post_id", "comment_id"):
            value = args.get(key)
            if value is not None:
                authors[f"{key}:{value}"] = agent_id
        # OASIS often returns created ids in result dicts
        if isinstance(result, dict):
            for key in ("post_id", "comment_id"):
                value = result.get(key)
                if value is not None:
                    authors[f"{key}:{value}"] = agent_id

    def _resolve_target_stance(
        self,
        authors: Dict[str, int],
        args: Dict[str, Any],
        expressed_stances: List[float],
    ) -> Optional[float]:
        """Stance being endorsed/rejected: author's stance if resolvable,
        else the mean stance expressed so far this round (mean-field)."""
        for key in ("post_id", "comment_id"):
            value = args.get(key)
            if value is not None:
                author_id = authors.get(f"{key}:{value}")
                if author_id is not None and author_id in self.agents:
                    return self.agents[author_id].stance
        if expressed_stances:
            return sum(expressed_stances) / len(expressed_stances)
        return None

    # ------------------------------------------------------------------
    # metrics
    # ------------------------------------------------------------------

    def _snapshot(
        self,
        round_num: int,
        interventions: List[Dict[str, Any]],
        prev_clusters: List[Dict[str, Any]],
    ) -> OpinionSnapshot:
        values = sorted(a.stance for a in self.agents.values())
        n = len(values)
        mean = sum(values) / n
        std = math.sqrt(sum((v - mean) ** 2 for v in values) / n)

        histogram = [0] * HIST_BINS
        for v in values:
            idx = min(HIST_BINS - 1, int((v + 1.0) / 2.0 * HIST_BINS))
            histogram[idx] += 1

        clusters = self._find_clusters(values)
        self._label_clusters(clusters, prev_clusters)
        minorities = [
            c for c in clusters
            if c["share"] < self.MINORITY_SHARE and len(clusters) > 1
        ]

        return OpinionSnapshot(
            round_num=round_num,
            mean=mean,
            std=std,
            polarization=self._polarization(values, mean),
            consensus=self._consensus(values, clusters),
            histogram=histogram,
            clusters=clusters,
            minorities=minorities,
            interventions=interventions,
            agent_stances={
                str(a.agent_id): round(a.stance, 4) for a in self.agents.values()
            },
        )

    @staticmethod
    def _polarization(values: List[float], mean: float) -> float:
        """
        Esteban-Ray-flavoured index: mass concentrated in mutually distant
        groups scores high; a single tight peak scores ~0. Normalised so
        a 50/50 split at -1/+1 -> 1.0.
        """
        n = len(values)
        if n < 2:
            return 0.0
        total = 0.0
        for i in range(n):
            for j in range(n):
                total += abs(values[i] - values[j])
        # max possible avg pairwise distance is 2 (half at -1, half at +1)
        return _clamp(total / (n * n) / 1.0, 0.0, 1.0)

    def _consensus(self, values: List[float], clusters: List[Dict[str, Any]]) -> float:
        """Share of the population inside the largest cluster, discounted
        by how spread that cluster is."""
        if not clusters:
            return 0.0
        top = max(clusters, key=lambda c: c["share"])
        return _clamp(top["share"] * (1.0 - top["spread"]), 0.0, 1.0)

    def _find_clusters(self, sorted_values: List[float]) -> List[Dict[str, Any]]:
        """1-D gap clustering: a gap wider than CLUSTER_GAP starts a new
        cluster. Cheap, deterministic, and good enough for [-1, 1]."""
        if not sorted_values:
            return []
        groups: List[List[float]] = [[sorted_values[0]]]
        for v in sorted_values[1:]:
            if v - groups[-1][-1] > self.CLUSTER_GAP:
                groups.append([v])
            else:
                groups[-1].append(v)
        n = len(sorted_values)
        clusters = []
        for g in groups:
            centroid = sum(g) / len(g)
            spread = (max(g) - min(g)) / 2.0
            clusters.append({
                "centroid": round(centroid, 4),
                "share": round(len(g) / n, 4),
                "size": len(g),
                "spread": round(spread, 4),
            })
        return clusters

    @staticmethod
    def _label_clusters(
        clusters: List[Dict[str, Any]], prev: List[Dict[str, Any]]
    ) -> None:
        """Give clusters stable ids by matching nearest centroids from the
        previous round, so the frontend can draw trajectories."""
        used = set()
        next_id = max((c.get("cluster_id", -1) for c in prev), default=-1) + 1
        for cluster in clusters:
            best = None
            best_dist = 0.35  # matching radius
            for p in prev:
                if p.get("cluster_id") in used:
                    continue
                d = abs(cluster["centroid"] - p["centroid"])
                if d < best_dist:
                    best, best_dist = p, d
            if best is not None:
                cluster["cluster_id"] = best["cluster_id"]
                used.add(best["cluster_id"])
            else:
                cluster["cluster_id"] = next_id
                next_id += 1


# ----------------------------------------------------------------------
# file-level API used by the insights endpoints
# ----------------------------------------------------------------------

def _read_log_lines(sim_dir: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Read every channel's actions.jsonl; split agent actions vs events."""
    actions: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    channels = []
    for name in sorted(os.listdir(sim_dir)) if os.path.isdir(sim_dir) else []:
        candidate = os.path.join(sim_dir, name, "actions.jsonl")
        if os.path.isfile(candidate):
            channels.append((name, candidate))
    # legacy single-file layout
    legacy = os.path.join(sim_dir, "actions.jsonl")
    if not channels and os.path.isfile(legacy):
        channels.append(("default", legacy))

    for channel, path in channels:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                data.setdefault("platform", channel)
                if "event_type" in data:
                    events.append(data)
                elif "agent_id" in data:
                    actions.append(data)

    actions.sort(key=lambda a: (int(a.get("round", 0)), str(a.get("timestamp", ""))))
    return actions, events


def compute_opinion_timeline(sim_dir: str, force: bool = False) -> Dict[str, Any]:
    """
    Compute (with mtime caching) the opinion timeline for a simulation dir.

    Returns ``{"rounds": [snapshot...], "agents": {id: name}, "computed_at": ...}``.
    """
    from datetime import datetime

    config_path = os.path.join(sim_dir, "simulation_config.json")
    if not os.path.exists(config_path):
        raise ValueError(f"simulation config not found in {sim_dir}")

    cache_path = os.path.join(sim_dir, "opinion_timeline.json")
    newest_source = os.path.getmtime(config_path)
    for name in os.listdir(sim_dir):
        candidate = os.path.join(sim_dir, name, "actions.jsonl")
        if os.path.isfile(candidate):
            newest_source = max(newest_source, os.path.getmtime(candidate))

    if not force and os.path.exists(cache_path):
        if os.path.getmtime(cache_path) >= newest_source:
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass  # fall through to recompute

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    tracker = OpinionTracker(config.get("agent_configs", []))
    actions, events = _read_log_lines(sim_dir)
    snapshots = tracker.replay(actions, events)

    timeline = {
        "rounds": [s.to_dict() for s in snapshots],
        "agents": {
            str(a.agent_id): a.name for a in tracker.agents.values()
        },
        "stance_vocabulary": sorted({
            str(c.get("stance", "neutral")).lower()
            for c in config.get("agent_configs", [])
        }),
        "computed_at": datetime.now().isoformat(),
    }
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(timeline, f, ensure_ascii=False)
    except OSError as exc:
        logger.warning("could not cache opinion timeline: %s", exc)
    return timeline
