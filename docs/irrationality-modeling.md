# WS-6 — Irrationality Modeling

**As of 2026-07-11.** Opt-in layer that adds explicit, tunable models of
non-rational human behavior on top of the LLM-driven OASIS agents.
Implemented on the **Reddit runner** first; Twitter/parallel port pending.

## Why

Delphi previously had no model of irrationality at all: agent behavior was
"LLM role-plays a persona at default temperature". Any bias/emotion effects
were emergent artifacts of the model — unparameterized, unmeasured, and
model-dependent. For a public-opinion simulator, the phenomena of interest
(outrage cascades, polarization, rumor spread) are *driven* by non-rational
dynamics, so under-modeling them systematically under-predicts exactly what
the tool exists to predict. This layer makes the implicit explicit:
numeric, sweepable, and measured.

## Architecture

Everything lives in **`backend/scripts/irrationality/`** (importable by all
three runner scripts) and is orchestrated by `IrrationalityEngine` with
three hooks in the round loop:

```
engine = IrrationalityEngine.maybe_create(config, sim_dir, llm_model)  # None if disabled
engine.attach(agent_graph)          # after env.reset(): traits, env swap, prompt augment
await engine.run_baseline_probes(env)
loop:
    engine.before_round(n)          # affect decay + refresh dynamic prompt context
    actions = engine.build_actions(active_agents, n)   # S1/S2 routing + choice noise
    await env.step(actions)
    await engine.after_round(env, n)  # opinion equations, affect updates, probes, telemetry
engine.finalize()                   # probe effect sizes
```

| Module | Mechanism |
|---|---|
| `traits.py` | Per-agent trait vector (2–4 biases from a 12-entry operational library + credulity, conformity, negativity_bias, impulsivity, need_for_cognition). Rendered into the system prompt **before** `# RESPONSE FORMAT` (so interviews see it). Legacy `sentiment_bias`/`stance` config fields are now live. Deterministic per-agent fallback when no `psych` block exists. |
| `affect.py` | Arousal/valence/fatigue state per agent. Updated from feed exposure (language-agnostic charge scorer: punctuation/emoji/caps + engagement + small EN/ZH lexicon), decays to a trait baseline. Fatigue = boredom/immunity so cascades can die. Couples to activation probability. |
| `opinion.py` | Numeric opinion per topic in [-1,1], governed by Deffuant / Hegselmann-Krause / DeGroot. Credulity widens the confidence bound, conformity scales convergence, **arousal narrows the bound** (agitation → polarization). Verbalized into prompts each round. Exposures come from new posts/comments in the OASIS SQLite DB, topics from a keyword classifier. |
| `perception.py` | `PsychSocialEnvironment` (per-agent `SocialEnvironment` subclass, no OASIS fork): confirmation-bias/negativity re-ranking + selective dropping of the refreshed feed; injects the dynamic emotion+opinion context; System-1 mode truncates the feed and appends a gut-reaction instruction. |
| `dual_process.py` | Per-activation System-1/System-2 routing: P(S1) = f(arousal, impulsivity, need_for_cognition). S1 swaps in a shared temperature-1.2 model backend + S1 prompt mode; restored on S2. |
| `probes.py` | Bias-probe harness via INTERVIEW actions (excluded from autonomous action space, not written to memory): anchoring, conformity (social-proof vs control), framing (gain/loss), rumor-chain retells. Baseline wave pre-simulation, then every N rounds. Effect sizes in `bias_probe_results.json`. |
| `engine.py` | Orchestration, config merge, seeded RNG (stable per simulation_id → reproducible runs), ε-impulsivity choice noise (forced like/dislike on the most charged recent post by opinion alignment), telemetry. |

## Config

`simulation_config.json → irrationality_config` (defaults in
`engine.py:DEFAULT_CONFIG`; master switch off):

```json
{
  "enabled": true,
  "intensity": 1.0,
  "features": {"trait_prompts": true, "affect": true, "opinion_dynamics": true,
                "biased_perception": true, "dual_process": true,
                "choice_noise": true, "bias_probes": true},
  "opinion": {"model": "deffuant", "epsilon": 0.4, "mu": 0.3,
               "affect_coupling": 0.5, "topics": []},
  "affect": {"decay": 0.15, "boredom_rate": 0.05, "activation_coupling": 0.6},
  "dual_process": {"base_s1_prob": 0.3, "s1_temperature": 1.2},
  "choice_noise": {"epsilon_scale": 0.15},
  "probes": {"sample_size": 8, "every_n_rounds": 10}
}
```

## Plumbing

- **API**: `POST /api/simulation/create` and `POST /api/simulation/prepare`
  accept a `psychology` object (stored on `SimulationState.psychology_settings`,
  becomes `irrationality_config`). `GET /api/simulation/<id>/psychology`
  returns trait vectors, per-round aggregates, and probe results.
- **Config generation**: when enabled, the agent-config LLM batch prompt also
  generates per-agent `psych` blocks (`simulation_config_generator.py`).
- **Frontend**: "Agent Psychology" collapsible panel in Step 1
  (`Step1GraphBuild.vue`) — master toggle, intensity slider, opinion-model
  select, per-feature checkboxes.
- **Report**: when telemetry exists, a deterministic "Irrationality & Bias
  Metrics" section is appended to the report outline (`report_agent.py`) —
  data computed in Python, narrative by LLM (no ReAct loop).

## Artifacts (simulation dir)

- `psych_profiles.json` — trait vector per agent
- `psych_state.jsonl` — per-round: mean arousal/valence/fatigue, per-topic
  polarization (std + bimodality coefficient), S1/S2 counts, impulsive actions,
  per-agent states
- `bias_probe_results.json` — probe waves + effect-size summary

## Testing

- `backend/tests/test_irrationality.py` — 21 unit tests (dynamics properties,
  couplings, parsers, gating). All pass; existing suite unaffected (31/31).
- Integration-verified against a real OASIS agent graph (prompt augmentation
  position, env swap, backend swap mechanics, telemetry writes).

## Known limitations / next steps

1. **Exposure approximation**: opinion/affect exposures use a recent-content
   window sampled per active agent, not each agent's actual recsys feed.
2. **Twitter/parallel runners** not yet wired (module is shared-ready;
   `maybe_create(db_filename=...)` parameterizes the DB name).
3. **Charge scorer is a crude proxy** (by design — deterministic, testable);
   a model-based scorer is a possible upgrade.
4. **Validation against a real event** (cascade-shape comparison) has not
   been run yet — that's the gate before trusting tuned parameters.
5. Rumor-chain probe responses are stored raw; distortion scoring happens
   only qualitatively in the report narrative.
