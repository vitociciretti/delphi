"""
Demo run generator: a complete, synthetic simulation on disk.

Real runs need LLM + Zep credentials and minutes-to-hours of wall clock.
This module fabricates a plausible finished run in milliseconds — same
directory layout, same actions.jsonl schema, same state files — so the
outcomes dashboard, SSE stream, interventions panel and ensemble view can
be demonstrated (and integration-tested) with zero external services.

Two flavours:

- ``social``: a scripted two-camp public-opinion story on twitter+reddit.
  Echo chambers hold for the first act; a mid-run intervention (a
  "revelation" post) starts pulling the undecided toward one camp while a
  hard core keeps disliking it — so the dashboard shows polarization,
  a consensus shift, and a persistent minority. All logged actions are
  ordinary posts/likes/dislikes: the opinion dynamics emerge from the
  OpinionTracker replaying them, not from precomputed numbers.
- ``market``: a genuine MarketEngine run (it's already fast and
  deterministic), plus an ensemble for the outcome-distribution view.
"""

import json
import os
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger('mirofish.demo')

FIRST_NAMES = [
    "Ava", "Ben", "Chloe", "Dan", "Elif", "Farid", "Gina", "Hugo", "Iris",
    "Jon", "Kira", "Liam", "Mona", "Nico", "Omar", "Pia", "Quinn", "Rosa",
    "Sam", "Tara", "Uri", "Vera", "Wes", "Xena",
]


def _sim_root() -> str:
    from .simulation_runner import SimulationRunner

    return SimulationRunner.RUN_STATE_DIR


def create_demo_simulation(kind: str = "social", seed: int = 20) -> Dict[str, Any]:
    """Create a demo simulation; returns its descriptor."""
    if kind == "market":
        return _create_market_demo(seed)
    return _create_social_demo(seed)


# ----------------------------------------------------------------------
# market demo: real engine, real ensemble
# ----------------------------------------------------------------------

def _market_config(rng: random.Random, agents: int = 24) -> Dict[str, Any]:
    stances = (["bullish"] * 9 + ["bearish"] * 7 + ["neutral"] * 5 + ["hedging"] * 3)
    rng.shuffle(stances)
    return {
        "time_config": {"total_simulation_hours": 24, "minutes_per_round": 15},
        "agent_configs": [
            {
                "agent_id": i,
                "agent_name": FIRST_NAMES[i % len(FIRST_NAMES)] + f"_{i}",
                "stance": stances[i % len(stances)],
                "activity_level": round(rng.uniform(0.3, 0.8), 2),
            }
            for i in range(agents)
        ],
    }


