# NOTICE

**Delphi** is a derivative work based on **MiroFish**
(<https://github.com/666ghj/MiroFish>), a multi-agent simulation / prediction
engine by the MiroFish authors and Shanda Group.

MiroFish is licensed under the **GNU Affero General Public License v3.0**
(AGPL-3.0). Delphi preserves that license — see [`LICENSE`](./LICENSE) —
and, in accordance with the AGPL, distributes its modified source and makes it
available to users who interact with it over a network.

## Relationship to the upstream project

Delphi tracks the MiroFish codebase and adds improvements aimed at
making the engine adaptable to **different scopes / domains** rather than being
hard-wired to social-media public-opinion simulation.

## Summary of modifications

- Added a **scenario / domain preset layer** (`backend/app/scenarios/`) that
  externalises "what kind of world" a simulation runs in — activity rhythm,
  time horizon, communication channels, stance vocabulary and LLM prompt
  framing — into config-driven JSON presets.
- Shipped built-in presets for social media (default, backward compatible),
  global social media, financial-market sentiment, organizational decisions,
  and creative/narrative worlds.
- Made the simulation config generator, manager and API **consume the selected
  scenario** instead of the previously hard-coded Twitter/Reddit +
  China-timezone assumptions, while keeping the original behaviour as the
  default for full backward compatibility.
- Added a `GET /api/simulation/scenarios` endpoint and a
  `SCENARIO_DEFAULT` / `SCENARIO_PRESETS_DIR` configuration surface so new
  domains can be added by dropping a JSON file — no code changes required.
- Added unit and integration tests for the scenario layer under
  `backend/tests/`.

All original copyright and license notices are retained. Trademarks, logos and
project names referenced from the upstream project remain the property of their
respective owners.
