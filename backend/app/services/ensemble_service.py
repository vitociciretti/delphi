"""
Ensemble runs: the distribution of *outcomes*, not just of opinions.

One run of a stochastic simulation is an anecdote. An ensemble runs the
same prepared configuration under N different seeds and aggregates the
final opinion snapshots into an outcome distribution: how often does the
population reach consensus? where does the final mean land? how far apart
do runs diverge (cross-run controversy)?

Mechanics:

- Variant runs are sibling simulation directories named
  ``<parent>__v<k>`` that share the parent's ``simulation_config.json``.
  Each carries an ``ensemble.json`` tag ``{ensemble_id, seed, parent}``.
- Engines with ``capabilities.ensembles`` (the market engine) execute all
  variants synchronously in-process — dozens of runs take milliseconds.
- For subprocess engines (OASIS) the fan-out is a deliberate no-go here:
  every variant is a full LLM-driven run costing real money, so this
  module only *aggregates* whatever variant runs the user has started;
  it never silently launches them.

Aggregation reads each member's opinion timeline (and market timeline if
present) and reduces final-round metrics across members.
"""

import json
import math
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger
from .opinion_tracker import HIST_BINS, compute_opinion_timeline

logger = get_logger('mirofish.ensemble')

ENSEMBLE_FILE = "ensemble.json"
CONSENSUS_THRESHOLD = 0.55  # snapshot.consensus above this counts as "consensus reached"


def _sim_root() -> str:
    from .simulation_runner import SimulationRunner

    return SimulationRunner.RUN_STATE_DIR


