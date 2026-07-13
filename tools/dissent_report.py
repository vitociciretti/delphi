# -*- coding: utf-8 -*-
"""Dissent / controversy / irrationality report for a delphi simulation.

Generates, into <sim>/analysis/:
  DISSENT.md                       model-risk audit (fabricated numeric claims)
  fabrication_audit.png            per-agent numeric claims: grounded vs fabricated
  fabrication_by_round.png         fabricated-claim share per round (drift)
and, when an OpenAI-compatible LLM is reachable (default: local Ollama):
  DISSENT_IDEAS.md (+ .docx if pandoc is installed)   controversial / non-consensus
  dissent_position_map.png                            IDEAS: who breaks from the
  dissent_contested_questions.png                     crowd, on what, saying what

Fabrication audit is deterministic (numeric tokens checked against the seed doc).
The ideas layer is LLM-assisted: one call discovers the contested questions in the
agents' own words, then one call per agent classifies its position. Use a fast
non-thinking model for this (llama3.1) — set DISSENT_LLM_MODEL to override.

Usage: python tools/dissent_report.py <sim_id> [--workspace <id>] [--out <dir>] [--no-ideas]
Env:   DISSENT_LLM_BASE_URL (default http://localhost:11434/v1)
       DISSENT_LLM_MODEL    (default llama3.1)
Deps: matplotlib. pandoc optional (for the .docx).
"""
import argparse
import json
import os
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


# ───────────────────────── LLM-assisted ideas layer ─────────────────────────

