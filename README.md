<div align="center">

<img src="./frontend/src/assets/logo/delphi-mark.svg" alt="Delphi Logo" width="220"/>

# Delphi

**A multi-agent world simulation and prediction engine**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](./LICENSE)
[![Node](https://img.shields.io/badge/node-%3E%3D18-339933?logo=node.js&logoColor=white)](https://nodejs.org)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](https://www.python.org)

[English](./README.md) | [中文文档](./README-ZH.md)

</div>

## ⚡ Overview

Delphi builds a high-fidelity parallel digital world from real-world seed material — a
news article, a policy draft, a financial signal, a story you want to re-imagine.
Inside that world, agents with independent personalities, long-term memory and
behavioral logic interact and evolve. You can inject variables from a god's-eye view
and watch how the resulting dynamics play out — rehearsing outcomes in a sandbox
before they happen for real.

> Give it seed material and a natural-language prediction question.
> It gives back a detailed report and an interactive digital world you can keep
> exploring after the simulation ends.

### Vision

- **Macro**: a zero-risk rehearsal lab for decision-makers — test policies, messaging,
  and market moves before committing to them.
- **Micro**: a creative sandbox — deduce alternate story endings or explore
  "what if" scenarios just for fun.

## 🔄 Workflow

1. **Graph Building** — seed extraction, individual/collective memory injection, GraphRAG construction
2. **Environment Setup** — entity relationship extraction, persona generation, agent configuration injection
3. **Simulation** — parallel multi-channel simulation, auto-parsed prediction requirements, dynamic temporal memory updates
4. **Report Generation** — a report agent with a rich toolset for deep interaction with the post-simulation world
5. **Deep Interaction** — chat with any agent in the simulated world, or with the report agent itself

## 🧭 Scenario Presets — adapting to different scopes

Delphi doesn't assume a single scope. Activity rhythm, time horizon, communication
channels, stance vocabulary and LLM prompt framing are all externalised into
**config-driven scenario presets**, so the same engine can model very different
worlds:

| Preset | Scope |
|--------|-------|
| `social_media` *(default)* | Social-media public opinion, single timezone. |
| `global_social_media` | Worldwide, cross-timezone opinion (round-the-clock activity). |
| `financial_market` | Investor/analyst sentiment around a market signal. |
| `organization` | How a decision propagates inside an organisation. |
| `creative_narrative` | Characters acting out an alternate/lost story ending. |

Pick one per run:

```http
POST /api/simulation/create   { "project_id": "proj_x", "scenario_id": "financial_market" }
GET  /api/simulation/scenarios
```

Add your **own domain with zero code** by dropping a JSON file into
`SCENARIO_PRESETS_DIR`. Full guide: [`docs/SCENARIOS.md`](./docs/SCENARIOS.md).

## 🚀 Quick Start

### Option 1: Source Code Deployment (Recommended)

#### Prerequisites

| Tool | Version | Description | Check Installation |
|------|---------|-------------|-------------------|
| **Node.js** | 18+ | Frontend runtime, includes npm | `node -v` |
| **Python** | ≥3.11, ≤3.12 | Backend runtime | `python --version` |
| **uv** | Latest | Python package manager | `uv --version` |

#### 1. Configure Environment Variables

```bash
# Copy the example configuration file
cp .env.example .env

# Edit the .env file and fill in the required API keys
```

**Required Environment Variables:**

```env
# LLM API Configuration (supports any LLM API with OpenAI SDK format)
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.your-provider.com/v1
LLM_MODEL_NAME=your-model-name

# Zep Cloud Configuration (memory graph)
# Free monthly quota is sufficient for simple usage: https://app.getzep.com/
ZEP_API_KEY=your_zep_api_key
```

#### 2. Install Dependencies

```bash
# One-click installation of all dependencies (root + frontend + backend)
npm run setup:all
```

Or install step by step:

```bash
# Install Node dependencies (root + frontend)
npm run setup

# Install Python dependencies (backend, auto-creates virtual environment)
npm run setup:backend
```

#### 3. Start Services

```bash
# Start both frontend and backend (run from project root)
npm run dev
```

**Service URLs:**
- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:5001`

**Start Individually:**

```bash
npm run backend   # Start backend only
npm run frontend  # Start frontend only
```

### Option 2: Docker Deployment

```bash
# 1. Configure environment variables (same as source deployment)
cp .env.example .env

# 2. Build and start
docker compose up -d --build
```

Reads `.env` from the root directory by default, maps ports `3000` (frontend) and
`5001` (backend).

## 📄 License & Attribution

Delphi is licensed under the **[GNU Affero General Public License v3.0](./LICENSE)**.

Delphi is a derivative work built on **[MiroFish](https://github.com/666ghj/MiroFish)**
(AGPL-3.0), extended with a config-driven scenario/domain preset layer so the same
engine can model social media, financial markets, organizations, and narrative
worlds instead of a single fixed scope. See [`NOTICE.md`](./NOTICE.md) for the
full list of modifications.

Delphi's simulation engine is powered by
**[OASIS (Open Agent Social Interaction Simulations)](https://github.com/camel-ai/oasis)**
— thanks to the CAMEL-AI team for their open-source work.
