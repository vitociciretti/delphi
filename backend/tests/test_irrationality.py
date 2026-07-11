"""
Unit tests for the irrationality modeling package (scripts/irrationality).

The package depends only on oasis/camel (installed in the backend venv) --
no Flask/Zep/OpenAI stubbing needed. Model-backend construction is skipped
via __new__ where a real LLM client would otherwise be created.

Run: cd backend && .venv/bin/python -m pytest tests/test_irrationality.py -q
"""

import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(BACKEND, "scripts"))
sys.path.insert(0, BACKEND)

import random

from irrationality.traits import PsychProfile, BIAS_LIBRARY
from irrationality.affect import AffectState, score_emotional_charge
from irrationality.opinion import (
    Exposure,
    OpinionDynamics,
    TopicClassifier,
    polarization_metrics,
)
from irrationality.probes import _parse
from irrationality.dual_process import DualProcessRouter
from irrationality.engine import IrrationalityEngine, _deep_merge, _stable_seed


# ---------------------------------------------------------------------------
# traits
# ---------------------------------------------------------------------------

def test_psych_profile_from_explicit_block():
    profile = PsychProfile.from_agent_config({
        "agent_id": 7,
        "stance": "opposing",
        "sentiment_bias": -0.6,
        "psych": {
            "biases": ["confirmation_bias", "not_a_real_bias"],
            "credulity": 0.9,
            "conformity": 0.2,
            "impulsivity": 1.7,  # out of range -> clipped
        },
    })
    assert profile.biases == ["confirmation_bias"]  # unknown ids filtered
    assert profile.credulity == 0.9
    assert profile.impulsivity == 1.0  # clipped to [0, 1]
    assert profile.stance == "opposing"
    assert profile.sentiment_bias == -0.6


def test_psych_profile_fallback_is_deterministic():
    cfg = {"agent_id": 3, "entity_type": "student"}
    a = PsychProfile.from_agent_config(cfg)
    b = PsychProfile.from_agent_config(cfg)
    assert a.to_dict() == b.to_dict()
    assert 2 <= len(a.biases) <= 4
    assert all(bias in BIAS_LIBRARY for bias in a.biases)


def test_initial_opinion_follows_stance():
    supportive = PsychProfile.from_agent_config(
        {"agent_id": 1, "entity_type": "student", "stance": "supportive"})
    opposing = PsychProfile.from_agent_config(
        {"agent_id": 2, "entity_type": "student", "stance": "opposing"})
    assert supportive.initial_opinion("main_event") > 0.4
    assert opposing.initial_opinion("main_event") < -0.4
    # explicit opinions win over stance defaults
    explicit = PsychProfile.from_agent_config({
        "agent_id": 3, "stance": "supportive",
        "psych": {"opinions": {"main_event": -0.9}},
    })
    assert explicit.initial_opinion("main_event") == -0.9


def test_trait_prompt_rendering():
    profile = PsychProfile.from_agent_config({
        "agent_id": 1,
        "sentiment_bias": -0.8,
        "psych": {"biases": ["confirmation_bias"], "credulity": 0.9,
                  "conformity": 0.5, "impulsivity": 0.9,
                  "negativity_bias": 0.5, "need_for_cognition": 0.5},
    })
    section = profile.render_prompt_section(1.0)
    assert "# PSYCHOLOGICAL PROFILE" in section
    assert BIAS_LIBRARY["confirmation_bias"] in section
    assert "irritable and pessimistic" in section
    # intensity 0 disables the section entirely
    assert profile.render_prompt_section(0.0) == ""


# ---------------------------------------------------------------------------
# affect
# ---------------------------------------------------------------------------

def test_affect_decays_toward_baseline():
    state = AffectState.for_profile(sentiment_bias=0.0, impulsivity=0.5)
    state.arousal = 0.9
    for _ in range(60):
        state.decay()
    assert abs(state.arousal - state.baseline_arousal) < 0.02


def test_affect_absorbs_charged_content_and_fatigues():
    state = AffectState.for_profile(sentiment_bias=0.0, impulsivity=0.5)
    start = state.arousal
    for _ in range(10):
        state.absorb_exposure([(0.9, -0.8)] * 5, negativity_bias=0.8)
    assert state.arousal > start
    assert state.fatigue > 0.2  # repeated exposure builds immunity
    assert state.valence < 0    # mood follows negative content
    # multiplier bounded
    assert 0.2 <= state.activation_multiplier() <= 2.0


def test_fatigue_attenuates_arousal_gain():
    fresh = AffectState.for_profile(0.0, 0.5)
    tired = AffectState.for_profile(0.0, 0.5)
    tired.fatigue = 0.9
    fresh.absorb_exposure([(0.9, 0.0)])
    tired.absorb_exposure([(0.9, 0.0)])
    assert fresh.arousal > tired.arousal


