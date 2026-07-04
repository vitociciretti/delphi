"""
Tests for ensemble outcome distributions and the intervention queue.

Run with::

    cd backend && python -m pytest tests/test_ensemble_and_interventions.py -q
"""

import json
import os
import sys

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _BACKEND_DIR)
_SCRIPTS_DIR = os.path.join(_BACKEND_DIR, "scripts")
sys.path.insert(0, _SCRIPTS_DIR)

from app.services.ensemble_service import (  # noqa: E402
    create_ensemble,
    get_ensemble_outcomes,
)
from app.services.intervention_service import (  # noqa: E402
    list_interventions,
    queue_intervention,
)


def _market_config(agents=16):
    stances = ["bullish"] * 6 + ["bearish"] * 5 + ["neutral"] * 3 + ["hedging"] * 2
    return {
        "time_config": {"total_simulation_hours": 6, "minutes_per_round": 15},
        "agent_configs": [
            {"agent_id": i, "agent_name": f"trader_{i}",
             "stance": stances[i % len(stances)], "activity_level": 0.6}
            for i in range(agents)
        ],
    }


@pytest.fixture
def sim_root(tmp_path, monkeypatch):
    from app.services.simulation_runner import SimulationRunner

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    return tmp_path


def _prepare(sim_root, sim_id, scenario_id):
    d = sim_root / sim_id
    d.mkdir()
    (d / "simulation_config.json").write_text(
        json.dumps(_market_config()), encoding="utf-8"
    )
    (d / "state.json").write_text(json.dumps({
        "simulation_id": sim_id, "scenario_id": scenario_id,
        "enable_twitter": True, "enable_reddit": True,
    }), encoding="utf-8")
    return d


# ---------------------------------------------------------------- ensembles

def test_ensemble_runs_variants_and_aggregates(sim_root):
    _prepare(sim_root, "sim_e1", "market_live")
    descriptor = create_ensemble("sim_e1", variants=6, base_seed=10)
    assert descriptor["variants"] == 6
    assert all(m["status"] == "completed" for m in descriptor["members"])

    outcomes = get_ensemble_outcomes("sim_e1")
    assert outcomes["members_count"] == 6
    dist = outcomes["distribution"]
    assert 0.0 <= dist["consensus_probability"] <= 1.0
    assert sum(dist["final_mean_histogram"]) == 6
    assert dist["price_range"]["min"] <= dist["price_range"]["max"]
    seeds = {o.get("seed") for o in outcomes["outcomes"]}
    assert seeds == set(range(10, 16))


def test_ensemble_rejects_bad_variant_counts(sim_root):
    _prepare(sim_root, "sim_e2", "market_live")
    with pytest.raises(ValueError):
        create_ensemble("sim_e2", variants=1)
    with pytest.raises(ValueError):
        create_ensemble("sim_e2", variants=100)


def test_ensemble_refuses_llm_engines(sim_root):
    _prepare(sim_root, "sim_e3", "social_media")
    with pytest.raises(ValueError, match="cannot auto-run"):
        create_ensemble("sim_e3", variants=4)


def test_outcomes_without_members_raises(sim_root):
    _prepare(sim_root, "sim_e4", "market_live")
    with pytest.raises(ValueError, match="no ensemble members"):
        get_ensemble_outcomes("sim_e4")


# ------------------------------------------------------------ interventions

def test_queue_intervention_writes_per_channel_files(sim_root):
    _prepare(sim_root, "sim_i1", "social_media")
    record = queue_intervention("sim_i1", "a big announcement", agent_id=3)
    assert record["channels"] == ["twitter", "reddit"]

    for channel in ("twitter", "reddit"):
        pending = sim_root / "sim_i1" / "interventions_pending" / channel
        files = list(pending.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["text"] == "a big announcement"
        assert data["agent_id"] == 3

    listed = list_interventions("sim_i1")
    assert len(listed) == 1
    assert listed[0]["status"] == "pending"


def test_queue_intervention_rejects_empty_text(sim_root):
    _prepare(sim_root, "sim_i2", "social_media")
    with pytest.raises(ValueError):
        queue_intervention("sim_i2", "   ")


def test_script_side_drain_and_journal(sim_root):
    """The scripts' queue protocol round-trips with the backend's."""
    import interventions as script_iv  # from backend/scripts

    _prepare(sim_root, "sim_i3", "social_media")
    record = queue_intervention("sim_i3", "what if it rains", channels=["twitter"])
    sim_dir = str(sim_root / "sim_i3")

    drained = script_iv.drain_pending_interventions(sim_dir, "twitter")
    assert len(drained) == 1
    assert drained[0]["intervention_id"] == record["intervention_id"]
    # queue is now empty
    assert script_iv.drain_pending_interventions(sim_dir, "twitter") == []

    script_iv.record_applied(sim_dir, "twitter", 5, drained[0], success=True)
    listed = list_interventions("sim_i3")
    assert listed[0]["status"] == "applied"
    assert listed[0]["applications"][0]["round"] == 5


def test_intervention_marker_line_shape(sim_root, tmp_path):
    import interventions as script_iv

    log_path = tmp_path / "actions.jsonl"
    script_iv.log_intervention_marker(str(log_path), 7, {
        "intervention_id": "iv_x", "text": "hello", "agent_id": 1,
    })
    line = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert line["event_type"] == "intervention"
    assert line["round"] == 7
    assert line["text"] == "hello"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
