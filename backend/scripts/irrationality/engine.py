"""
IrrationalityEngine: orchestrator wired into the runner scripts.

Lifecycle (from a runner script's perspective):

    engine = IrrationalityEngine.maybe_create(config, sim_dir, llm_model)
    ...
    agent_graph = await generate_reddit_agent_graph(...)
    env = oasis.make(...); await env.reset()
    if engine:
        engine.attach(agent_graph)              # traits, env swap, prompts
        await engine.run_baseline_probes(env)   # pre-simulation bias baseline
    for round_num in ...:
        if engine:
            engine.before_round(round_num)      # affect decay, prompt context
        active = self._get_active_agents_for_round(...)  # uses engine.activity_multiplier
        actions = engine.build_actions(active, round_num) if engine \
                  else {agent: LLMAction() for _, agent in active}
        await env.step(actions)
        if engine:
            await engine.after_round(env, round_num)  # opinions, affect, probes
    if engine:
        engine.finalize()

Everything is gated by irrationality_config.enabled (default off) and
per-feature flags, so a config without the block runs the legacy behavior
byte-for-byte.
"""

import copy
import json
import os
import random
import sqlite3
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from oasis import ActionType, LLMAction, ManualAction

from .affect import AffectState, score_emotional_charge
from .dual_process import DualProcessRouter
from .opinion import (
    DEFAULT_TOPIC,
    Exposure,
    OpinionDynamics,
    TopicClassifier,
    polarization_metrics,
)
from .perception import PsychSocialEnvironment
from .probes import BiasProbeHarness
from .traits import PsychProfile

TELEMETRY_FILENAME = "psych_state.jsonl"
PROFILES_FILENAME = "psych_profiles.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "intensity": 1.0,
    "features": {
        "trait_prompts": True,
        "affect": True,
        "opinion_dynamics": True,
        "biased_perception": True,
        "dual_process": True,
        "choice_noise": True,
        "bias_probes": True,
    },
    "opinion": {
        "model": "deffuant",
        "epsilon": 0.4,
        "mu": 0.3,
        "self_weight": 0.7,
        "affect_coupling": 0.5,
        # [{"id": "...", "label": "...", "keywords": [...]}]
        "topics": [],
        "exposures_per_agent": 8,
    },
    "affect": {
        "decay": 0.15,
        "boredom_rate": 0.05,
        "fatigue_recovery": 0.02,
        "activation_coupling": 0.6,
    },
    "dual_process": {
        "base_s1_prob": 0.3,
        "arousal_weight": 0.5,
        "impulsivity_weight": 0.3,
        "cognition_weight": 0.4,
        "s1_temperature": 1.2,
    },
    "choice_noise": {
        "epsilon_scale": 0.15,
    },
    "probes": {
        "sample_size": 8,
        "every_n_rounds": 10,
        "probe_types": ["anchoring", "conformity", "framing", "rumor_chain"],
    },
}


