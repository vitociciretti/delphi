"""
Intervention service (backend side).

Lets a user inject a "what if X happens now?" event into a running
simulation and keeps the record of what was queued and what actually
landed. The queue protocol matches ``backend/scripts/interventions.py``:

- ``<sim_dir>/interventions_pending/<channel>/<id>.json`` — queued, one
  file per (intervention, channel); the run loop drains these at the next
  round start and injects the text as a real post.
- ``<sim_dir>/interventions_applied.jsonl`` — journal appended by the run
  loop when an intervention lands (or fails).
- ``<sim_dir>/interventions.json`` — registry of everything queued, which
  this service owns.

For in-process engines (market) the event is additionally delivered
directly through ``engine.inject_event`` as a numeric shock.
"""

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger('mirofish.interventions')

PENDING_DIR = "interventions_pending"
APPLIED_LOG = "interventions_applied.jsonl"
REGISTRY_FILE = "interventions.json"


def _sim_dir(simulation_id: str) -> str:
    from .simulation_runner import SimulationRunner

    return os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id)


def _resolve_engine_and_channels(simulation_id: str):
    """Engine + active channel names for a simulation, from its state."""
    from ..engines import engine_for_scenario, get_engine
    from ..scenarios import get_registry

    sim_dir = _sim_dir(simulation_id)
    state_path = os.path.join(sim_dir, "state.json")
    scenario_id = "social_media"
    enable_twitter = enable_reddit = True
    if os.path.exists(state_path):
        try:
            with open(state_path, 'r', encoding='utf-8') as f:
                state = json.load(f)
            scenario_id = state.get("scenario_id", scenario_id)
            enable_twitter = state.get("enable_twitter", True)
            enable_reddit = state.get("enable_reddit", True)
        except (json.JSONDecodeError, OSError):
            pass

    scenario = get_registry().get_or_default(scenario_id)
    try:
        engine = engine_for_scenario(scenario)
    except Exception:  # unknown/custom engine id in a user preset
        engine = get_engine("oasis")

    if engine.engine_id == "market":
        channels = ["market"]
    else:
        channels = []
        if enable_twitter:
            channels.append("twitter")
        if enable_reddit:
            channels.append("reddit")
        if not channels:
            channels = ["twitter"]
    return engine, channels


def queue_intervention(
    simulation_id: str,
    text: str,
    agent_id: Optional[int] = None,
    magnitude: Optional[float] = None,
    channels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Queue an intervention for the next simulation round.

    Args:
        simulation_id: target simulation
        text: the event content (a post / news item agents will see)
        agent_id: agent who "publishes" the event (default 0)
        magnitude: numeric shock in [-1, 1] for market-type engines
        channels: restrict to specific channels (default: all enabled)
    """
    if not text or not text.strip():
        raise ValueError("intervention text is required")

    sim_dir = _sim_dir(simulation_id)
    if not os.path.isdir(sim_dir):
        raise ValueError(f"simulation not found: {simulation_id}")

    engine, default_channels = _resolve_engine_and_channels(simulation_id)
    targets = channels or default_channels
    intervention_id = f"iv_{uuid.uuid4().hex[:10]}"

    record = {
        "intervention_id": intervention_id,
        "simulation_id": simulation_id,
        "text": text.strip(),
        "agent_id": int(agent_id) if agent_id is not None else 0,
        "magnitude": magnitude,
        "channels": targets,
        "engine": engine.engine_id,
        "queued_at": datetime.now().isoformat(),
    }

    if engine.capabilities.in_process:
        # delivered directly; the engine writes its own log marker
        engine.inject_event(simulation_id, {
            "text": record["text"],
            "magnitude": magnitude or 0.0,
        })
    else:
        for channel in targets:
            pending_dir = os.path.join(sim_dir, PENDING_DIR, channel)
            os.makedirs(pending_dir, exist_ok=True)
            path = os.path.join(pending_dir, f"{intervention_id}.json")
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(record, f, ensure_ascii=False, indent=2)

    _append_registry(sim_dir, record)
    logger.info("intervention queued: %s -> %s %s", intervention_id, simulation_id, targets)
    return record


def _append_registry(sim_dir: str, record: Dict[str, Any]) -> None:
    registry_path = os.path.join(sim_dir, REGISTRY_FILE)
    registry: List[Dict[str, Any]] = []
    if os.path.exists(registry_path):
        try:
            with open(registry_path, 'r', encoding='utf-8') as f:
                registry = json.load(f)
        except (json.JSONDecodeError, OSError):
            registry = []
    registry.append(record)
    with open(registry_path, 'w', encoding='utf-8') as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)


def list_interventions(simulation_id: str) -> List[Dict[str, Any]]:
    """Queued interventions merged with their application journal."""
    sim_dir = _sim_dir(simulation_id)
    registry_path = os.path.join(sim_dir, REGISTRY_FILE)
    registry: List[Dict[str, Any]] = []
    if os.path.exists(registry_path):
        try:
            with open(registry_path, 'r', encoding='utf-8') as f:
                registry = json.load(f)
        except (json.JSONDecodeError, OSError):
            registry = []

    applied: Dict[str, List[Dict[str, Any]]] = {}
    applied_path = os.path.join(sim_dir, APPLIED_LOG)
    if os.path.exists(applied_path):
        with open(applied_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                applied.setdefault(entry.get("intervention_id", ""), []).append(entry)

    for record in registry:
        events = applied.get(record.get("intervention_id", ""), [])
        # in-process engines deliver instantly and mark their own log
        instant = record.get("engine") == "market"
        record["applications"] = events
        record["status"] = (
            "applied" if instant or any(e.get("success") for e in events)
            else "failed" if events
            else "pending"
        )
    return registry