def _llm_json(system, prompt, max_tokens=1500):
    """One chat call against an OpenAI-compatible endpoint; returns parsed JSON or None."""
    import urllib.request
    base = os.environ.get("DISSENT_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    model = os.environ.get("DISSENT_LLM_MODEL", "llama3.1")
    body = json.dumps({
        "model": model, "temperature": 0, "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"] or ""
        raw = re.sub(r"<think>[\s\S]*?</think>", "", raw)
        return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except Exception as e:
        print(f"[ideas] LLM call failed: {e}")
        return None


def ideas_layer(items, out):
    """Discover contested questions and per-agent positions; render charts + report."""
    by_agent = defaultdict(list)
    for rnd, agent, atype, text, pf in items:
        by_agent[agent].append(f"(r{rnd}) {text}")
    corpus = "\n".join(f"[{a}] {t}" for a, ts in by_agent.items() for t in ts)[:12000]

    disc = _llm_json(
        "You analyze multi-agent debates. JSON only.",
        "Below are all posts from a multi-agent simulation. Identify 3 to 6 genuinely CONTESTED "
        "questions — where different agents take opposing positions (not topics everyone agrees on). "
        "Respond ONLY JSON: {\"questions\": [{\"label\": \"short question\", "
        "\"pole_a\": \"one position, short\", \"pole_b\": \"the opposing position, short\"}]}\n\n" + corpus)
    if not disc or not disc.get("questions"):
        print("[ideas] no contested questions returned — skipping ideas layer")
        return
    questions = disc["questions"][:6]

    positions = {}  # agent -> list of 'a'|'b'|'none'
    qlist = "\n".join(f"{i+1}. {q['label']} | A: {q['pole_a']} | B: {q['pole_b']}"
                      for i, q in enumerate(questions))
    for agent, texts in by_agent.items():
        res = _llm_json(
            "You classify a debate participant's positions. JSON only.",
            f"Questions (each with position A and position B):\n{qlist}\n\n"
            f"Everything agent \"{agent}\" wrote:\n" + "\n".join(texts)[:6000] +
            "\n\nFor EACH question, does this agent support A, B, or take no clear position? "
            "Respond ONLY JSON: {\"positions\": [\"a\"|\"b\"|\"none\", ...]} "
            f"(exactly {len(questions)} entries, in order).")
        pos = (res or {}).get("positions") or []
        positions[agent] = [str(p).lower() if str(p).lower() in ("a", "b") else "none"
                            for p in (pos + ["none"] * len(questions))[:len(questions)]]

    # majority pole per question; dissent = minority pole
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Segoe UI", "DejaVu Sans"],
        "text.color": INK, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
        "axes.edgecolor": BASE, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    })
    CONSENSUS, DIS, SILENT = SERIES[0], SERIES[5], "#f0efec"
    agents = sorted(by_agent, key=lambda a: -len(by_agent[a]))
    rows = []
    for qi, q in enumerate(questions):
        a_n = sum(1 for ag in agents if positions[ag][qi] == "a")
        b_n = sum(1 for ag in agents if positions[ag][qi] == "b")
        crowd = "a" if a_n >= b_n else "b"
        rows.append({"q": q, "crowd": crowd,
                     "crowd_n": max(a_n, b_n), "dis_n": min(a_n, b_n),
                     "dissenters": [ag for ag in agents
                                    if positions[ag][qi] not in ("none", crowd)]})

    ny, nx = len(questions), len(agents)
    fig, ax = plt.subplots(figsize=(max(9, 0.85 * nx + 2.5), 0.8 * ny + 1.8))
    for qi, row in enumerate(rows):
        for xi, ag in enumerate(agents):
            p = positions[ag][qi]
            c = SILENT if p == "none" else (CONSENSUS if p == row["crowd"] else DIS)
            ax.add_patch(plt.Rectangle((xi + 0.06, ny - 1 - qi + 0.08), 0.88, 0.84,
                                       facecolor=c, edgecolor=SURFACE, linewidth=2))
    ax.set_xlim(0, nx); ax.set_ylim(0, ny)
    ax.set_xticks([i + 0.5 for i in range(nx)])
    ax.set_xticklabels(agents, rotation=32, ha="right", fontsize=8.5)
    ax.set_yticks([ny - 1 - i + 0.5 for i in range(ny)])
    ax.set_yticklabels([r["q"]["label"][:48] for r in rows], fontsize=8.5)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.legend(handles=[mpatches.Patch(color=CONSENSUS, label="holds the crowd view"),
                       mpatches.Patch(color=DIS, label="dissents / contrarian"),
                       mpatches.Patch(color=SILENT, label="no stated position")],
              frameon=False, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, -0.34), ncol=3)
    ax.set_title("Who breaks from the crowd — agent positions on the contested questions",
                 loc="left", fontsize=13, color=INK, weight="bold", pad=14)
    fig.tight_layout()
    fig.savefig(out / "dissent_position_map.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.5, 0.75 * len(rows) + 1.6))
    y = list(range(len(rows)))[::-1]
    ax.barh(y, [r["crowd_n"] for r in rows], color=CONSENSUS, height=0.55,
            label="crowd view", edgecolor=SURFACE, linewidth=2)
    ax.barh(y, [r["dis_n"] for r in rows], left=[r["crowd_n"] for r in rows],
            color=DIS, height=0.55, label="dissenters", edgecolor=SURFACE, linewidth=2)
    for yi, r in zip(y, rows):
        dis_pole = "b" if r["crowd"] == "a" else "a"
        ax.text(r["crowd_n"] + r["dis_n"] + 0.15, yi,
                "“" + r["q"][f"pole_{dis_pole}"][:60] + "”",
                va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(y)
    ax.set_yticklabels([r["q"]["label"][:40] for r in rows], fontsize=9)
    ax.set_xlabel("agents with a stated position")
    ax.grid(axis="y", visible=False); ax.grid(axis="x", color=GRID, linewidth=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    ax.set_title("How contested is each question — and what the dissenters say",
                 loc="left", fontsize=13, color=INK, weight="bold")
    fig.tight_layout()
    fig.savefig(out / "dissent_contested_questions.png", dpi=180)
    plt.close(fig)

    lines = ["# Controversial & non-consensus ideas (auto-generated)\n",
             "*Contested questions discovered and positions classified by "
             f"{os.environ.get('DISSENT_LLM_MODEL', 'llama3.1')}; verify against "
             "`all_agent_texts.md` before quoting.*\n",
             "![Position map](dissent_position_map.png)\n",
             "![Contested questions](dissent_contested_questions.png)\n"]
    for r in rows:
        q = r["q"]
        dis_pole = "b" if r["crowd"] == "a" else "a"
        lines.append(f"## {q['label']}")
        lines.append(f"- **Crowd view** ({r['crowd_n']}): {q['pole_' + r['crowd']]}")
        lines.append(f"- **Dissent** ({r['dis_n']}): {q['pole_' + dis_pole]}")
        lines.append(f"- **Dissenters:** {', '.join(r['dissenters']) or '—'}\n")
    (out / "DISSENT_IDEAS.md").write_text("\n".join(lines), encoding="utf-8")

    # optional Word export
    import shutil, subprocess
    pandoc = shutil.which("pandoc") or os.path.expandvars(r"%LOCALAPPDATA%\Pandoc\pandoc.exe")
    if pandoc and os.path.isfile(pandoc):
        try:
            subprocess.run([pandoc, str(out / "DISSENT_IDEAS.md"), "-f", "gfm", "-t", "docx",
                            "-o", str(out / "DISSENT_IDEAS.docx"),
                            f"--resource-path={out}"], check=True, timeout=120)
        except Exception as e:
            print(f"[ideas] docx export skipped: {e}")
    print(f"[ideas] {len(rows)} contested questions, positions for {len(agents)} agents")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sim_id")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-ideas", action="store_true",
                    help="skip the LLM-assisted controversial-ideas layer")
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

    if not args.no_ideas:
        ideas_layer(items, out)


if __name__ == "__main__":
    main()
