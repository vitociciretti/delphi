# Delphi — the outcome oracle

**Delphi** is SimulatedWorld's prediction layer. It answers the question a
single simulation run cannot: **what is the distribution of outcomes?** Where
does consensus form, what stays controversial, which minority views grow or
radicalise, and what changes if you intervene mid-run.

Open it at **`/delphi`** in the frontend (also linked from the home nav;
`/outcomes` still resolves as an alias). Two demo buttons generate complete
synthetic runs — no LLM or Zep keys needed.

## 1. Engine layer (`backend/app/engines/`)

Scenario presets have always carried an `engine` field; it is now real.

| Engine | Substrate | Seeded | In-process | Interventions | Ensembles |
|---|---|---|---|---|---|
| `oasis` | LLM-driven Twitter/Reddit feeds (subprocess) | no | no | yes (file queue) | aggregate-only |
| `market` | order-flow → price-impact → sentiment loop | **yes** | **yes** | yes (news shocks) | **auto-run** |

- `SimulationEngine` (`base.py`) is the contract; `registry.py` resolves the
  preset's `engine` value. Custom engines register with `register_engine()`.
- The **market engine** is a closed loop: agent sentiment (seeded traits:
  herding, momentum/contrarian, noise) → orders → price impact → sentiment.
  It writes the same `actions.jsonl` protocol as the OASIS scripts plus a
  `market_timeline.json` (price / volume / imbalance / sentiment per round),
  so every downstream consumer works unchanged.
- The `market_live` preset selects it; `GET /api/insights/engines` lists
  engines and capabilities.

## 2. Opinion tracker (`backend/app/services/opinion_tracker.py`)

Replays any run's action log through a deterministic opinion-dynamics model:

- continuous stance `s ∈ [-1, 1]` per agent, initialised from its categorical
  stance label (works for every scenario vocabulary);
- posts re-anchor an agent to its prior; likes/upvotes pull it toward the
  endorsed author within a **bounded-confidence window**; dislikes push away;
  endorsing distant content lowers confidence (openness), keeping persuadable
  agents mobile;
- per-round snapshots: histogram, mean/std, **polarization** (pairwise-
  distance index), **consensus** (largest-cluster share × tightness),
  1-D **camp clustering** with stable ids across rounds, and **minority**
  camps (<25 % share).

`GET /api/insights/simulation/<id>/opinion/timeline` returns the whole
timeline (cached by file mtime; `?force=1` recomputes).

## 3. Interventions (what-ifs)

Queue an event mid-run; it lands as a *real post* at the next round, so
agents react to it like any other content:

```
POST /api/insights/simulation/<id>/intervene
{ "text": "City hall releases the traffic study…",
  "agent_id": 3,            # optional: who publishes it
  "magnitude": 0.5,         # market engine: news shock in [-1, 1]
  "channels": ["twitter"] } # optional: default = all enabled
```

- OASIS runs: files under `interventions_pending/<channel>/` are drained by
  the round loop (`backend/scripts/interventions.py`) and injected as
  `ManualAction CREATE_POST`; an `event_type: "intervention"` marker line is
  logged so timelines annotate the moment.
- Market runs: delivered in-process as a fair-value shock.
- `GET …/interventions` merges the queue registry with the applied journal.

## 4. Ensembles — the distribution of outcomes

```
POST /api/insights/simulation/<id>/ensemble   { "variants": 12, "base_seed": 7 }
GET  /api/insights/simulation/<id>/ensemble/outcomes
```

Runs N seeded variants (in-process engines only — LLM engines must be run
individually and are then aggregated by the same endpoint) and reduces the
final snapshots: histogram of final mean stances, **consensus probability**,
mean polarization, cross-run **divergence** (how controversial the outcome
itself is) and, for markets, the final price range.

## 5. Live stream

`GET /api/insights/simulation/<id>/stream` — server-sent events with
`status`, `opinion` (fresh rounds) and `done`. The dashboard attaches
automatically and appends rounds as they happen; start a slow-motion live
run with `POST …/market/start {"round_delay_seconds": 0.5}`.

## 6. Demos

`POST /api/insights/demo {"kind": "social" | "market", "seed": 20}`

- **social**: a scripted two-camp story (echo chambers → evidence lands at
  round 18 → the undecided convert while a hard core radicalises). The
  dynamics *emerge* from replaying ordinary posts/likes/dislikes.
- **market**: a real market-engine run plus a 10-member ensemble.

## 7. Dashboard (`frontend/src/views/Delphi.vue`)

KPI tiles with sparklines · opinion-flow streamgraph with intervention
markers · per-round distribution histogram with scrubber + play animation ·
camp-centroid trajectories with minorities highlighted · consensus-vs-
polarization lines · market price/volume panels · ensemble outcome
distribution · an intervention console · a table view of every metric.

The chart palette was validated for colour-vision-deficiency separation and
contrast (diverging red↔blue around a neutral grey for stance; the brand
orange is reserved for interventions/minorities emphasis).

## Tests

```
cd backend && python -m pytest tests/ -q
```

`test_engines.py`, `test_opinion_tracker.py`,
`test_ensemble_and_interventions.py` — all deterministic, no external
services.
