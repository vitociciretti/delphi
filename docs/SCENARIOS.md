# Scenario / Domain Presets

SimulatedWorld can adapt to different **scopes** — social media, financial
markets, organisational decisions, creative "what-if" worlds, or anything you
define — without touching engine code. This is done through **scenario
presets**: config-driven JSON descriptions of *what kind of world* a simulation
runs in.

## What a preset controls

A scenario preset externalises the assumptions that used to be hard-coded into
the config generator:

| Field | Meaning |
|-------|---------|
| `id` | Unique preset identifier (used in the API / `SCENARIO_DEFAULT`). |
| `name` / `description` | Human-facing label and blurb (shown in the UI). |
| `domain` | High-level grouping: `social_media`, `market`, `organization`, `narrative`, `custom`. |
| `activity_rhythm` | When agents are active over 24h (peak / work / morning / off-peak hours and their multipliers) plus a `timezone` label. Replaces the old fixed China-timezone rhythm. |
| `default_total_hours` / `default_minutes_per_round` | Default simulation horizon and time granularity. |
| `channels` | The communication channels agents use. Each maps onto an `engine_platform` the underlying engine can run (`twitter` or `reddit` for OASIS) and carries recommender weights, a `viral_threshold` and `echo_chamber_strength`. |
| `stances` / `default_stance` | Domain-appropriate stance vocabulary (e.g. `bullish/bearish` for markets, `advocate/skeptic` for organisations). |
| `prompt_framing` | Natural-language framing injected into the LLM prompts so generated events, personas and behaviours fit the domain. |
| `tags` | Free-form labels for filtering / display. |

> **Engine note:** the underlying simulation still runs on OASIS social
> platforms, so every channel maps onto `twitter` or `reddit`. A preset changes
> the *framing, rhythm and dynamics* of a run. The `engine` field on each
> channel leaves room for alternative simulation backends to be plugged in
> later.

## Built-in presets

| id | Domain | Highlights |
|----|--------|-----------|
| `social_media` | social_media | **Default.** Reproduces the original engine behaviour (China-timezone rhythm, 72h, Twitter + Reddit). |
| `global_social_media` | social_media | Flattened 24/7 rhythm for worldwide, cross-timezone events. |
| `financial_market` | market | Market-hours rhythm, 48h/30-min rounds, fast news feed + investor forum, bullish/bearish stances. |
| `organization` | organization | Business-hours rhythm, 5-day horizon, announcements + team discussion, advocate/skeptic/blocker stances. |
| `creative_narrative` | narrative | No diurnal cycle, week-long story-time horizon, in-character framing for alternate endings. |

## Selecting a scenario

Per simulation, pass `scenario_id` when creating it:

```http
POST /api/simulation/create
{
  "project_id": "proj_xxxx",
  "scenario_id": "financial_market"
}
```

If `scenario_id` is omitted, `Config.SCENARIO_DEFAULT` (env `SCENARIO_DEFAULT`,
default `social_media`) is used. Which platforms run (Twitter / Reddit) is
inferred from the preset's channels unless you explicitly pass
`enable_twitter` / `enable_reddit`.

List everything available:

```http
GET /api/simulation/scenarios
```

## Adding your own domain (no code required)

1. Point `SCENARIO_PRESETS_DIR` at a directory (default:
   `backend/uploads/scenarios`).
2. Drop a `<your_id>.json` file in it, following the schema of the built-ins in
   `backend/app/scenarios/presets/`.
3. It is loaded automatically. A user preset with the same `id` as a built-in
   **overrides** it, so you can retune a shipped scenario without editing
   source.

Minimal example:

```json
{
  "id": "product_launch",
  "name": "Product Launch Buzz",
  "domain": "social_media",
  "activity_rhythm": { "timezone": "UTC", "peak_hours": [12, 13, 18, 19, 20] },
  "default_total_hours": 96,
  "channels": [
    { "id": "social", "engine_platform": "twitter", "viral_threshold": 8 },
    { "id": "forum",  "engine_platform": "reddit" }
  ],
  "stances": ["excited", "critical", "neutral", "observer"],
  "default_stance": "neutral",
  "prompt_framing": "Simulate public reaction to a new product launch..."
}
```

Presets are validated on load; malformed or invalid files raise a clear
`ScenarioLoadError` at startup rather than failing silently at runtime.

## Tests

```bash
cd backend
python tests/test_scenarios.py                    # scenario layer (no deps)
python tests/test_config_generator_scenarios.py   # generator wiring
# or, with pytest installed:
python -m pytest tests/ -q
```
