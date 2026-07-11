"""
Live simulation aggregation (WS-5 Part B).

Turns the raw `actions.jsonl` a running simulation streams into two things the
frontend Live view renders:

  1. an **agent interaction graph** — who engages whom (likes / reposts / quotes /
     comments / follows), resolved via post_id → author;
  2. a **stance-convergence histogram** — per round, how many actions come from
     each stance group (supportive / opposing / neutral / observer), so you watch
     which side of the conversation dominates over time.

All data already exists on disk (the sim writes it); this only reads + aggregates,
workspace-scoped. Safe to call repeatedly while the sim runs (incremental-friendly:
returns everything up to the latest written line).
"""

import json
import os
from typing import Any, Dict, List

# Actions that target another agent's content → an interaction edge.
# Maps action_type → the action_args key that identifies the target's content/agent.
_TARGET_KEYS = {
    'LIKE_POST': 'post_id',
    'DISLIKE_POST': 'post_id',
    'REPOST': 'post_id',
    'QUOTE_POST': 'post_id',
    'CREATE_COMMENT': 'post_id',
    'LIKE_COMMENT': 'comment_id',
    'DISLIKE_COMMENT': 'comment_id',
    'FOLLOW': 'followee_id',
    'MUTE': 'mutee_id',
}

_STANCES = ('supportive', 'opposing', 'neutral', 'observer')


def _load_roster(sim_dir: str) -> Dict[int, Dict[str, Any]]:
    """agent_id -> {name, entity_type, stance} from simulation_config.json."""
    roster: Dict[int, Dict[str, Any]] = {}
    cfg_path = os.path.join(sim_dir, 'simulation_config.json')
    if not os.path.exists(cfg_path):
        return roster
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        for ac in cfg.get('agent_configs', []) or []:
            try:
                aid = int(ac.get('agent_id'))
            except (TypeError, ValueError):
                continue
            roster[aid] = {
                'name': ac.get('entity_name') or f'agent_{aid}',
                'entity_type': ac.get('entity_type') or 'Entity',
                'stance': (ac.get('stance') or 'neutral').lower(),
            }
    except Exception:
        pass
    return roster


def _iter_action_lines(sim_dir: str):
    """Yield parsed JSON objects from both platforms' actions.jsonl, in file order."""
    for platform in ('twitter', 'reddit'):
        path = os.path.join(sim_dir, platform, 'actions.jsonl')
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        obj.setdefault('platform', platform)
                        yield obj
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue


def aggregate_live(sim_dir: str) -> Dict[str, Any]:
    """Build the interaction graph + per-round stance histogram from disk."""
    roster = _load_roster(sim_dir)

    # post_id -> author agent_id, learned from CREATE_POST as we stream.
    post_author: Dict[Any, int] = {}
    # (source, target, type) -> weight  (aggregated interaction edges)
    edge_weight: Dict[tuple, int] = {}
    action_count: Dict[int, int] = {}
    # round -> stance -> count, and round -> action_type -> count
    rounds: Dict[int, Dict[str, Any]] = {}
    max_round = 0

    def _round_bucket(r):
        if r not in rounds:
            rounds[r] = {'round': r, 'by_stance': {s: 0 for s in _STANCES},
                         'by_action': {}, 'active_agents': set()}
        return rounds[r]

    for obj in _iter_action_lines(sim_dir):
        atype = obj.get('action_type')
        if not atype:
            continue  # event line (round_start/simulation_end/…), skip
        aid = obj.get('agent_id')
        try:
            aid = int(aid)
        except (TypeError, ValueError):
            continue
        rnum = int(obj.get('round_num', obj.get('round', 0)) or 0)
        max_round = max(max_round, rnum)
        args = obj.get('action_args') or {}

        # roster fallback for agents not in config
        if aid not in roster:
            roster[aid] = {'name': obj.get('agent_name') or f'agent_{aid}',
                           'entity_type': 'Entity', 'stance': 'neutral'}

        action_count[aid] = action_count.get(aid, 0) + 1
        bucket = _round_bucket(rnum)
        bucket['active_agents'].add(aid)
        bucket['by_action'][atype] = bucket['by_action'].get(atype, 0) + 1
        bucket['by_stance'][roster[aid]['stance']] = \
            bucket['by_stance'].get(roster[aid]['stance'], 0) + 1

        # learn post authorship
        if atype == 'CREATE_POST':
            pid = args.get('post_id')
            if pid is not None:
                post_author[pid] = aid

        # resolve interaction edge
        tkey = _TARGET_KEYS.get(atype)
        if tkey:
            tval = args.get(tkey)
            target = None
            if atype in ('FOLLOW', 'MUTE'):
                try:
                    target = int(tval)
                except (TypeError, ValueError):
                    target = None
            else:
                target = post_author.get(tval)
            if target is not None and target != aid:
                key = (aid, target, atype)
                edge_weight[key] = edge_weight.get(key, 0) + 1

    nodes = [
        {
            'id': aid,
            'name': info['name'],
            'entity_type': info['entity_type'],
            'stance': info['stance'],
            'action_count': action_count.get(aid, 0),
        }
        for aid, info in sorted(roster.items())
    ]
    edges = [
        {'source': s, 'target': t, 'type': a, 'weight': w}
        for (s, t, a), w in edge_weight.items()
    ]
    round_list = [
        {
            'round': r['round'],
            'by_stance': r['by_stance'],
            'by_action': r['by_action'],
            'active_agents': len(r['active_agents']),
        }
        for r in sorted(rounds.values(), key=lambda x: x['round'])
    ]

    return {
        'nodes': nodes,
        'edges': edges,
        'rounds': round_list,
        'max_round': max_round,
        'stances': list(_STANCES),
        'total_actions': sum(action_count.values()),
    }