def _create_market_demo(seed: int) -> Dict[str, Any]:
    from ..engines import get_engine
    from .ensemble_service import create_ensemble

    rng = random.Random(seed)
    sim_id = f"demo_market_{seed}"
    sim_dir = os.path.join(_sim_root(), sim_id)
    os.makedirs(sim_dir, exist_ok=True)

    config = _market_config(rng)
    with open(os.path.join(sim_dir, "simulation_config.json"), 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    _write_state(sim_dir, sim_id, scenario_id="market_live")

    engine = get_engine("market")
    engine.run_sync(sim_id, seed=seed)
    # a small ensemble so the outcomes view has a distribution to draw
    create_ensemble(sim_id, variants=10, base_seed=seed + 1)

    logger.info("market demo created: %s", sim_id)
    return {"simulation_id": sim_id, "kind": "market", "seed": seed,
            "agents": len(config["agent_configs"])}


# ----------------------------------------------------------------------
# social demo: scripted two-camp story
# ----------------------------------------------------------------------

def _create_social_demo(seed: int) -> Dict[str, Any]:
    rng = random.Random(seed)
    sim_id = f"demo_social_{seed}"
    sim_dir = os.path.join(_sim_root(), sim_id)
    os.makedirs(sim_dir, exist_ok=True)

    agents = []
    camps: Dict[int, str] = {}
    # 9 supportive, 8 opposing, 7 undecided observers/neutrals
    roles = (["supportive"] * 9 + ["opposing"] * 8
             + ["neutral"] * 4 + ["observer"] * 3)
    rng.shuffle(roles)
    for i, stance in enumerate(roles):
        agents.append({
            "agent_id": i,
            "agent_name": FIRST_NAMES[i % len(FIRST_NAMES)] + f"_{i}",
            "stance": stance,
            "activity_level": round(rng.uniform(0.3, 0.85), 2),
        })
        camps[i] = stance

    # a third of the opposing camp never budges (the persistent minority);
    # the rest are persuadable once the evidence lands
    opposing_ids = [i for i, s in camps.items() if s == "opposing"]
    hardcore_ids = set(opposing_ids[:max(1, len(opposing_ids) // 3)])
    # the intervention is published by a supportive voice, not an opponent
    intervention_agent = next(i for i, s in camps.items() if s == "supportive")

    total_rounds = 36
    config = {
        "time_config": {"total_simulation_hours": 36, "minutes_per_round": 60},
        "agent_configs": agents,
        "event_config": {"initial_posts": [
            {"poster_agent_id": intervention_agent, "content": "BREAKING: the city plans to pedestrianise the old town center."}
        ]},
    }
    with open(os.path.join(sim_dir, "simulation_config.json"), 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    _write_state(sim_dir, sim_id, scenario_id="social_media")

    intervention_round = 18
    intervention_text = ("City hall releases the independent traffic study: congestion "
                         "drops 22% in comparable cities after pedestrianisation.")

    for platform in ("twitter", "reddit"):
        _write_social_log(
            sim_dir, platform, agents, camps, total_rounds,
            intervention_round, intervention_text, rng,
            hardcore_ids, intervention_agent,
        )

    # interventions record so the panel shows history
    record = {
        "intervention_id": "iv_demo0001",
        "simulation_id": sim_id,
        "text": intervention_text,
        "agent_id": intervention_agent,
        "magnitude": None,
        "channels": ["twitter", "reddit"],
        "engine": "oasis",
        "queued_at": datetime.now().isoformat(),
    }
    with open(os.path.join(sim_dir, "interventions.json"), 'w', encoding='utf-8') as f:
        json.dump([record], f, ensure_ascii=False, indent=2)
    with open(os.path.join(sim_dir, "interventions_applied.jsonl"), 'w', encoding='utf-8') as f:
        for platform in ("twitter", "reddit"):
            f.write(json.dumps({
                "intervention_id": "iv_demo0001", "platform": platform,
                "round": intervention_round, "text": intervention_text,
                "agent_id": intervention_agent, "success": True,
                "applied_at": datetime.now().isoformat(),
            }, ensure_ascii=False) + "\n")

    _write_run_state(sim_dir, sim_id, total_rounds)
    logger.info("social demo created: %s", sim_id)
    return {"simulation_id": sim_id, "kind": "social", "seed": seed,
            "agents": len(agents), "intervention_round": intervention_round}


def _write_social_log(
    sim_dir: str,
    platform: str,
    agents: List[Dict[str, Any]],
    camps: Dict[int, str],
    total_rounds: int,
    intervention_round: int,
    intervention_text: str,
    rng: random.Random,
    hardcore_ids: set,
    intervention_agent: int,
) -> None:
    os.makedirs(os.path.join(sim_dir, platform), exist_ok=True)
    path = os.path.join(sim_dir, platform, "actions.jsonl")
    t0 = datetime.now() - timedelta(hours=total_rounds)
    post_seq = 0
    # camp -> list of (post_id, author_id), most recent last
    posts_by_camp: Dict[str, List[tuple]] = {"supportive": [], "opposing": [], "neutral": []}

    def stamp(round_num: int) -> str:
        return (t0 + timedelta(hours=round_num, seconds=rng.randint(0, 3000))).isoformat()

    def camp_of(agent_id: int) -> str:
        stance = camps[agent_id]
        return stance if stance in ("supportive", "opposing") else "neutral"

    with open(path, 'w', encoding='utf-8') as log:
        def event(payload):
            log.write(json.dumps(payload, ensure_ascii=False) + "\n")

        def action(round_num, agent_id, a_type, args):
            event({
                "round": round_num, "timestamp": stamp(round_num),
                "agent_id": agent_id,
                "agent_name": agents[agent_id]["agent_name"],
                "action_type": a_type, "action_args": args, "success": True,
            })

        event({"event_type": "simulation_start", "platform": platform,
               "timestamp": t0.isoformat(),
               "total_rounds": total_rounds, "agents_count": len(agents)})

        for round_num in range(1, total_rounds + 1):
            event({"event_type": "round_start", "round": round_num,
                   "timestamp": stamp(round_num),
                   "simulated_hour": round_num % 24})
            acted = 0

            if round_num == intervention_round:
                event({"event_type": "intervention", "round": round_num,
                       "timestamp": stamp(round_num),
                       "intervention_id": "iv_demo0001",
                       "text": intervention_text, "agent_id": intervention_agent})
                post_seq += 1
                action(round_num, intervention_agent, "CREATE_POST",
                       {"content": intervention_text, "post_id": post_seq,
                        "intervention": True})
                posts_by_camp["supportive"].append((post_seq, intervention_agent))
                acted += 1

            after = round_num >= intervention_round
            # After the study lands, people re-share and react far more
            # than they author fresh takes — which also matters for the
            # opinion model: posting re-anchors an agent to its prior,
            # reacting is what moves it.
            post_prob = 0.35 if not after else 0.15
            for agent in agents:
                aid = agent["agent_id"]
                if rng.random() > agent["activity_level"]:
                    continue
                camp = camp_of(aid)
                hardcore = aid in hardcore_ids
                roll = rng.random()

                if roll < (0.35 if hardcore else post_prob):  # write something
                    post_seq += 1
                    action(round_num, aid, "CREATE_POST",
                           {"content": f"{platform} take #{post_seq} on the plan",
                            "post_id": post_seq})
                    posts_by_camp[camp].append((post_seq, aid))
                    acted += 1
                else:  # react to something
                    # Act 1: react inside your echo chamber.
                    # Act 2 (post-intervention): persuasion flows through
                    # the center — the tracker's bounded-confidence window
                    # blocks direct cross-camp jumps, so undecideds drift
                    # toward the supportive camp while persuadable
                    # opponents first endorse neutral voices, then
                    # supportive ones. A hard core keeps disliking the
                    # study: the persistent minority.
                    if camp == "neutral":
                        target_camp = ("supportive" if after and rng.random() < 0.8
                                       else rng.choice(["supportive", "opposing"]))
                        verb = "LIKE_POST"
                    elif camp == "supportive":
                        cross = rng.random() < (0.25 if after else 0.10)
                        target_camp = "opposing" if cross else "supportive"
                        verb = "DISLIKE_POST" if cross else "LIKE_POST"
                    elif hardcore:
                        if rng.random() < 0.45:
                            target_camp, verb = "supportive", "DISLIKE_POST"
                        else:
                            target_camp, verb = "opposing", "LIKE_POST"
                    else:  # persuadable opposing
                        if after:
                            bridge = rng.random()
                            if bridge < 0.55:
                                target_camp, verb = "neutral", "LIKE_POST"
                            elif bridge < 0.8:
                                target_camp, verb = "supportive", "LIKE_POST"
                            else:
                                target_camp, verb = "opposing", "LIKE_POST"
                        else:
                            target_camp, verb = "opposing", "LIKE_POST"
                    pool = posts_by_camp[target_camp] or posts_by_camp[camp]
                    if not pool:
                        continue
                    post_id, _author = pool[-min(len(pool), rng.randint(1, 6))]
                    action(round_num, aid, verb, {"post_id": post_id})
                    acted += 1

            event({"event_type": "round_end", "round": round_num,
                   "timestamp": stamp(round_num), "actions_count": acted})

        event({"event_type": "simulation_end", "platform": platform,
               "timestamp": stamp(total_rounds),
               "total_rounds": total_rounds, "total_actions": post_seq})


# ----------------------------------------------------------------------
# shared state writers
# ----------------------------------------------------------------------

def _write_state(sim_dir: str, sim_id: str, scenario_id: str) -> None:
    now = datetime.now().isoformat()
    with open(os.path.join(sim_dir, "state.json"), 'w', encoding='utf-8') as f:
        json.dump({
            "simulation_id": sim_id,
            "project_id": "demo",
            "graph_id": "demo",
            "scenario_id": scenario_id,
            "status": "completed",
            "demo": True,
            "created_at": now,
            "updated_at": now,
        }, f, ensure_ascii=False, indent=2)


def _write_run_state(sim_dir: str, sim_id: str, total_rounds: int) -> None:
    now = datetime.now().isoformat()
    with open(os.path.join(sim_dir, "run_state.json"), 'w', encoding='utf-8') as f:
        json.dump({
            "simulation_id": sim_id,
            "runner_status": "completed",
            "current_round": total_rounds,
            "total_rounds": total_rounds,
            "progress_percent": 100.0,
            "started_at": now,
            "updated_at": now,
            "completed_at": now,
        }, f, ensure_ascii=False, indent=2)
