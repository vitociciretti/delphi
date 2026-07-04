"""
Market engine: an in-process, seeded order-flow simulation.

This is the first non-OASIS substrate. Instead of re-framing a Twitter
feed as a "news feed", it models the thing the financial preset is really
about: sentiment becoming orders, orders moving price, and price feeding
back into sentiment.

Model (deliberately lean, but a real closed loop):

- Every agent carries a continuous sentiment ``s ∈ [-1, 1]`` initialised
  from its categorical stance (bullish/bearish/... from the scenario
  vocabulary) plus per-agent traits drawn from a seeded RNG:
  herding weight, momentum weight (negative = contrarian) and noise.
- Each round:  ``s_i ← (1-λ)·s_i + λ·(w_p·prior_i + w_h·crowd + w_m·mom_i·momentum + shock) + ε``
  where ``crowd`` is the mean sentiment expressed last round (mean-field
  herding) and ``momentum`` is the recent price return.
- Active agents place orders: side from the gap between their sentiment
  and the current price premium over fair value, size from conviction.
- Price moves with net order-flow imbalance (linear impact) and drift;
  news shocks (interventions) move fair value and jolt sentiment.

Everything is written in the same on-disk protocol as the OASIS scripts —
``<sim_dir>/market/actions.jsonl`` with the identical line schema plus
``run_state.json`` — so the actions feed, opinion tracker, SSE stream and
frontend work unchanged. A ``market_timeline.json`` adds per-round price /
volume / sentiment snapshots.

Runs are deterministic for a given (config, seed) pair, execute in-process
in milliseconds (no LLM, no network), and support live interventions and
cheap seeded ensembles.
"""

import json
import math
import os
import random
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger
from .base import EngineCapabilities, EngineError, EngineRunHandle, SimulationEngine

logger = get_logger('mirofish.engines.market')

# Categorical stance -> initial sentiment prior. Unknown labels fall back
# to 0.0 so custom scenario vocabularies degrade gracefully.
STANCE_SENTIMENT = {
    "bullish": 0.75, "bearish": -0.75, "hedging": -0.15, "neutral": 0.0,
    "observer": 0.0, "supportive": 0.6, "opposing": -0.6,
    "advocate": 0.6, "skeptic": -0.4, "blocker": -0.8,
    "allied": 0.6, "opposed": -0.6, "conflicted": 0.0, "detached": 0.0,
}

CHANNEL = "market"


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class _MarketAgent:
    agent_id: int
    name: str
    prior: float          # long-run sentiment anchor from stance
    sentiment: float      # current sentiment
    herding: float        # weight on crowd sentiment
    momentum: float       # weight on price momentum (negative = contrarian)
    noise: float          # idiosyncratic noise scale
    activity: float       # per-round action probability


