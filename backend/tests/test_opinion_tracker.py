"""
Tests for the opinion tracker (stance distributions, consensus /
polarization metrics, cluster + minority detection).

Run with::

    cd backend && python -m pytest tests/test_opinion_tracker.py -q
"""

import json
import os
import sys

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _BACKEND_DIR)

from app.services.opinion_tracker import (  # noqa: E402
    HIST_BINS,
    OpinionTracker,
    compute_opinion_timeline,
)


def _agents(*stances):
    return [
        {"agent_id": i, "agent_name": f"a{i}", "stance": s}
        for i, s in enumerate(stances)
    ]


def _action(agent_id, a_type, round_num=1, **args):
    return {
        "round": round_num,
        "timestamp": f"2026-01-01T00:00:{agent_id:02d}",
        "agent_id": agent_id,
        "action_type": a_type,
        "action_args": args,
    }


# ---------------------------------------------------------------- init

def test_initial_stances_from_vocabulary():
    tracker = OpinionTracker(_agents("supportive", "opposing", "neutral", "bullish"))
    assert tracker.agents[0].stance == pytest.approx(0.7)
    assert tracker.agents[1].stance == pytest.approx(-0.7)
    assert tracker.agents[2].stance == pytest.approx(0.0)
    assert tracker.agents[3].stance == pytest.approx(0.7)


def test_unknown_stance_label_degrades_to_neutral():
    tracker = OpinionTracker(_agents("flabbergasted"))
    assert tracker.agents[0].stance == pytest.approx(0.0)


# ---------------------------------------------------------------- dynamics

def test_expression_raises_confidence_and_reanchors():
    tracker = OpinionTracker(_agents("supportive"))
    agent = tracker.agents[0]
    agent.stance = 0.2  # drifted away from its 0.7 prior
    before_conf = agent.confidence
    tracker.replay([_action(0, "CREATE_POST", post_id=1)])
    assert agent.confidence > before_conf
    assert agent.stance > 0.2  # pulled back toward prior


def test_explicit_sentiment_overrides_stance():
    tracker = OpinionTracker(_agents("neutral"))
    tracker.replay([_action(0, "PLACE_ORDER", sentiment=-0.55)])
    assert tracker.agents[0].stance == pytest.approx(-0.55)


def test_endorsement_pulls_toward_author():
    tracker = OpinionTracker(_agents("neutral", "supportive"))
    tracker.replay([
        _action(1, "CREATE_POST", post_id=10),
        _action(0, "LIKE_POST", post_id=10),
    ])
    assert tracker.agents[0].stance > 0.05  # moved toward +0.7 author


def test_bounded_confidence_blocks_distant_persuasion():
    tracker = OpinionTracker(_agents("opposing", "supportive"))
    tracker.replay([
        _action(1, "CREATE_POST", post_id=10),
        _action(0, "LIKE_POST", post_id=10),
    ])
    # diff is 1.4 > window 0.9: no movement
    assert tracker.agents[0].stance == pytest.approx(-0.7)


def test_rejection_pushes_away():
    tracker = OpinionTracker(_agents("neutral", "supportive"))
    tracker.replay([
        _action(1, "CREATE_POST", post_id=10),
        _action(0, "DISLIKE_POST", post_id=10),
    ])
    assert tracker.agents[0].stance < 0.0  # pushed away from +0.7


def test_unresolvable_target_uses_round_mean_field():
    tracker = OpinionTracker(_agents("neutral", "supportive"))
    tracker.replay([
        _action(1, "CREATE_POST", post_id=99),
        _action(0, "LIKE_POST", post_id=12345),  # unknown post
    ])
    # falls back to the round's expressed mean (author's 0.7 stance)
    assert tracker.agents[0].stance > 0.05


def test_replay_is_deterministic():
    actions = [
        _action(1, "CREATE_POST", post_id=1),
        _action(0, "LIKE_POST", post_id=1),
        _action(2, "DISLIKE_POST", post_id=1),
    ]
    a = OpinionTracker(_agents("neutral", "supportive", "neutral")).replay(actions)
    b = OpinionTracker(_agents("neutral", "supportive", "neutral")).replay(actions)
    assert [s.to_dict() for s in a] == [s.to_dict() for s in b]


# ---------------------------------------------------------------- metrics

def test_snapshot_of_uniform_population_is_consensus():
    tracker = OpinionTracker(_agents(*(["supportive"] * 10)))
    snap = tracker.replay([_action(0, "CREATE_POST", post_id=1)])[-1]
    assert snap.polarization < 0.1
    assert snap.consensus > 0.8
    assert len(snap.clusters) == 1
    assert snap.minorities == []


def test_snapshot_of_split_population_is_polarized():
    tracker = OpinionTracker(_agents(*(["supportive"] * 5 + ["opposing"] * 5)))
    snap = tracker.replay([_action(0, "CREATE_POST", post_id=1)])[-1]
    assert snap.polarization > 0.6
    assert snap.consensus < 0.6
    assert len(snap.clusters) == 2


def test_minority_detection():
    tracker = OpinionTracker(_agents(*(["supportive"] * 9 + ["opposing"] * 1)))
    snap = tracker.replay([_action(0, "CREATE_POST", post_id=1)])[-1]
    assert len(snap.minorities) == 1
    assert snap.minorities[0]["share"] == pytest.approx(0.1)


def test_histogram_bins_sum_to_population():
    tracker = OpinionTracker(_agents("supportive", "opposing", "neutral"))
    snap = tracker.replay([_action(0, "CREATE_POST", post_id=1)])[-1]
    assert len(snap.histogram) == HIST_BINS
    assert sum(snap.histogram) == 3


def test_cluster_ids_stay_stable_across_rounds():
    tracker = OpinionTracker(_agents(*(["supportive"] * 5 + ["opposing"] * 5)))
    snaps = tracker.replay([
        _action(0, "CREATE_POST", round_num=1, post_id=1),
        _action(5, "CREATE_POST", round_num=2, post_id=2),
        _action(1, "CREATE_POST", round_num=3, post_id=3),
    ])
    by_round = [
        {c["cluster_id"]: c["centroid"] for c in s.clusters} for s in snaps[1:]
    ]
    ids = set(by_round[0])
    for round_clusters in by_round[1:]:
        assert set(round_clusters) == ids  # same two ids all the way through


# ---------------------------------------------------------------- file API

def test_compute_timeline_over_demo_run(tmp_path, monkeypatch):
    from app.services.simulation_runner import SimulationRunner
    from app.services.demo_seed import create_demo_simulation

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    descriptor = create_demo_simulation(kind="social", seed=20)
    sim_dir = os.path.join(str(tmp_path), descriptor["simulation_id"])

    timeline = compute_opinion_timeline(sim_dir)
    rounds = timeline["rounds"]
    assert len(rounds) == 37  # rounds 0..36

    iv_round = descriptor["intervention_round"]
    assert rounds[iv_round]["interventions"], "intervention marker missing"

    # the demo's story: opinion moves toward the supportive camp after the
    # intervention, while a minority persists
    assert rounds[-1]["mean"] > rounds[iv_round]["mean"]
    assert any(c["centroid"] < -0.5 for c in rounds[-1]["clusters"])

    # cache round-trips
    cached = compute_opinion_timeline(sim_dir)
    assert cached["computed_at"] == timeline["computed_at"]
    forced = compute_opinion_timeline(sim_dir, force=True)
    assert [r["mean"] for r in forced["rounds"]] == [r["mean"] for r in rounds]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