def test_emotional_charge_scorer():
    hot, hot_val = score_emotional_charge(
        "This is OUTRAGEOUS!! A total scandal and cover-up!!!",
        num_likes=5, num_dislikes=20)
    calm, _ = score_emotional_charge(
        "The committee will meet on Tuesday to review the schedule.")
    assert hot > calm
    assert hot_val < 0
    assert score_emotional_charge("") == (0.0, 0.0)


# ---------------------------------------------------------------------------
# opinion dynamics
# ---------------------------------------------------------------------------

def _population(dynamics, values):
    for agent_id, value in enumerate(values):
        dynamics.init_agent(agent_id, {"main_event": value})


def test_deffuant_respects_confidence_bound():
    dynamics = OpinionDynamics(model="deffuant", epsilon=0.3, mu=0.5)
    _population(dynamics, [0.9, -0.9])
    # distance 1.8 >> effective epsilon -> no update even for credulous agents
    updates = dynamics.step([Exposure(0, 1, "main_event")],
                            credulity={0: 1.0}, arousal={0: 0.0})
    assert updates == 0
    assert dynamics.opinions[0]["main_event"] == 0.9


def test_deffuant_converges_within_bound():
    dynamics = OpinionDynamics(model="deffuant", epsilon=1.0, mu=0.3)
    _population(dynamics, [0.4, 0.0])
    before_gap = abs(dynamics.opinions[0]["main_event"]
                     - dynamics.opinions[1]["main_event"])
    for _ in range(30):
        dynamics.step([Exposure(0, 1, "main_event"), Exposure(1, 0, "main_event")],
                      credulity={0: 0.5, 1: 0.5}, conformity={0: 0.5, 1: 0.5})
    after_gap = abs(dynamics.opinions[0]["main_event"]
                    - dynamics.opinions[1]["main_event"])
    assert after_gap < before_gap
    assert after_gap < 0.05


def test_arousal_narrows_confidence_bound():
    calm = OpinionDynamics(model="deffuant", epsilon=0.5, mu=0.3, affect_coupling=1.0)
    hot = OpinionDynamics(model="deffuant", epsilon=0.5, mu=0.3, affect_coupling=1.0)
    for dyn in (calm, hot):
        _population(dyn, [0.0, 0.45])
    calm.step([Exposure(0, 1, "main_event")], arousal={0: 0.0})
    hot.step([Exposure(0, 1, "main_event")], arousal={0: 1.0})
    calm_moved = abs(calm.opinions[0]["main_event"]) > 1e-9
    hot_moved = abs(hot.opinions[0]["main_event"]) > 1e-9
    assert calm_moved and not hot_moved  # agitation -> stops listening


def test_degroot_moves_toward_neighbor_mean():
    dynamics = OpinionDynamics(model="degroot", self_weight=0.7)
    _population(dynamics, [0.0, 0.8, 0.8])
    dynamics.step([Exposure(0, 1, "main_event"), Exposure(0, 2, "main_event")])
    assert 0.1 < dynamics.opinions[0]["main_event"] < 0.8


def test_hk_averages_in_bound_neighbors_only():
    dynamics = OpinionDynamics(model="hk", epsilon=0.5)
    _population(dynamics, [0.0, 0.3, -0.95])
    # neighbor 1 in bound, neighbor 2 far out of bound (given default couplings)
    dynamics.step([Exposure(0, 1, "main_event"), Exposure(0, 2, "main_event")],
                  credulity={0: 0.5}, arousal={0: 0.0})
    value = dynamics.opinions[0]["main_event"]
    assert 0.0 < value < 0.3  # pulled up by 1, not down by 2


def test_polarization_metrics_detects_bimodality():
    rng = random.Random(42)
    polarized = ([rng.gauss(0.8, 0.05) for _ in range(30)]
                 + [rng.gauss(-0.8, 0.05) for _ in range(30)])
    consensus = [rng.gauss(0.0, 0.1) for _ in range(60)]
    assert polarization_metrics(polarized)["bimodality"] > 0.555
    assert polarization_metrics(consensus)["bimodality"] < 0.555


def test_topic_classifier():
    classifier = TopicClassifier([
        {"id": "tuition", "keywords": ["tuition", "fees"]},
        {"id": "housing", "keywords": ["dorm", "housing"]},
    ])
    assert classifier.classify("The tuition fees are insane") == "tuition"
    assert classifier.classify("New dorm rules announced") == "housing"
    assert classifier.classify("unrelated content") == "tuition"  # fallback: first
    assert TopicClassifier(None).topic_ids == ["main_event"]


# ---------------------------------------------------------------------------
# probes
# ---------------------------------------------------------------------------

