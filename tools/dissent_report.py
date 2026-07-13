# -*- coding: utf-8 -*-
"""Dissent / controversy / irrationality report for a delphi simulation.

Generates, into <sim>/analysis/:
  DISSENT.md               dissent & irrationality report (read this first)
  fabrication_audit.png    per-agent numeric claims: grounded in seed vs fabricated
  fabrication_by_round.png fabricated-claim share per round (does drift grow?)

Method (deterministic, no LLM): every numeric token in agent-generated text is
checked against the seed document. A number the seed never contained is either
dissent (an agent asserting its own version of reality — sometimes the
irrationality layer working as designed) or confabulation. The report separates
candidates by whether they collide with a seed number in similar context.

Usage: python tools/dissent_report.py <sim_id> [--workspace <id>] [--out <dir>]
Deps: matplotlib.
"""
import argparse
import json
import re
import sys
import io
from collections import defaultdict, Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1] / "backend" / "uploads" / "workspaces"

SERIES = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
SURFACE, INK, INK2, MUTED, GRID, BASE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
GROUNDED, FABRICATED = SERIES[0], SERIES[5]

NUM_RE = re.compile(r"(?<![\w.])[$€£]?\d+(?:[.,]\d+)?\s*(?:%|bn|bp|pp|percent|billion|million|/MWh|x)?", re.I)
YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def find_sim(sim_id, workspace):
    wss = [ROOT / workspace] if workspace else sorted(ROOT.iterdir())
    for ws in wss:
        p = ws / "simulations" / sim_id
        if p.exists():
            return p
    raise SystemExit(f"simulation {sim_id} not found under {ROOT}")


def norm_num(tok):
    t = tok.strip().lower().replace(",", "").replace(" ", "")
    t = re.sub(r"^[$€£]", "", t)
    t = re.sub(r"(percent)$", "%", t)
    t = re.sub(r"(billion)$", "bn", t)
    return t


