"""
Tests for the engine abstraction layer and the market engine.

These run without any external services (no LLM, Zep or OASIS). Run with::

    cd backend && python -m pytest tests/test_engines.py -q
"""

import json
import os
import sys

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _BACKEND_DIR)

from app.engines import (  # noqa: E402
    EngineError,
    engine_for_scenario,
    get_engine,
    list_engines,
)
from app.scenarios import get_registry  # noqa: E402


def _market_config(agents=16, hours=6, minutes_per_round=15):
    stances = (["bullish"] * 6 + ["bearish"] * 5 + ["neutral"] * 3 + ["hedging"] * 2)
    return {
        "time_config": {
            "total_simulation_hours": hours,
            "minutes_per_round": minutes_per_round,
        },
        "agent_configs": [
            {"agent_id": i, "agent_name": f"trader_{i}",
             "stance": stances[i % len(stances)], "activity_level": 0.6}
            for i in range(agents)
        ],
    }


@pytest.fixture
def sim_dir(tmp_path, monkeypatch):
    """A prepared simulation dir under a temp RUN_STATE_DIR."""
    from app.services.simulation_runner import SimulationRunner

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    sim_id = "sim_test_market"
    d = tmp_path / sim_id
    d.mkdir()
    (d / "simulation_config.json").write_text(
        json.dumps(_market_config()), encoding="utf-8"
    )
    return sim_id, str(d)


# ---------------------------------------------------------------- registry

def test_builtin_engines_registered():
    ids = [e["engine_id"] for e in list_engines()]
    assert "oasis" in ids
    assert "market" in ids


def test_get_unknown_engine_raises():
    with pytest.raises(EngineError):
        get_engine("does_not_exist")


def test_engine_resolution_from_scenarios():
    reg = get_registry()
    assert engine_for_scenario(reg.get("social_media")).engine_id == "oasis"
    assert engine_for_scenario(reg.get("market_live")).engine_id == "market"


def test_market_live_preset_is_valid():
    preset = get_registry().get("market_live")
    assert preset.validate() == []
    assert preset.channels[0].engine == "market"


def test_capabilities_shape():
    market = get_engine("market")
    assert market.capabilities.deterministic_seed
    assert market.capabilities.in_process
    assert market.capabilities.ensembles
    oasis = get_engine("oasis")
    assert not oasis.capabilities.in_process
    assert oasis.capabilities.live_interventions


# ---------------------------------------------------------------- market run

def test_market_run_completes_and_writes_protocol(sim_dir):
    sim_id, d = sim_dir
    engine = get_engine("market")
    status = engine.run_sync(sim_id, seed=1)

    assert status["runner_status"] == "completed"
    assert status["current_round"] == status["total_rounds"] == 24  # 6h / 15min

    timeline = json.loads(open(os.path.join(d, "market_timeline.json")).read())
    assert len(timeline) == 24
    assert all(r["price"] > 0 for r in timeline)

    log_path = os.path.join(d, "market", "actions.jsonl")
    lines = [json.loads(l) for l in open(log_path, encoding="utf-8")]
    kinds = {l.get("event_type") or l.get("action_type") for l in lines}
    assert {"simulation_start", "round_start", "round_end",
            "simulation_end", "PLACE_ORDER"} <= kinds
    # action lines follow the shared schema
    action = next(l for l in lines if l.get("action_type") == "PLACE_ORDER")
    assert {"round", "timestamp", "agent_id", "agent_name",
            "action_type", "action_args"} <= set(action)


def test_market_run_is_deterministic_per_seed(sim_dir):
    sim_id, d = sim_dir
    engine = get_engine("market")

    engine.run_sync(sim_id, seed=42)
    prices_a = [r["price"] for r in json.loads(
        open(os.path.join(d, "market_timeline.json")).read())]

    engine.run_sync(sim_id, seed=42)
    prices_b = [r["price"] for r in json.loads(
        open(os.path.join(d, "market_timeline.json")).read())]

    engine.run_sync(sim_id, seed=43)
    prices_c = [r["price"] for r in json.loads(
        open(os.path.join(d, "market_timeline.json")).read())]

    assert prices_a == prices_b
    assert prices_a != prices_c


def test_market_shock_moves_fair_value_and_logs_marker(sim_dir):
    sim_id, d = sim_dir
    engine = get_engine("market")

    # pre-register the shock queue (normally done by start()/run_sync)
    import threading
    engine._shock_queues[sim_id] = []
    engine._locks[sim_id] = threading.Lock()
    engine.inject_event(sim_id, {"text": "surprise rate cut", "magnitude": 0.8})

    engine.run_sync(sim_id, seed=7)

    timeline = json.loads(open(os.path.join(d, "market_timeline.json")).read())
    assert timeline[0]["shock"] == pytest.approx(0.8)
    assert timeline[0]["fair_value"] == pytest.approx(104.0)  # 100 * (1 + .05*.8)

    lines = [json.loads(l) for l in
             open(os.path.join(d, "market", "actions.jsonl"), encoding="utf-8")]
    markers = [l for l in lines if l.get("event_type") == "intervention"]
    assert len(markers) == 1
    assert markers[0]["text"] == "surprise rate cut"


def test_market_requires_agent_configs(tmp_path, monkeypatch):
    from app.services.simulation_runner import SimulationRunner

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    d = tmp_path / "sim_empty"
    d.mkdir()
    (d / "simulation_config.json").write_text(
        json.dumps({"time_config": {}, "agent_configs": []}), encoding="utf-8"
    )
    engine = get_engine("market")
    status = engine.run_sync("sim_empty", seed=1)
    assert status["runner_status"] == "failed"
    assert "agent_configs" in (status.get("error") or "")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