def test_probe_parsers():
    assert _parse("number", "I'd say 72, because most people agree.") == 72.0
    assert _parse("number", "no idea") is None
    assert _parse("number", "about 250 percent") is None  # out of 0-100
    assert _parse("choice", "B. The gamble is worth it.") == "B"
    assert _parse("choice", "Neither appeals to me") is None
    assert _parse("raw", "story text") == "story text"
    assert _parse("number", None) is None


# ---------------------------------------------------------------------------
# dual process
# ---------------------------------------------------------------------------

def test_s1_probability_shape():
    router = DualProcessRouter.__new__(DualProcessRouter)  # skip ModelFactory
    router.base_s1_prob = 0.3
    router.arousal_weight = 0.5
    router.impulsivity_weight = 0.3
    router.cognition_weight = 0.4
    router.intensity = 1.0

    calm_thinker = PsychProfile.from_agent_config({
        "agent_id": 1,
        "psych": {"impulsivity": 0.1, "need_for_cognition": 0.9,
                  "credulity": 0.5, "conformity": 0.5, "negativity_bias": 0.5,
                  "biases": ["anchoring"]},
    })
    hothead = PsychProfile.from_agent_config({
        "agent_id": 2,
        "psych": {"impulsivity": 0.9, "need_for_cognition": 0.1,
                  "credulity": 0.5, "conformity": 0.5, "negativity_bias": 0.5,
                  "biases": ["emotional_reasoning"]},
    })
    calm_state = AffectState(arousal=0.1)
    agitated_state = AffectState(arousal=0.9)

    low = router.s1_probability(calm_thinker, calm_state)
    high = router.s1_probability(hothead, agitated_state)
    assert 0.0 <= low < high <= 0.95


# ---------------------------------------------------------------------------
# engine (config gating + helpers; no LLM/DB required)
# ---------------------------------------------------------------------------

def test_maybe_create_disabled_by_default(tmp_path):
    assert IrrationalityEngine.maybe_create({}, str(tmp_path), "gpt-4o-mini") is None
    assert IrrationalityEngine.maybe_create(
        {"irrationality_config": {"enabled": False}}, str(tmp_path), "m") is None


def test_engine_config_merge_and_seed(tmp_path):
    engine = IrrationalityEngine(
        {"simulation_id": "sim_abc",
         "irrationality_config": {"enabled": True, "intensity": 0.5,
                                  "opinion": {"model": "hk"}}},
        str(tmp_path), "gpt-4o-mini")
    # overrides merged over defaults
    assert engine.intensity == 0.5
    assert engine.cfg["opinion"]["model"] == "hk"
    assert engine.cfg["opinion"]["epsilon"] == 0.4  # default preserved
    assert engine.features["affect"] is True
    # same simulation_id -> same seed -> reproducible runs
    assert _stable_seed("sim_abc") == _stable_seed("sim_abc")
    assert _stable_seed("sim_abc") != _stable_seed("sim_abd")


def test_deep_merge_nested():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    merged = _deep_merge(base, {"a": {"b": 9}, "e": 4})
    assert merged == {"a": {"b": 9, "c": 2}, "d": 3, "e": 4}
    assert base["a"]["b"] == 1  # base untouched


def test_engine_content_scoring_and_impulse(tmp_path):
    engine = IrrationalityEngine(
        {"simulation_id": "sim_x",
         "irrationality_config": {"enabled": True},
         "agent_configs": [
             {"agent_id": 0, "entity_type": "student", "stance": "supportive"},
             {"agent_id": 1, "entity_type": "student", "stance": "opposing"},
         ]},
        str(tmp_path), "gpt-4o-mini")
    # minimal manual attach (no agent graph): profiles + opinion only
    from irrationality.opinion import OpinionDynamics as OD, TopicClassifier as TC
    engine.topic_classifier = TC(None)
    engine.opinion = OD(topics=["main_event"])
    for cfg in engine.full_config["agent_configs"]:
        profile = PsychProfile.from_agent_config(cfg)
        engine.profiles[profile.agent_id] = profile
        engine.opinion.init_agent(
            profile.agent_id, {"main_event": profile.initial_opinion("main_event")})

    item = engine._make_item("post", 5, 1, "SCANDAL!! this is disgusting!!", 0, 9)
    assert item["charge"] > 0.3 and item["topic"] == "main_event"
    engine._content_window.append(item)

    # opposing author vs supportive actor -> dislike
    action = engine._impulsive_action(0, engine.profiles[0])
    assert action is not None
    assert action.action_args == {"post_id": 5}
    from oasis import ActionType
    assert action.action_type == ActionType.DISLIKE_POST


if __name__ == "__main__":
    # standalone run (matching the repo's existing test style)
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                if "tmp_path" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                    import tempfile
                    with tempfile.TemporaryDirectory() as tmp:
                        fn(tmp)
                else:
                    fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)
