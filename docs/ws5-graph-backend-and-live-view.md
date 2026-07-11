# WS-5 — Pluggable graph backend + live simulation view

Covers three pieces of feedback that turn out to be two workstreams:
- **#2 "make our own temporal graphs"** + **#5 "cooperation with my RAG harness"**
  → **Part A: pluggable BYO-graph-backend** (Zep *or* Mnemosyne), mirroring the
  existing BYO-LLM provider picker. Keep Zep, add Mnemosyne, offer both.
- **#3 "live agent relationship graph + convergence histogram"**
  → **Part B: live simulation view.**

Status: **planning.** Parts A and B are independent — do either first.

---

## STATUS: WS-5 COMPLETE (Jul 5 2026)
All of Part A (phases 1–5) and Part B implemented + tested. Zep remains the default
and is behavior-unchanged; Mnemosyne is a working self-host backend.

**How to use Mnemosyne locally:** in Settings → Memory graph, pick "Mnemosyne (local)".
Needs the Mnemosyne venv at `~/projects/mnemosyne/.venv/bin/python` (override with
`MNEMOSYNE_VENV_PYTHON`). With an LLM key set, extraction uses it (OpenAI-compatible)
and honors your project ontology; with no key it falls back to the rules extractor.

**Known limitations (acceptable, documented):**
- Mnemosyne is **self-host only** (loomstate is an absolute `file://` dep) — public
  delphi.example.com users use Zep. To offer it publicly, vendor loomstate+mnemosyne.
- ~~The report agent's rich Zep toolkit is Zep-specific…~~ **RESOLVED (Jul 5 2026):**
  `report_tools()` is now on the interface; `MnemosyneReportTools` implements
  insight_forge / panorama_search / quick_search / get_entity_summary /
  get_entities_by_type / get_graph_statistics on `backend.search()` + graph reads
  (returns the same `SearchResult`/`InsightForgeResult` types, so `.to_text()` works
  unchanged), and interview_agents delegates to the existing sim-side impl. report.py
  builds the agent's tools from `get_graph_backend(creds).report_tools()`. Reports now
  run fully on Mnemosyne. Tested on both backends.
- Mnemosyne entity types are coarse (person/org/thing) with the rules extractor; the
  LLM extractor + injected ontology (A4) produces richer typing when a key is set.

### Implementation map
- `graph_backends/base.py` — `GraphBackend` ABC.
- `graph_backends/zep.py` — `ZepBackend` (wraps existing services, unchanged behavior).
- `graph_backends/mnemosyne.py` — `MnemosyneBackend`, subprocess-per-op, per-project
  data dir under the workspace, returns Zep-compatible `EntityNode`/`FilteredEntities`.
- `graph_backends/mnemosyne_worker.py` — runs under Mnemosyne's venv; ops: ingest
  (with A4 ontology-prompt injection), graph_data, entities, search.
- `graph_backends/factory.py` — `get_graph_backend(creds)`.
- Wired through: `graph.py` (build/data/delete), `simulation.py` + `simulation_manager.py`
  (entity reads), Settings picker (`X-Graph-Provider`).

---

## Part A — Pluggable graph backend (Zep **or** Mnemosyne)

### The idea
Delphi already treats the **LLM** as a swappable, user-chosen provider. Do the
same for the **memory graph**: an interface Delphi codes against, with two
implementations the user picks between in Settings — exactly the "offer multiple
solutions" model.

- **Zep Cloud** — current default. BYO Zep key. Managed, async, proven.
- **Mnemosyne** (`~/projects/mnemosyne/`) — self-hosted, in-process, **no external
  key, no free-tier cap**. A temporal GraphRAG engine the user already built.

### What Delphi actually needs from a graph backend
(from `zep_tools.py`, `zep_entity_reader.py`, `api/graph.py`)

