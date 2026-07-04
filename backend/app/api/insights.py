"""
Insights API: opinion distributions, interventions, ensembles, live stream.

This blueprint serves the outcomes dashboard:

- opinion timeline (per-round stance distributions, consensus/polarization
  metrics, clusters and minority trajectories)
- interventions (queue a what-if event into a running sim, list history)
- ensembles (run N seeded variants on capable engines, aggregate the
  distribution of final outcomes)
- market timeline (price/volume/sentiment for market-engine runs)
- an SSE stream that pushes status + fresh opinion snapshots while a
  simulation is running
- demo simulations so all of the above can be explored with no external
  services configured
"""

import json
import os
import time

from flask import Response, jsonify, request

from . import insights_bp
from ..engines import EngineError, list_engines
from ..services.opinion_tracker import compute_opinion_timeline
from ..utils.logger import get_logger

logger = get_logger('mirofish.api.insights')


def _sim_dir(simulation_id: str) -> str:
    from ..services.simulation_runner import SimulationRunner

    return os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id)


def _fail(message: str, code: int = 400):
    return jsonify({"success": False, "error": message}), code


# ============== engines ==============

@insights_bp.route('/engines', methods=['GET'])
def get_engines():
    """List registered simulation engines and their capabilities."""
    return jsonify({"success": True, "data": list_engines()})


# ============== opinion distributions ==============

@insights_bp.route('/simulation/<simulation_id>/opinion/timeline', methods=['GET'])
def opinion_timeline(simulation_id: str):
    """Per-round opinion snapshots for a simulation."""
    force = request.args.get('force', '0') in ('1', 'true')
    try:
        timeline = compute_opinion_timeline(_sim_dir(simulation_id), force=force)
    except ValueError as exc:
        return _fail(str(exc), 404)
    return jsonify({"success": True, "data": timeline})


# ============== interventions ==============

@insights_bp.route('/simulation/<simulation_id>/intervene', methods=['POST'])
def intervene(simulation_id: str):
    """Queue a what-if event; it lands at the next simulation round."""
    from ..services.intervention_service import queue_intervention

    payload = request.get_json(silent=True) or {}
    try:
        record = queue_intervention(
            simulation_id=simulation_id,
            text=payload.get('text', ''),
            agent_id=payload.get('agent_id'),
            magnitude=payload.get('magnitude'),
            channels=payload.get('channels'),
        )
    except ValueError as exc:
        return _fail(str(exc), 400)
    return jsonify({"success": True, "data": record})


@insights_bp.route('/simulation/<simulation_id>/interventions', methods=['GET'])
def interventions(simulation_id: str):
    from ..services.intervention_service import list_interventions

    return jsonify({"success": True, "data": list_interventions(simulation_id)})


# ============== ensembles / outcome distributions ==============

@insights_bp.route('/simulation/<simulation_id>/ensemble', methods=['POST'])
def create_ensemble_route(simulation_id: str):
    """Run N seeded variants (in-process engines only) and tag them."""
    from ..services.ensemble_service import create_ensemble

    payload = request.get_json(silent=True) or {}
    try:
        descriptor = create_ensemble(
            simulation_id=simulation_id,
            variants=int(payload.get('variants', 8)),
            base_seed=int(payload.get('base_seed', 1)),
            max_rounds=payload.get('max_rounds'),
        )
    except ValueError as exc:
        return _fail(str(exc), 400)
    return jsonify({"success": True, "data": descriptor})


@insights_bp.route('/simulation/<simulation_id>/ensemble/outcomes', methods=['GET'])
def ensemble_outcomes(simulation_id: str):
    from ..services.ensemble_service import get_ensemble_outcomes

    try:
        outcomes = get_ensemble_outcomes(simulation_id)
    except ValueError as exc:
        return _fail(str(exc), 404)
    return jsonify({"success": True, "data": outcomes})


# ============== market engine ==============

@insights_bp.route('/simulation/<simulation_id>/market/timeline', methods=['GET'])
def market_timeline(simulation_id: str):
    path = os.path.join(_sim_dir(simulation_id), "market_timeline.json")
    if not os.path.exists(path):
        return _fail("no market timeline for this simulation", 404)
    with open(path, 'r', encoding='utf-8') as f:
        return jsonify({"success": True, "data": json.load(f)})


@insights_bp.route('/simulation/<simulation_id>/market/start', methods=['POST'])
def market_start(simulation_id: str):
    """Start a market-engine run (optionally slowed down for live viewing)."""
    from ..engines import get_engine

    payload = request.get_json(silent=True) or {}
    try:
        handle = get_engine("market").start(
            simulation_id,
            seed=payload.get('seed'),
            max_rounds=payload.get('max_rounds'),
            round_delay_seconds=float(payload.get('round_delay_seconds', 0.0)),
        )
    except (EngineError, ValueError) as exc:
        return _fail(str(exc), 400)
    return jsonify({"success": True, "data": handle.to_dict()})


# ============== live stream (SSE) ==============

@insights_bp.route('/simulation/<simulation_id>/stream', methods=['GET'])
def stream(simulation_id: str):
    """
    Server-sent events: run status + fresh opinion snapshots.

    Emits one ``status`` event per tick and an ``opinion`` event whenever
    new rounds appear in the timeline; ends with ``done`` once the run
    reaches a terminal state (or after ~10 min as a safety stop).
    """
    sim_dir = _sim_dir(simulation_id)
    if not os.path.isdir(sim_dir):
        return _fail(f"simulation not found: {simulation_id}", 404)

    poll_seconds = max(0.5, min(10.0, float(request.args.get('poll', '1.5'))))
    max_ticks = int(600 / poll_seconds)

    def generate():
        last_rounds = -1
        for _ in range(max_ticks):
            state_path = os.path.join(sim_dir, "run_state.json")
            status = {}
            if os.path.exists(state_path):
                try:
                    with open(state_path, 'r', encoding='utf-8') as f:
                        status = json.load(f)
                except (json.JSONDecodeError, OSError):
                    status = {}
            status.pop("recent_actions", None)
            yield f"event: status\ndata: {json.dumps(status, ensure_ascii=False)}\n\n"

            try:
                timeline = compute_opinion_timeline(sim_dir)
                rounds = timeline.get("rounds", [])
                if len(rounds) != last_rounds and rounds:
                    payload = {
                        "rounds_count": len(rounds),
                        "latest": rounds[-1],
                        "new_rounds": rounds[max(0, last_rounds):] if last_rounds >= 0 else rounds,
                    }
                    yield f"event: opinion\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last_rounds = len(rounds)
            except ValueError:
                pass  # no config yet

            runner_status = status.get("runner_status")
            if runner_status in ("completed", "stopped", "failed"):
                yield f"event: done\ndata: {json.dumps({'runner_status': runner_status})}\n\n"
                return
            time.sleep(poll_seconds)
        yield "event: done\ndata: {\"runner_status\": \"timeout\"}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


# ============== demo ==============

@insights_bp.route('/demo', methods=['POST'])
def create_demo():
    """Create a synthetic demo simulation (kind: social | market)."""
    from ..services.demo_seed import create_demo_simulation

    payload = request.get_json(silent=True) or {}
    kind = payload.get('kind', 'social')
    if kind not in ('social', 'market'):
        return _fail("kind must be 'social' or 'market'", 400)
    try:
        descriptor = create_demo_simulation(kind=kind, seed=int(payload.get('seed', 20)))
    except ValueError as exc:
        return _fail(str(exc), 400)
    return jsonify({"success": True, "data": descriptor})