def create_ensemble(
    simulation_id: str,
    variants: int = 8,
    base_seed: int = 1,
    max_rounds: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create (and, for in-process engines, immediately run) an ensemble.

    Returns the ensemble descriptor with member ids and per-member status.
    """
    if variants < 2 or variants > 64:
        raise ValueError("variants must be between 2 and 64")

    root = _sim_root()
    parent_dir = os.path.join(root, simulation_id)
    config_path = os.path.join(parent_dir, "simulation_config.json")
    if not os.path.exists(config_path):
        raise ValueError(f"simulation config not found for {simulation_id}")

    from .intervention_service import _resolve_engine_and_channels

    engine, _ = _resolve_engine_and_channels(simulation_id)
    if not engine.capabilities.ensembles:
        raise ValueError(
            f"engine '{engine.engine_id}' cannot auto-run ensembles (each variant is a "
            "full LLM run); start variant runs individually, then aggregate with "
            "get_ensemble_outcomes()"
        )

    ensemble_id = f"ens_{simulation_id}"
    members = []
    for k in range(variants):
        member_id = f"{simulation_id}__v{k}"
        member_dir = os.path.join(root, member_id)
        os.makedirs(member_dir, exist_ok=True)
        shutil.copy2(config_path, os.path.join(member_dir, "simulation_config.json"))
        # variants inherit the parent's scenario, so engine resolution works
        parent_state = os.path.join(parent_dir, "state.json")
        if os.path.exists(parent_state):
            shutil.copy2(parent_state, os.path.join(member_dir, "state.json"))
        seed = base_seed + k
        with open(os.path.join(member_dir, ENSEMBLE_FILE), 'w', encoding='utf-8') as f:
            json.dump({
                "ensemble_id": ensemble_id,
                "parent_simulation_id": simulation_id,
                "seed": seed,
                "variant": k,
            }, f, ensure_ascii=False, indent=2)

        status = engine.run_sync(member_id, seed=seed, max_rounds=max_rounds)
        members.append({
            "simulation_id": member_id,
            "seed": seed,
            "status": status.get("runner_status"),
        })

    descriptor = {
        "ensemble_id": ensemble_id,
        "parent_simulation_id": simulation_id,
        "engine": engine.engine_id,
        "variants": variants,
        "base_seed": base_seed,
        "members": members,
        "created_at": datetime.now().isoformat(),
    }
    with open(os.path.join(parent_dir, ENSEMBLE_FILE), 'w', encoding='utf-8') as f:
        json.dump(descriptor, f, ensure_ascii=False, indent=2)
    logger.info("ensemble %s: %d variants run on engine %s",
                ensemble_id, variants, engine.engine_id)
    return descriptor


def _find_members(simulation_id: str) -> List[str]:
    """Member sim ids for an ensemble parent (from descriptor or naming)."""
    root = _sim_root()
    descriptor_path = os.path.join(root, simulation_id, ENSEMBLE_FILE)
    if os.path.exists(descriptor_path):
        try:
            with open(descriptor_path, 'r', encoding='utf-8') as f:
                descriptor = json.load(f)
            members = [m["simulation_id"] for m in descriptor.get("members", [])]
            if members:
                return members
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    # fallback: directory naming convention
    prefix = f"{simulation_id}__v"
    if not os.path.isdir(root):
        return []
    return sorted(
        name for name in os.listdir(root)
        if name.startswith(prefix) and os.path.isdir(os.path.join(root, name))
    )


def get_ensemble_outcomes(simulation_id: str) -> Dict[str, Any]:
    """
    Aggregate final-round opinion metrics across ensemble members.

    Returns per-member outcomes plus the cross-run distribution: final
    mean-stance histogram, consensus probability, polarization stats and
    a divergence score (std of final means across runs — high divergence
    means the *outcome itself* is controversial).
    """
    members = _find_members(simulation_id)
    if not members:
        raise ValueError(f"no ensemble members found for {simulation_id}")

    root = _sim_root()
    outcomes = []
    for member_id in members:
        member_dir = os.path.join(root, member_id)
        try:
            timeline = compute_opinion_timeline(member_dir)
        except ValueError:
            continue
        rounds = timeline.get("rounds", [])
        if not rounds:
            continue
        final = rounds[-1]
        outcome = {
            "simulation_id": member_id,
            "final_round": final["round_num"],
            "mean": final["mean"],
            "std": final["std"],
            "polarization": final["polarization"],
            "consensus": final["consensus"],
            "clusters": final["clusters"],
            "reached_consensus": final["consensus"] >= CONSENSUS_THRESHOLD,
        }
        seed_path = os.path.join(member_dir, ENSEMBLE_FILE)
        if os.path.exists(seed_path):
            try:
                with open(seed_path, 'r', encoding='utf-8') as f:
                    outcome["seed"] = json.load(f).get("seed")
            except (json.JSONDecodeError, OSError):
                pass
        market_path = os.path.join(member_dir, "market_timeline.json")
        if os.path.exists(market_path):
            try:
                with open(market_path, 'r', encoding='utf-8') as f:
                    market = json.load(f)
                if market:
                    outcome["final_price"] = market[-1]["price"]
            except (json.JSONDecodeError, OSError, KeyError):
                pass
        outcomes.append(outcome)

    if not outcomes:
        raise ValueError(f"no completed ensemble members for {simulation_id}")

    n = len(outcomes)
    means = [o["mean"] for o in outcomes]
    grand_mean = sum(means) / n
    divergence = math.sqrt(sum((m - grand_mean) ** 2 for m in means) / n)

    histogram = [0] * HIST_BINS
    for m in means:
        idx = min(HIST_BINS - 1, int((max(-1.0, min(1.0, m)) + 1.0) / 2.0 * HIST_BINS))
        histogram[idx] += 1

    prices = [o["final_price"] for o in outcomes if "final_price" in o]

    return {
        "parent_simulation_id": simulation_id,
        "members_count": n,
        "outcomes": outcomes,
        "distribution": {
            "final_mean_histogram": histogram,
            "grand_mean": round(grand_mean, 4),
            "divergence": round(divergence, 4),
            "consensus_probability": round(
                sum(1 for o in outcomes if o["reached_consensus"]) / n, 4
            ),
            "mean_polarization": round(
                sum(o["polarization"] for o in outcomes) / n, 4
            ),
            "price_range": (
                {"min": min(prices), "max": max(prices),
                 "mean": round(sum(prices) / len(prices), 4)}
                if prices else None
            ),
        },
        "computed_at": datetime.now().isoformat(),
    }
