# -*- coding: utf-8 -*-
"""Post-run analysis bundle for a delphi simulation.

Generates, for one simulation:
  activity_by_round.png    agent activity per round, by action type, per platform
  agent_activity.png       actions per agent (stacked by type)
  interaction_graph.png    who replied to whom (comment -> post-author edges)
  all_agent_texts.md       every generated post/comment for close reading
  run_summary.json         counts, stances input material, artifacts checklist

Usage:
  python tools/analyze_run.py <sim_id> [--workspace <id>] [--out <dir>]

Deps: matplotlib, networkx (not part of delphi's backend env — run under any
python that has them). Colors follow a validated categorical palette; do not
reorder the slots.
"""
import argparse
import json
import re
import sqlite3
import sys
import io
from collections import defaultdict, Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1] / "backend" / "uploads" / "workspaces"

# validated categorical palette (light mode), fixed slot order — do not cycle
SERIES = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
SURFACE, INK, INK2, MUTED, GRID, BASE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"


def find_sim(sim_id: str, workspace: str | None) -> Path:
    wss = [ROOT / workspace] if workspace else sorted(ROOT.iterdir())
    for ws in wss:
        p = ws / "simulations" / sim_id
        if p.exists():
            return p
    raise SystemExit(f"simulation {sim_id} not found under {ROOT}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sim_id")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    sim = find_sim(args.sim_id, args.workspace)
    out = Path(args.out) if args.out else sim / "analysis"
    out.mkdir(exist_ok=True)

    # ── actions ──
    actions = []
    for platform in ("twitter", "reddit"):
        p = sim / platform / "actions.jsonl"
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if "action_type" in d:
                d["platform"] = platform
                actions.append(d)
    if not actions:
        raise SystemExit("no agent actions found")
    by_type = Counter(a["action_type"] for a in actions)

    # ── text dump ──
    dump = [f"# All agent-generated text — {args.sim_id}\n"]
    for pf in ("twitter", "reddit"):
        dump.append(f"\n## {pf}\n")
        for a in actions:
            if a["platform"] != pf:
                continue
            c = (a.get("action_args") or {}).get("content") or (a.get("action_args") or {}).get("comment_content") or ""
            if c:
                dump.append(f"- r{a.get('round','?')} **{a.get('agent_name','?')}** [{a['action_type']}]: {c}")
    (out / "all_agent_texts.md").write_text("\n".join(dump), encoding="utf-8")

    # ── aggregates ──
    agent_counts = defaultdict(Counter)
    for a in actions:
        agent_counts[a.get("agent_name", "?")][a["action_type"]] += 1

    # duplicate-content artifact check
    texts = Counter(
        ((a.get("action_args") or {}).get("content") or "")
        for a in actions if (a.get("action_args") or {}).get("content")
    )
    duplicates = {t[:80]: n for t, n in texts.items() if n > 2}

    # ── sqlite: users + comment edges ──
    users, edges = {}, []
    for db, pf in (("reddit_simulation.db", "reddit"), ("twitter_simulation.db", "twitter")):
        f = sim / db
        if not f.exists():
            continue
        con = sqlite3.connect(f)
        try:
            for r in con.execute("select user_id, coalesce(user_name, name) from user"):
                users[(pf, r[0])] = r[1]
            for cu, pu in con.execute(
                "select c.user_id, p.user_id from comment c join post p on c.post_id = p.post_id"
            ):
                edges.append((users.get((pf, cu)), users.get((pf, pu))))
        except sqlite3.Error as e:
            print(f"{db}: {e}")

    def norm(x):
        return re.sub(r"[^a-z]", "", re.sub(r"_\d+$", "", str(x).lower()))

    canon = {norm(a): a for a in agent_counts}

    def to_agent(dbname):
        n = norm(dbname)
        if n in canon:
            return canon[n]
        for k, v in canon.items():
            if n.startswith(k) or k.startswith(n):
                return v
        return str(dbname)

    edge_w = Counter((to_agent(s), to_agent(t)) for s, t in edges if s and t)

    # ── charts ──
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx

    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Segoe UI", "DejaVu Sans"],
        "text.color": INK, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
        "axes.edgecolor": BASE, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False,
    })
    order = [t for t, _ in by_type.most_common()]
    tcolor = {t: SERIES[i % 8] for i, t in enumerate(order)}

    fig, axes = plt.subplots(2, 1, figsize=(9, 5.4), sharex=True)
    for ax, pf in zip(axes, ("twitter", "reddit")):
        rc = defaultdict(Counter)
        for a in actions:
            if a["platform"] == pf:
                rc[a.get("round", -1)][a["action_type"]] += 1
        xs = sorted(rc)
        bottom = [0] * len(xs)
        for t in order:
            vals = [rc[x][t] for x in xs]
            ax.bar([str(x) for x in xs], vals, bottom=bottom, color=tcolor[t], width=0.62,
                   label=t, edgecolor=SURFACE, linewidth=2)
            bottom = [b + v for b, v in zip(bottom, vals)]
        ax.set_title(pf, loc="left", fontsize=11, color=INK)
        ax.set_ylabel("actions")
        ax.grid(axis="x", visible=False)
    axes[0].legend(frameon=False, fontsize=9, ncol=max(1, len(order)), loc="upper right")
    axes[1].set_xlabel("simulation round")
    fig.suptitle("Agent activity per round, by action type", x=0.01, ha="left",
                 fontsize=13, color=INK, weight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out / "activity_by_round.png", dpi=180)
    plt.close(fig)

    agents_sorted = sorted(agent_counts, key=lambda a: sum(agent_counts[a].values()))
    fig, ax = plt.subplots(figsize=(9, 0.42 * len(agents_sorted) + 1.6))
    left = [0] * len(agents_sorted)
    for t in order:
        vals = [agent_counts[a][t] for a in agents_sorted]
        ax.barh(agents_sorted, vals, left=left, color=tcolor[t], height=0.58,
                label=t, edgecolor=SURFACE, linewidth=2)
        left = [l + v for l, v in zip(left, vals)]
    for i in range(len(agents_sorted)):
        ax.text(left[i] + 0.25, i, str(left[i]), va="center", fontsize=9, color=INK2)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("actions (both platforms)")
    ax.set_title("Who did the talking — actions per agent", loc="left",
                 fontsize=13, color=INK, weight="bold")
    fig.tight_layout()
    fig.savefig(out / "agent_activity.png", dpi=180)
    plt.close(fig)

    G = nx.DiGraph()
    for a, c in agent_counts.items():
        G.add_node(a, size=sum(c.values()))
    for (s, t), n in edge_w.items():
        if s != t:
            G.add_edge(s, t, weight=n)
    fig, ax = plt.subplots(figsize=(9.5, 7.5))
    pos = nx.spring_layout(G, seed=7, k=1.4)
    nx.draw_networkx_edges(G, pos, ax=ax, edge_color=MUTED, arrows=True, arrowsize=13, alpha=0.75,
                           width=[1 + 1.2 * G[u][v]["weight"] for u, v in G.edges],
                           connectionstyle="arc3,rad=0.08")
    nx.draw_networkx_nodes(G, pos, ax=ax, node_size=[180 + 60 * G.nodes[n]["size"] for n in G.nodes],
                           node_color=SERIES[0], edgecolors=SURFACE, linewidths=2)
    nx.draw_networkx_labels(G, pos, ax=ax, font_size=9, font_color=INK)
    ax.set_title("Agent interaction graph — who replied to whom (node size = activity)",
                 loc="left", fontsize=13, color=INK, weight="bold")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(out / "interaction_graph.png", dpi=180)
    plt.close(fig)

    summary = {
        "sim_id": args.sim_id,
        "actions_total": len(actions),
        "actions_by_type": dict(by_type),
        "agents": {a: sum(c.values()) for a, c in agent_counts.items()},
        "comment_edges": [{"from": s, "to": t, "n": n} for (s, t), n in edge_w.items()],
        "duplicate_content_artifacts": duplicates,
    }
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"analysis written to {out}")


if __name__ == "__main__":
    main()