| Capability | Zep call today | Mnemosyne equivalent |
|---|---|---|
| create graph per project | `create_graph(name)` | one `GraphStore(path=…)` per project |
| set custom ontology | `set_ontology(types)` | ⚠️ **gap** — extend extractor prompt/schema |
| ingest document chunks | `add_text_batches()` (async) | `pipeline.ingest()` (sync) |
| read nodes/edges | `get_graph_data()` | `store.all_relations()` |
| agent retrieval | `graph.search(q)` | `GraphRetriever.retrieve()` (temporal-aware) |
| get entity | `graph.node.get()` | `store.entity()` |
| agent memory write | Zep memory | `memory.remember()` |

### Design
1. **Define `GraphBackend` ABC** in `backend/app/services/graph_backends/base.py`:
   `create_graph, set_ontology, ingest, get_graph_data, search, get_entity,
   remember`. Return plain dataclasses (not Zep types) so callers are backend-agnostic.
2. **`ZepBackend`** — move the current Zep code behind the ABC. **No behavior change**;
   this is a pure refactor and the safety net.
3. **`MnemosyneBackend`** — import Mnemosyne as a **library** (both Python; in-process,
   no network service). Map the table above. Data dir keyed per
   `workspace_id/project_id` (reuses WS-2 isolation cleanly).
4. **Selection = provider picker.** Add `graph_provider` to the BYO settings the
   browser already sends (new `X-Graph-Provider` header + `creds`), and a section in
   `SettingsModal.vue`: "Memory graph → Zep Cloud (bring key) | Mnemosyne (local, no
   key)". `graph.py` / `simulation.py` build the chosen backend per request, same way
   they build the LLM client.

### ⚠️ Hard constraints discovered in Mnemosyne's code (reshape A3)
A deep API scan found three things that change the integration design:
1. **Data dir frozen at import time.** Mnemosyne reads `MNEMOSYNE_DATA`/`LOOMSTATE_DATA`
   into module constants on first `import mnemosyne`. A long-lived Flask process can
   therefore only bind ONE project's graph. → **A3 must run each Mnemosyne op in a
   short-lived subprocess** (env set → import → op → exit), which also gives per-project
   isolation for free. Not a library call; a subprocess worker (`mnemosyne_worker.py`).
2. **No custom-ontology hook.** Entity types are hardcoded `person|org|thing` and the
   extraction prompt (`EXTRACT_PROMPT`) is a module constant with no parameter. → **A4**
   must monkeypatch/override that prompt inside the worker to inject Delphi's types.
3. **loomstate dep is an absolute `file://` path** (`file:///Users/.../loomstate`), editable-
   installed. → Mnemosyne **won't `pip install` on the public VPS** as-is. **Strategic
   consequence:** Mnemosyne is a **local / self-host power-user backend**, not something
   public delphi.example.com users get. Public users still pick Zep (BYO key). This is
   fine — it removes the Zep dependency for self-hosters — but it is *not* "kill Zep for
   everyone." To offer it publicly you'd vendor loomstate+mnemosyne into the image.

Good news from the scan: extraction **can** use the user's OpenAI-compatible LLM key
(`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` env), and embeddings need no key
(`LOOMSTATE_EMBEDDER=hash` avoids the model download). So the BYO-LLM key drives
Mnemosyne extraction too.

### Gaps to close for Mnemosyne (from the code scan)
1. **Custom ontology** — Mnemosyne emits person/org/thing + free-text predicates;
   Delphi's `set_ontology()` passes project-specific typed entities/edges. Extend
   Mnemosyne's extraction prompt to accept a type schema. *(biggest item)*
2. **Per-project isolation** — parameterize `GraphStore` by data dir; trivial, maps to
   `workspace_root()/projects/<id>/graph`.
3. **Extraction quality unvalidated at Delphi's scale** — the author's own eval notes
   flag this. Gate the swap on measuring it with **Tripwire** (`~/projects/tripwire/`)
   on a sample seed doc before defaulting anyone to it.