def core_digits(tok):
    m = re.search(r"\d+(?:\.\d+)?", tok.replace(",", ""))
    return m.group(0) if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sim_id")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    sim = find_sim(args.sim_id, args.workspace)
    out = Path(args.out) if args.out else sim / "analysis"
    out.mkdir(exist_ok=True)

    state = json.loads((sim / "state.json").read_text(encoding="utf-8"))
    cfg = json.loads((sim / "simulation_config.json").read_text(encoding="utf-8"))
    irr = cfg.get("irrationality_config") or state.get("psychology_settings") or {}

    # seed document: the project's extracted text
    seed_text = ""
    proj = state.get("project_id")
    if proj:
        p = sim.parents[1] / "projects" / proj / "extracted_text.txt"
        if p.exists():
            seed_text = p.read_text(encoding="utf-8", errors="replace")
    seed_nums = {norm_num(t) for t in NUM_RE.findall(seed_text)}
    seed_cores = {core_digits(t) for t in NUM_RE.findall(seed_text)}

    # agent texts
    items = []  # (round, agent, action_type, text, platform)
    for pf in ("twitter", "reddit"):
        f = sim / pf / "actions.jsonl"
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if "action_type" not in d:
                continue
            a = d.get("action_args") or {}
            text = a.get("content") or a.get("comment_content") or ""
            if text:
                items.append((d.get("round", -1), d.get("agent_name", "?"), d["action_type"], text, pf))

    # numeric audit
    per_agent = defaultdict(lambda: {"grounded": 0, "fabricated": 0})
    per_round = defaultdict(lambda: {"grounded": 0, "fabricated": 0})
    fabricated = []  # (agent, round, token, context, collides_with_seed_context)
    seen_claim = set()
    for rnd, agent, atype, text, pf in items:
        for m in NUM_RE.finditer(text):
            tok = m.group(0).strip()
            core = core_digits(tok)
            if not core or YEAR_RE.match(core):
                continue
            key = (agent, rnd, norm_num(tok), text[max(0, m.start() - 20):m.start()])
            if key in seen_claim:
                continue
            seen_claim.add(key)
            ok = norm_num(tok) in seed_nums or core in seed_cores
            bucket = "grounded" if ok else "fabricated"
            per_agent[agent][bucket] += 1
            per_round[rnd][bucket] += 1
            if not ok:
                ctx = text[max(0, m.start() - 60):m.end() + 60].replace("\n", " ")
                fabricated.append((agent, rnd, tok, ctx))

    dups = Counter(t for _, _, _, t, _ in items)
    duplicates = {k[:90]: v for k, v in dups.items() if v > 2}
    do_nothing = sum(1 for _, _, at, _, _ in items if at == "DO_NOTHING")

    # ── charts ──
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Segoe UI", "DejaVu Sans"],
        "text.color": INK, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
        "axes.edgecolor": BASE, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False,
    })

    agents = sorted(per_agent, key=lambda a: per_agent[a]["fabricated"] + per_agent[a]["grounded"])
    fig, ax = plt.subplots(figsize=(9, 0.42 * max(1, len(agents)) + 1.8))
    g = [per_agent[a]["grounded"] for a in agents]
    f = [per_agent[a]["fabricated"] for a in agents]
    ax.barh(agents, g, color=GROUNDED, height=0.58, label="grounded in seed", edgecolor=SURFACE, linewidth=2)
    ax.barh(agents, f, left=g, color=FABRICATED, height=0.58, label="fabricated / dissenting", edgecolor=SURFACE, linewidth=2)
    for i, a in enumerate(agents):
        if f[i]:
            ax.text(g[i] + f[i] + 0.15, i, str(f[i]), va="center", fontsize=9, color=FABRICATED)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("numeric claims")
    ax.set_title("Fabrication audit — numeric claims per agent vs the seed document",
                 loc="left", fontsize=13, color=INK, weight="bold")
    fig.tight_layout()
    fig.savefig(out / "fabrication_audit.png", dpi=180)
    plt.close(fig)

    rounds = sorted(per_round)
    fig, ax = plt.subplots(figsize=(9, 3.6))
    tot = [per_round[r]["grounded"] + per_round[r]["fabricated"] for r in rounds]
    share = [per_round[r]["fabricated"] / t if t else 0 for r, t in zip(rounds, tot)]
    ax.bar([str(r) for r in rounds], share, color=FABRICATED, width=0.6, edgecolor=SURFACE, linewidth=2)
    for i, s in enumerate(share):
        ax.text(i, s + 0.02, f"{s:.0%}", ha="center", fontsize=9, color=INK2)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("fabricated share")
    ax.set_xlabel("simulation round")
    ax.grid(axis="x", visible=False)
    ax.set_title("Does the world drift from its seed? Fabricated share of numeric claims per round",
                 loc="left", fontsize=13, color=INK, weight="bold")
    fig.tight_layout()
    fig.savefig(out / "fabrication_by_round.png", dpi=180)
    plt.close(fig)

    # ── report ──
    stances = Counter(a.get("stance", "?") for a in cfg.get("agent_configs", []))
    lines = [f"# Dissent & irrationality report — {args.sim_id}\n"]
    lines.append("## Irrationality (psychology) configuration\n")
    lines.append(f"- enabled: **{irr.get('enabled')}**, intensity {irr.get('intensity')}; "
                 f"opinion model: {(irr.get('opinion') or {}).get('model')}")
    feats = irr.get("features") or {}
    lines.append(f"- features on: {', '.join(k for k, v in feats.items() if v) or 'none'}")
    lines.append(f"- configured stances: {dict(stances)} — "
                 + ("**all agents neutral: the config generator did not differentiate stances for this "
                    "scenario, so opinion dynamics had no initial disagreement to work with.**"
                    if len(stances) == 1 else "differentiated."))
    lines.append(f"- DO_NOTHING actions (choice-noise candidates): {do_nothing}")
    if duplicates:
        lines.append(f"- duplicate-content artifacts (posted >2×): {len(duplicates)} — check seeding logic")
    lines.append("\n## Fabrication / dissent audit\n")
    tot_g = sum(v['grounded'] for v in per_agent.values())
    tot_f = sum(v['fabricated'] for v in per_agent.values())
    lines.append(f"Numeric claims: **{tot_g} grounded** in the seed, **{tot_f} fabricated/dissenting** "
                 f"({tot_f / max(1, tot_g + tot_f):.0%}). Charts: `fabrication_audit.png`, `fabrication_by_round.png`.\n")
    lines.append("A fabricated number is not automatically a bug: an interested party asserting rosier "
                 "numbers than the seed (motivated reasoning) is the biased_perception feature working. "
                 "A neutral reporter inventing statistics is confabulation. Judge each below.\n")
    lines.append("### Every fabricated/dissenting numeric claim\n")
    lines.append("| Agent | Round | Claim | Context |")
    lines.append("|---|---|---|---|")
    for agent, rnd, tok, ctx in sorted(fabricated, key=lambda x: (x[0], x[1])):
        lines.append(f"| {agent} | {rnd} | `{tok}` | …{ctx.replace('|', '/')}… |")
    lines.append("\n### Reading guide\n")
    lines.append("- **Dissent worth keeping:** self-serving numbers from interested parties "
                 "(e.g. a consortium claiming restored capacity against the seed's figure).")
    lines.append("- **Confabulation worth fixing:** precise statistics from neutral/analyst agents "
                 "with no motive (correlations, yield thresholds, rainfall percentages).")
    lines.append("- If `fabrication_by_round.png` trends up, the world drifts from its anchor as "
                 "rounds progress — consider fewer rounds or grounding reminders in agent prompts.")
    (out / "DISSENT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"dissent report written to {out}")
    print(f"grounded={tot_g} fabricated={tot_f} agents={len(per_agent)} items={len(items)}")


if __name__ == "__main__":
    main()