class MarketEngine(SimulationEngine):
    """Seeded in-process order-flow / price-impact simulation."""

    engine_id = "market"

    # dynamics constants (kept as class attrs so tests can tweak)
    LAMBDA = 0.35          # sentiment update rate
    W_PRIOR = 0.45
    W_HERD = 0.35
    W_MOMENTUM = 0.20
    PRICE_IMPACT = 0.008   # price move per unit of full imbalance
    DRIFT = 0.0
    PREMIUM_SENSITIVITY = 4.0  # how strongly over/under-valuation deters orders
    COMMENT_PROB = 0.18    # chance an acting agent also posts commentary

    _threads: Dict[str, threading.Thread] = {}
    _stop_flags: Dict[str, threading.Event] = {}
    _shock_queues: Dict[str, List[Dict[str, Any]]] = {}
    _locks: Dict[str, threading.Lock] = {}
    _registry_lock = threading.Lock()

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            live_interventions=True,
            interviews=False,
            deterministic_seed=True,
            in_process=True,
            ensembles=True,
        )

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        simulation_id: str,
        *,
        seed: Optional[int] = None,
        max_rounds: Optional[int] = None,
        **options: Any,
    ) -> EngineRunHandle:
        from ..services.simulation_runner import SimulationRunner

        sim_dir = os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        if not os.path.exists(config_path):
            raise EngineError(f"simulation config not found: {config_path}")

        with self._registry_lock:
            existing = self._threads.get(simulation_id)
            if existing and existing.is_alive():
                raise EngineError(f"market simulation already running: {simulation_id}")
            stop_flag = threading.Event()
            self._stop_flags[simulation_id] = stop_flag
            self._shock_queues[simulation_id] = []
            self._locks[simulation_id] = threading.Lock()

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        seed = seed if seed is not None else int(options.get("seed", 0) or 0)
        round_delay = float(options.get("round_delay_seconds", 0.0))

        thread = threading.Thread(
            target=self._run,
            args=(simulation_id, sim_dir, config, seed, max_rounds, round_delay, stop_flag),
            daemon=True,
        )
        with self._registry_lock:
            self._threads[simulation_id] = thread
        thread.start()

        logger.info("market simulation started: %s (seed=%s)", simulation_id, seed)
        return EngineRunHandle(
            simulation_id=simulation_id,
            engine_id=self.engine_id,
            status="running",
            detail={"seed": seed},
        )

    def run_sync(
        self,
        simulation_id: str,
        *,
        seed: int = 0,
        max_rounds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run to completion on the calling thread (tests / ensembles)."""
        from ..services.simulation_runner import SimulationRunner

        sim_dir = os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        if not os.path.exists(config_path):
            raise EngineError(f"simulation config not found: {config_path}")
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        with self._registry_lock:
            self._shock_queues.setdefault(simulation_id, [])
            self._locks.setdefault(simulation_id, threading.Lock())
        self._run(simulation_id, sim_dir, config, seed, max_rounds, 0.0, threading.Event())
        return self.get_status(simulation_id)

    def stop(self, simulation_id: str) -> Dict[str, Any]:
        flag = self._stop_flags.get(simulation_id)
        if flag is None:
            raise ValueError(f"market simulation not running: {simulation_id}")
        flag.set()
        thread = self._threads.get(simulation_id)
        if thread:
            thread.join(timeout=10)
        return self.get_status(simulation_id)

    def get_status(self, simulation_id: str) -> Dict[str, Any]:
        from ..services.simulation_runner import SimulationRunner

        state_file = os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id, "run_state.json")
        if not os.path.exists(state_file):
            return {"simulation_id": simulation_id, "runner_status": "idle"}
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"simulation_id": simulation_id, "runner_status": "idle"}

    def inject_event(self, simulation_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Queue a news shock for the next round.

        ``magnitude`` in [-1, 1] moves fair value by up to ±5% and jolts
        sentiment; ``text`` is logged so feeds and reports can show it.
        """
        lock = self._locks.get(simulation_id)
        queue = self._shock_queues.get(simulation_id)
        if lock is None or queue is None:
            raise ValueError(f"market simulation not running: {simulation_id}")

        magnitude = _clamp(float(event.get("magnitude", 0.0)))
        shock = {
            "text": event.get("text", ""),
            "magnitude": magnitude,
            "queued_at": datetime.now().isoformat(),
        }
        with lock:
            queue.append(shock)
        logger.info("market shock queued for %s: magnitude=%.2f", simulation_id, magnitude)
        return {"success": True, "result": shock}

    # ------------------------------------------------------------------
    # simulation core
    # ------------------------------------------------------------------

    def _build_agents(self, config: Dict[str, Any], rng: random.Random) -> List[_MarketAgent]:
        agents = []
        for cfg in config.get("agent_configs", []):
            stance = str(cfg.get("stance", "neutral")).lower()
            prior = STANCE_SENTIMENT.get(stance, 0.0)
            activity = float(cfg.get("activity_level", cfg.get("activity", 0.4)) or 0.4)
            agents.append(_MarketAgent(
                agent_id=int(cfg.get("agent_id", len(agents))),
                name=cfg.get("agent_name", cfg.get("name", f"agent_{len(agents)}")),
                prior=prior,
                sentiment=_clamp(prior + rng.gauss(0, 0.15)),
                herding=rng.uniform(0.2, 0.9),
                momentum=rng.uniform(-0.5, 0.9),
                noise=rng.uniform(0.03, 0.12),
                activity=_clamp(activity, 0.05, 0.95),
            ))
        if not agents:
            raise EngineError("simulation config has no agent_configs")
        return agents

    def _run(
        self,
        simulation_id: str,
        sim_dir: str,
        config: Dict[str, Any],
        seed: int,
        max_rounds: Optional[int],
        round_delay: float,
        stop_flag: threading.Event,
    ) -> None:
        import time as _time

        rng = random.Random(seed)
        channel_dir = os.path.join(sim_dir, CHANNEL)
        os.makedirs(channel_dir, exist_ok=True)
        actions_path = os.path.join(channel_dir, "actions.jsonl")
        timeline_path = os.path.join(sim_dir, "market_timeline.json")

        time_config = config.get("time_config", {})
        total_hours = int(time_config.get("total_simulation_hours", 48))
        minutes_per_round = int(time_config.get("minutes_per_round", 30))
        total_rounds = max(1, (total_hours * 60) // minutes_per_round)
        if max_rounds:
            total_rounds = min(total_rounds, int(max_rounds))

        try:
            agents = self._build_agents(config, rng)
        except EngineError as exc:
            self._write_run_state(sim_dir, simulation_id, "failed", 0, total_rounds,
                                  0, total_hours, error=str(exc))
            return

        price = 100.0
        fair_value = 100.0
        prev_price = price
        crowd = sum(a.sentiment for a in agents) / len(agents)
        timeline: List[Dict[str, Any]] = []
        total_actions = 0

        self._write_run_state(sim_dir, simulation_id, "running", 0, total_rounds,
                              0, total_hours, started=True)

        with open(actions_path, 'w', encoding='utf-8') as log:
            self._log_event(log, {"event_type": "simulation_start", "engine": self.engine_id,
                                  "seed": seed, "total_rounds": total_rounds})

            for round_num in range(1, total_rounds + 1):
                if stop_flag.is_set():
                    break

                simulated_hours = (round_num * minutes_per_round) // 60
                self._log_event(log, {"event_type": "round_start", "round": round_num,
                                      "simulated_hours": simulated_hours})

                # -- apply queued news shocks -------------------------------
                shock_effect = 0.0
                lock = self._locks.get(simulation_id)
                queue = self._shock_queues.get(simulation_id)
                if lock and queue:
                    with lock:
                        pending, queue[:] = list(queue), []
                    for shock in pending:
                        shock_effect += shock["magnitude"]
                        fair_value *= (1.0 + 0.05 * shock["magnitude"])
                        self._log_event(log, {
                            "event_type": "intervention", "round": round_num,
                            "text": shock["text"], "magnitude": shock["magnitude"],
                        })

                momentum = (price - prev_price) / prev_price if prev_price else 0.0
                prev_price = price

                # -- sentiment updates & orders -----------------------------
                buys = sells = 0
                buy_vol = sell_vol = 0.0
                expressed: List[float] = []
                round_actions = 0

                for agent in agents:
                    target = (self.W_PRIOR * agent.prior
                              + self.W_HERD * agent.herding * crowd
                              + self.W_MOMENTUM * agent.momentum * _clamp(momentum * 25)
                              + 0.5 * shock_effect)
                    agent.sentiment = _clamp(
                        (1 - self.LAMBDA) * agent.sentiment
                        + self.LAMBDA * target
                        + rng.gauss(0, agent.noise)
                    )

                    if rng.random() > agent.activity:
                        continue

                    premium = (price - fair_value) / fair_value
                    signal = agent.sentiment - self.PREMIUM_SENSITIVITY * premium
                    if abs(signal) < 0.05:
                        continue  # conviction too weak to trade

                    side = "buy" if signal > 0 else "sell"
                    size = max(1, min(10, round(abs(signal) * 10)))
                    if side == "buy":
                        buys += 1
                        buy_vol += size
                    else:
                        sells += 1
                        sell_vol += size
                    expressed.append(agent.sentiment)
                    round_actions += 1
                    total_actions += 1

                    self._log_action(log, round_num, agent, "PLACE_ORDER", {
                        "side": side, "size": size,
                        "price": round(price, 2),
                        "sentiment": round(agent.sentiment, 3),
                    })

                    if rng.random() < self.COMMENT_PROB:
                        mood = ("bullish" if agent.sentiment > 0.2
                                else "bearish" if agent.sentiment < -0.2 else "uncertain")
                        self._log_action(log, round_num, agent, "CREATE_POST", {
                            "content": f"{agent.name} is {mood} at {price:.2f} "
                                       f"({'accumulating' if side == 'buy' else 'reducing'})",
                            "sentiment": round(agent.sentiment, 3),
                        })
                        round_actions += 1
                        total_actions += 1

                # -- price formation ----------------------------------------
                volume = buy_vol + sell_vol
                imbalance = (buy_vol - sell_vol) / volume if volume else 0.0
                price *= (1.0 + self.PRICE_IMPACT * imbalance * math.sqrt(max(volume, 1.0))
                          + self.DRIFT + 0.01 * shock_effect)
                price = max(price, 1.0)

                if expressed:
                    crowd = sum(expressed) / len(expressed)

                sentiments = [a.sentiment for a in agents]
                mean_s = sum(sentiments) / len(sentiments)
                std_s = math.sqrt(sum((s - mean_s) ** 2 for s in sentiments) / len(sentiments))
                timeline.append({
                    "round": round_num,
                    "price": round(price, 4),
                    "fair_value": round(fair_value, 4),
                    "volume": volume,
                    "buys": buys,
                    "sells": sells,
                    "imbalance": round(imbalance, 4),
                    "mean_sentiment": round(mean_s, 4),
                    "std_sentiment": round(std_s, 4),
                    "shock": round(shock_effect, 4),
                })

                self._log_event(log, {"event_type": "round_end", "round": round_num,
                                      "simulated_hours": simulated_hours,
                                      "actions_count": round_actions})

                if round_num % 10 == 0 or round_num == total_rounds:
                    self._write_run_state(sim_dir, simulation_id, "running", round_num,
                                          total_rounds, simulated_hours, total_hours)
                    with open(timeline_path, 'w', encoding='utf-8') as tf:
                        json.dump(timeline, tf, ensure_ascii=False)

                if round_delay > 0:
                    _time.sleep(round_delay)

            self._log_event(log, {"event_type": "simulation_end",
                                  "total_rounds": total_rounds,
                                  "total_actions": total_actions,
                                  "final_price": round(price, 4)})

        with open(timeline_path, 'w', encoding='utf-8') as tf:
            json.dump(timeline, tf, ensure_ascii=False)

        final_status = "stopped" if stop_flag.is_set() else "completed"
        self._write_run_state(sim_dir, simulation_id, final_status,
                              min(total_rounds, len(timeline)), total_rounds,
                              total_hours, total_hours, completed=True)
        logger.info("market simulation %s: %s (final price %.2f, %d actions)",
                    final_status, simulation_id, price, total_actions)

    # ------------------------------------------------------------------
    # on-disk protocol helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _log_event(log, payload: Dict[str, Any]) -> None:
        payload.setdefault("timestamp", datetime.now().isoformat())
        log.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _log_action(log, round_num: int, agent: _MarketAgent,
                    action_type: str, args: Dict[str, Any]) -> None:
        log.write(json.dumps({
            "round": round_num,
            "timestamp": datetime.now().isoformat(),
            "platform": CHANNEL,
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "action_type": action_type,
            "action_args": args,
            "success": True,
        }, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_run_state(sim_dir: str, simulation_id: str, status: str,
                         current_round: int, total_rounds: int,
                         simulated_hours: int, total_hours: int,
                         started: bool = False, completed: bool = False,
                         error: Optional[str] = None) -> None:
        state_file = os.path.join(sim_dir, "run_state.json")
        now = datetime.now().isoformat()
        state: Dict[str, Any] = {}
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
            except (json.JSONDecodeError, OSError):
                state = {}
        state.update({
            "simulation_id": simulation_id,
            "engine": MarketEngine.engine_id,
            "runner_status": status,
            "current_round": current_round,
            "total_rounds": total_rounds,
            "simulated_hours": simulated_hours,
            "total_simulation_hours": total_hours,
            "progress_percent": round(current_round / max(total_rounds, 1) * 100, 1),
            "updated_at": now,
            "error": error,
        })
        if started:
            state["started_at"] = now
        if completed:
            state["completed_at"] = now
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