4. *(optional)* async ingestion via **Sluice** (`~/projects/sluice/`) if large docs
   feel slow; sync is fine for the small seed docs the free-tier story already implies.

### Phasing
1. `GraphBackend` ABC + `ZepBackend` refactor (behavior-preserving). *← ship first, low risk*
2. Settings picker + per-request backend selection (Zep still default).
3. `MnemosyneBackend` (library integration + per-project dir).
4. Custom-ontology support in Mnemosyne.
5. Validate extraction with Tripwire → then allow Mnemosyne as default.

### Payoff
Removes the last operator/user cost surface (Zep free-tier limit) for anyone who
picks Mnemosyne; it's the user's own code; and Mnemosyne's provenance + temporal
edges + Plumbline repair are strictly more than Zep exposes. Zep stays for those who
want managed/zero-setup — same "pick your provider" ethos as the LLM layer.

---

## Part B — Live simulation view (#3)  ✅ IMPLEMENTED (Jul 5 2026)

Done + tested on real sim data. Backend: `services/live_aggregator.py` reads both
platforms' `actions.jsonl`, resolves interaction edges via `post_id→author`
(likes/reposts/quotes/comments) + `FOLLOW`, and buckets each round's actions by
stance (from `agent_configs[].stance`) and action-type. Endpoint
`GET /api/simulation/<id>/live` (workspace-scoped, poll-safe) returns
`{nodes, edges, rounds, stances, current_round, total_rounds, status, total_actions}`.
Frontend: `components/LiveView.vue` — a D3 force interaction graph (nodes colored by
stance, sized by activity) + a stacked stance-convergence histogram over rounds,
polling every 3s and auto-stopping when the sim ends. Added as a **"Live"** mode in
`SimulationRunView.vue`'s switcher (full-width). Verified: aggregator extracts
stance/entity_type/edges from the real sample sim; endpoint enforces WS-2 isolation;
prod build passes. (Original design notes below.)

### Original design

Show, **while the sim runs**: (1) a live graph of agent interactions and (2) a
convergence histogram of opinion over rounds — distinct from the static workbench
knowledge graph. This is the marquee "prediction engine" view.

### Data source (already produced)
The sim streams **`actions.jsonl`** (posts, comments, likes, follows, per round),
parsed live by `SimulationRunner._monitor_simulation`. Agent **stance** is available
from the config-generator output (supportive / opposing / neutral / observer).

### Backend
- Add an incremental endpoint (SSE or poll) exposing, per round:
  - **interaction edges**: `(actor → target, action_type, round)` derived from actions.
  - **stance aggregate**: count per stance per round (for the histogram).
- Compute from the existing action queue; no new sim work, just aggregation +
  exposure. Workspace-scoped like every other sim endpoint.

### Frontend
- New **"Live"** mode alongside Graph / Split / Workbench in `SimulationRunView.vue`.
- **Live interaction graph** (D3 force layout, reuse the existing graph viz): nodes =
  agents, edges = interactions, animating as rounds arrive; edge color by action type.
- **Convergence histogram**: stacked area / bar of stance distribution over rounds —
  watch consensus form or fracture. This *is* the prediction signal.

### Effort
Medium–large: backend aggregation + stream endpoint, plus a D3 live view. Biggest
value for demos and for making the "predict everything" tagline real.

### Interplay with Part A
If Mnemosyne backs the graph, its **temporal edges + provenance** can enrich both the
live view (why an agent shifted) and the final report (cited, timestamped facts) —
more than Zep surfaces today.

---

## Recommended order
Part A phase 1–2 (interface + picker, Zep still default) is low-risk and unlocks the
Mnemosyne path incrementally. Part B is independent and higher demo-value. Suggested:
**A1 (refactor behind interface) → B (live view) → A3–5 (Mnemosyne swap + validate).**
Do A1 first regardless — coding against an interface is the right shape even if Zep
stays the only backend for a while.