class IrrationalityEngine:
    """Per-simulation orchestrator for all irrationality features."""

    def __init__(self, config: Dict[str, Any], simulation_dir: str, llm_model: str):
        self.simulation_dir = simulation_dir
        self.llm_model = llm_model
        self.full_config = config
        self.cfg = _deep_merge(DEFAULT_CONFIG, config.get("irrationality_config") or {})
        self.intensity = max(0.0, float(self.cfg.get("intensity", 1.0)))
        self.features: Dict[str, bool] = dict(self.cfg["features"])

        sim_id = str(config.get("simulation_id", "delphi"))
        self.rng = random.Random(_stable_seed(sim_id))

        # Populated by attach().
        self.agent_graph = None
        self.profiles: Dict[int, PsychProfile] = {}
        self.affect: Dict[int, AffectState] = {}
        self.opinion: Optional[OpinionDynamics] = None
        self.topic_classifier: Optional[TopicClassifier] = None
        self.topic_labels: Dict[str, str] = {}
        self.router: Optional[DualProcessRouter] = None
        self.probes: Optional[BiasProbeHarness] = None

        # Recent-content window feeding exposures / choice noise.
        self._content_window: deque = deque(maxlen=40)
        self._last_post_id = 0
        self._last_comment_id = 0
        self._last_active_ids: List[int] = []
        self._round_events: Dict[str, int] = {"s1": 0, "s2": 0, "noise": 0}

        self.db_path = os.path.join(simulation_dir, "reddit_simulation.db")
        self.telemetry_path = os.path.join(simulation_dir, TELEMETRY_FILENAME)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def maybe_create(
        cls,
        config: Dict[str, Any],
        simulation_dir: str,
        llm_model: str,
        db_filename: str = "reddit_simulation.db",
    ) -> Optional["IrrationalityEngine"]:
        irr = config.get("irrationality_config") or {}
        if not irr.get("enabled"):
            return None
        engine = cls(config, simulation_dir, llm_model)
        engine.db_path = os.path.join(simulation_dir, db_filename)
        print("[irrationality] engine enabled "
              f"(intensity={engine.intensity}, features="
              f"{[k for k, v in engine.features.items() if v]})")
        return engine

    # ------------------------------------------------------------------
    # Attachment: traits, opinion init, env swap, prompt augmentation
    # ------------------------------------------------------------------

    def attach(self, agent_graph) -> None:
        self.agent_graph = agent_graph
        agent_configs = {int(c.get("agent_id", i)): c
                         for i, c in enumerate(self.full_config.get("agent_configs", []))}

        # Topics: configured, else derived from event hot_topics, else default.
        topics_cfg = list(self.cfg["opinion"].get("topics") or [])
        if not topics_cfg:
            hot = (self.full_config.get("event_config") or {}).get("hot_topics") or []
            topics_cfg = [{"id": DEFAULT_TOPIC,
                           "label": str(hot[0]) if hot else "the main event",
                           "keywords": [str(h) for h in hot[:5]]}]
        self.topic_classifier = TopicClassifier(topics_cfg)
        self.topic_labels = {
            str(t.get("id", "")): str(t.get("label") or t.get("id") or "")
            for t in topics_cfg if t.get("id")
        }

        opinion_cfg = self.cfg["opinion"]
        self.opinion = OpinionDynamics(
            model=str(opinion_cfg.get("model", "deffuant")),
            epsilon=opinion_cfg.get("epsilon", 0.4),
            mu=opinion_cfg.get("mu", 0.3),
            self_weight=opinion_cfg.get("self_weight", 0.7),
            affect_coupling=opinion_cfg.get("affect_coupling", 0.5),
            topics=self.topic_classifier.topic_ids,
        )

        for agent_id, agent in agent_graph.get_agents():
            cfg = agent_configs.get(agent_id, {"agent_id": agent_id})
            profile = PsychProfile.from_agent_config(cfg)
            self.profiles[agent_id] = profile
            self.affect[agent_id] = AffectState.for_profile(
                sentiment_bias=profile.sentiment_bias,
                impulsivity=profile.impulsivity,
            )
            self.opinion.init_agent(agent_id, {
                t: profile.initial_opinion(t)
                for t in self.topic_classifier.topic_ids
            })

            # Swap in the biased-perception environment (keeps the same
            # SocialAction, so channel/DB wiring is untouched).
            if self.features.get("biased_perception") or self.features.get("dual_process") \
                    or self.features.get("affect") or self.features.get("opinion_dynamics"):
                agent.env = PsychSocialEnvironment(
                    action=agent.env.action,
                    agent_id=agent_id,
                    psych=profile,
                    intensity=self.intensity,
                    biased_perception=bool(self.features.get("biased_perception")),
                    opinion_lookup=self._main_topic_opinion,
                )

            if self.features.get("trait_prompts"):
                section = profile.render_prompt_section(self.intensity)
                if section:
                    self._augment_system_prompt(agent, section)

        if self.features.get("dual_process"):
            dp = self.cfg["dual_process"]
            self.router = DualProcessRouter(
                llm_model=self.llm_model,
                base_s1_prob=dp.get("base_s1_prob", 0.3),
                arousal_weight=dp.get("arousal_weight", 0.5),
                impulsivity_weight=dp.get("impulsivity_weight", 0.3),
                cognition_weight=dp.get("cognition_weight", 0.4),
                s1_temperature=dp.get("s1_temperature", 1.2),
                intensity=self.intensity,
                rng=self.rng,
            )

        if self.features.get("bias_probes"):
            probes_cfg = self.cfg["probes"]
            subject = str(self.full_config.get("simulation_requirement", ""))[:200] \
                or self.topic_labels.get(self.topic_classifier.topic_ids[0], "the event")
            self.probes = BiasProbeHarness(
                simulation_dir=self.simulation_dir,
                db_path=self.db_path,
                subject=subject,
                sample_size=probes_cfg.get("sample_size", 8),
                every_n_rounds=probes_cfg.get("every_n_rounds", 10),
                probe_types=probes_cfg.get("probe_types"),
                rng=self.rng,
            )

        self._save_profiles()
        print(f"[irrationality] attached to {len(self.profiles)} agents "
              f"(opinion model: {self.opinion.model}, "
              f"topics: {self.topic_classifier.topic_ids})")

    # ------------------------------------------------------------------
    # Round hooks
    # ------------------------------------------------------------------

    def activity_multiplier(self, agent_id: int) -> float:
        """Multiplier for the activation gate in _get_active_agents_for_round."""
        if not self.features.get("affect"):
            return 1.0
        state = self.affect.get(agent_id)
        if state is None:
            return 1.0
        coupling = self.cfg["affect"].get("activation_coupling", 0.6) * self.intensity
        return state.activation_multiplier(coupling)

    def before_round(self, round_num: int) -> None:
        """Affect decay + refresh every agent's dynamic prompt context."""
        affect_cfg = self.cfg["affect"]
        for agent_id, state in self.affect.items():
            if self.features.get("affect"):
                state.decay(
                    decay_rate=affect_cfg.get("decay", 0.15),
                    fatigue_recovery=affect_cfg.get("fatigue_recovery", 0.02),
                )
            self._refresh_dynamic_context(agent_id)
        self._round_events = {"s1": 0, "s2": 0, "noise": 0}

    def build_actions(self, active_agents: List[Tuple[int, Any]], round_num: int) -> Dict[Any, Any]:
        """Produce the actions dict for env.step: LLMAction by default,
        System-1-flavored LLMAction, or an impulsive manual action."""
        actions: Dict[Any, Any] = {}
        self._last_active_ids = [aid for aid, _ in active_agents]

        for agent_id, agent in active_agents:
            profile = self.profiles.get(agent_id)
            state = self.affect.get(agent_id)
            if profile is None or state is None:
                actions[agent] = LLMAction()
                continue

            # Choice noise: quantal-response-style impulsive override.
            if self.features.get("choice_noise"):
                epsilon = (self.cfg["choice_noise"].get("epsilon_scale", 0.15)
                           * profile.impulsivity * self.intensity)
                if self.rng.random() < epsilon:
                    noise_action = self._impulsive_action(agent_id, profile)
                    if noise_action is not None:
                        actions[agent] = noise_action
                        self._round_events["noise"] += 1
                        continue

            # Dual-process routing (also resets s1_mode to False on S2).
            if self.router is not None:
                mode = self.router.route(profile, state)
                self.router.apply(agent, mode)
                self._round_events[mode] += 1

            actions[agent] = LLMAction()
        return actions

    async def after_round(self, env, round_num: int) -> None:
        """Ingest new content, run the opinion equation, update affect,
        run scheduled probe waves, append telemetry."""
        new_items = self._fetch_new_content()
        for item in new_items:
            self._content_window.append(item)

        exposures: List[Exposure] = []
        window = list(self._content_window)
        per_agent = int(self.cfg["opinion"].get("exposures_per_agent", 8))

        for listener_id in self._last_active_ids:
            if not window:
                break
            seen = self.rng.sample(window, k=min(per_agent, len(window)))
            if self.features.get("affect"):
                self.affect[listener_id].absorb_exposure(
                    ((item["charge"], item["valence"]) for item in seen),
                    negativity_bias=self.profiles[listener_id].negativity_bias,
                    boredom_rate=self.cfg["affect"].get("boredom_rate", 0.05),
                )
            for item in seen:
                exposures.append(Exposure(
                    listener_id=listener_id,
                    author_id=item["author_id"],
                    topic=item["topic"],
                    weight=0.5 + 0.5 * item["charge"],
                ))

        opinion_updates = 0
        if self.features.get("opinion_dynamics") and exposures:
            opinion_updates = self.opinion.step(
                exposures,
                credulity={a: p.credulity for a, p in self.profiles.items()},
                conformity={a: p.conformity for a, p in self.profiles.items()},
                arousal={a: s.arousal for a, s in self.affect.items()},
            )

        if self.probes is not None and self.probes.should_run(round_num):
            await self.probes.run_wave(env, self.agent_graph, f"round_{round_num}")

        self._append_telemetry(round_num, len(new_items), opinion_updates)

    async def run_baseline_probes(self, env) -> None:
        """Pre-simulation probe wave: measures the LLM's intrinsic bias
        profile before any dynamics have acted."""
        if self.probes is not None:
            await self.probes.run_wave(env, self.agent_graph, "baseline")

    def finalize(self) -> None:
        if self.probes is not None:
            summary = self.probes.finalize()
            print(f"[irrationality] probe summary: {summary}")
        print("[irrationality] telemetry written to "
              f"{self.telemetry_path}")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _main_topic_opinion(self, user_id: int) -> Optional[float]:
        if self.opinion is None:
            return None
        ops = self.opinion.opinions.get(user_id)
        if not ops:
            return None
        return ops.get(self.topic_classifier.topic_ids[0])

    def _refresh_dynamic_context(self, agent_id: int) -> None:
        agent = self._get_agent(agent_id)
        if agent is None:
            return
        env = getattr(agent, "env", None)
        if not isinstance(env, PsychSocialEnvironment):
            return
        parts = []
        if self.features.get("affect"):
            parts.append(self.affect[agent_id].render_prompt_section())
        if self.features.get("opinion_dynamics") and self.opinion is not None:
            opinion_text = self.opinion.render_prompt_section(agent_id, self.topic_labels)
            if opinion_text:
                parts.append(opinion_text)
        env.dynamic_context = (
            "# YOUR CURRENT STATE\n" + "\n".join(parts) if parts else ""
        )

    def _impulsive_action(self, agent_id: int, profile: PsychProfile) -> Optional[ManualAction]:
        """Pick the most emotionally charged recent post and react to it on
        pure alignment: agree -> like, strongly disagree -> dislike."""
        posts = [item for item in self._content_window
                 if item["kind"] == "post" and item["author_id"] != agent_id]
        if not posts:
            return None
        target = max(posts, key=lambda item: item["charge"])
        my_opinion = self._main_topic_opinion(agent_id)
        author_opinion = self._main_topic_opinion(target["author_id"])
        if (my_opinion is not None and author_opinion is not None
                and abs(my_opinion - author_opinion) > 0.9):
            action_type = ActionType.DISLIKE_POST
        else:
            action_type = ActionType.LIKE_POST
        return ManualAction(
            action_type=action_type,
            action_args={"post_id": target["item_id"]},
        )

    def _fetch_new_content(self) -> List[Dict[str, Any]]:
        """Incrementally read new posts + comments from the OASIS SQLite DB
        and score them (charge, valence, topic)."""
        items: List[Dict[str, Any]] = []
        if not os.path.exists(self.db_path):
            return items
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT post_id, user_id, content, num_likes, num_dislikes "
                    "FROM post WHERE post_id > ? ORDER BY post_id",
                    (self._last_post_id,),
                )
                rows = cursor.fetchall()
            except sqlite3.OperationalError:
                cursor.execute(
                    "SELECT post_id, user_id, content, 0, 0 "
                    "FROM post WHERE post_id > ? ORDER BY post_id",
                    (self._last_post_id,),
                )
                rows = cursor.fetchall()
            for post_id, user_id, content, likes, dislikes in rows:
                self._last_post_id = max(self._last_post_id, int(post_id))
                items.append(self._make_item("post", post_id, user_id, content,
                                             likes or 0, dislikes or 0))
            try:
                cursor.execute(
                    "SELECT comment_id, user_id, content FROM comment "
                    "WHERE comment_id > ? ORDER BY comment_id",
                    (self._last_comment_id,),
                )
                for comment_id, user_id, content in cursor.fetchall():
                    self._last_comment_id = max(self._last_comment_id, int(comment_id))
                    items.append(self._make_item("comment", comment_id, user_id,
                                                 content, 0, 0))
            except sqlite3.OperationalError:
                pass
            conn.close()
        except Exception as e:
            print(f"[irrationality] content fetch failed: {e}")
        return [item for item in items if item["author_id"] in self.profiles]

    def _make_item(self, kind: str, item_id, user_id, content,
                   likes: int, dislikes: int) -> Dict[str, Any]:
        charge, valence = score_emotional_charge(
            content or "", num_likes=int(likes), num_dislikes=int(dislikes))
        return {
            "kind": kind,
            "item_id": int(item_id),
            "author_id": int(user_id) if user_id is not None else -1,
            "topic": self.topic_classifier.classify(content or ""),
            "charge": charge,
            "valence": valence,
        }

    def _augment_system_prompt(self, agent, section: str) -> None:
        """Insert the trait section into the agent's system prompt.

        Placed BEFORE any '# RESPONSE FORMAT' block because
        perform_interview truncates the system prompt at that marker --
        traits must survive into interviews (probes depend on it).
        Re-seeds memory via init_messages(); safe because it runs before
        the simulation produces any memory.
        """
        try:
            sys_msg = agent._system_message
            content = sys_msg.content if sys_msg is not None else ""
            if section in content:
                return
            if "# RESPONSE FORMAT" in content:
                content = content.replace(
                    "# RESPONSE FORMAT", section + "\n\n# RESPONSE FORMAT", 1)
            else:
                content = content + "\n\n" + section
            new_msg = sys_msg.create_new_instance(content)
            agent._original_system_message = new_msg
            agent._system_message = new_msg
            agent.init_messages()
        except Exception as e:
            print(f"[irrationality] system-prompt augmentation failed for "
                  f"agent {getattr(agent, 'social_agent_id', '?')}: {e}")

    def _get_agent(self, agent_id: int):
        try:
            return self.agent_graph.get_agent(agent_id)
        except Exception:
            return None

    def _append_telemetry(self, round_num: int, new_items: int, opinion_updates: int) -> None:
        arousals = [s.arousal for s in self.affect.values()]
        fatigues = [s.fatigue for s in self.affect.values()]
        valences = [s.valence for s in self.affect.values()]
        record = {
            "round": round_num,
            "active_agents": len(self._last_active_ids),
            "new_content_items": new_items,
            "opinion_updates": opinion_updates,
            "events": dict(self._round_events),
            "mean_arousal": round(_mean(arousals), 4),
            "mean_valence": round(_mean(valences), 4),
            "mean_fatigue": round(_mean(fatigues), 4),
            "opinion": {},
            "agents": {
                str(aid): {
                    **self.affect[aid].to_dict(),
                    "opinions": {
                        t: round(v, 4)
                        for t, v in (self.opinion.opinions.get(aid) or {}).items()
                    } if self.opinion else {},
                }
                for aid in self.affect
            },
        }
        if self.opinion is not None:
            for topic in self.topic_classifier.topic_ids:
                values = [ops[topic] for ops in self.opinion.opinions.values()
                          if topic in ops]
                record["opinion"][topic] = polarization_metrics(values)
        try:
            with open(self.telemetry_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[irrationality] telemetry write failed: {e}")

    def _save_profiles(self) -> None:
        try:
            path = os.path.join(self.simulation_dir, PROFILES_FILENAME)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {str(aid): p.to_dict() for aid, p in self.profiles.items()},
                    f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[irrationality] profile save failed: {e}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _stable_seed(text: str) -> int:
    # hash() is salted per-process; use a stable arithmetic hash instead so
    # the same simulation_id always reproduces the same stochastic run.
    seed = 0
    for ch in text:
        seed = (seed * 131 + ord(ch)) % (2 ** 31)
    return seed


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0
