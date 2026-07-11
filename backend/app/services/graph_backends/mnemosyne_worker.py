#!/usr/bin/env python
"""
Mnemosyne subprocess worker (WS-5 A3/A4).

Runs UNDER MNEMOSYNE'S OWN VENV (so Delphi doesn't need mnemosyne/loomstate
installed in its venv — loomstate is an absolute file:// editable dep that won't
resolve elsewhere). Delphi's MnemosyneBackend shells out to:

    <mnemosyne_venv>/bin/python mnemosyne_worker.py

passing one JSON command on stdin and reading one JSON result on stdout.

Why a subprocess per call: Mnemosyne freezes its data dir from MNEMOSYNE_DATA at
import time, so a single long-lived process can only ever bind one project's
graph. A fresh process per op sets MNEMOSYNE_DATA first, giving clean per-project
isolation.

Commands (stdin JSON): {"op": "...", ...}
  ingest      {data_dir, chunks[], ontology?, extractor, llm_env{}}   -> {stats}
  graph_data  {data_dir}                                              -> {nodes, edges}
  entities    {data_dir, types?}                                      -> {entities:[...]}
  search      {data_dir, query, limit, llm_env{}}                     -> {facts:[...]}
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# This script lives next to a local `mnemosyne.py` (Delphi's backend adapter),
# which would shadow the real `mnemosyne` package. Drop our own dir from the
# import path so `import mnemosyne` resolves to the installed package.
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or '.') != _here]


def _setup_env(cmd):
    """Set env BEFORE importing mnemosyne (data dir + LLM are import-time)."""
    os.environ['MNEMOSYNE_DATA'] = cmd['data_dir']
    os.environ.setdefault('LOOMSTATE_EMBEDDER', 'hash')  # no model download by default
    extractor = cmd.get('extractor', 'auto')
    os.environ['MNEMOSYNE_EXTRACTOR'] = extractor
    # BYO-LLM: pass the user's OpenAI-compatible key through Mnemosyne's gateway.
    for k, v in (cmd.get('llm_env') or {}).items():
        if v:
            os.environ[k] = v


def _build_ontology_prompt(ontology):
    """A4: rewrite EXTRACT_PROMPT to steer extraction toward Delphi's typed schema."""
    ent_types = [e.get('name') for e in (ontology.get('entity_types') or []) if e.get('name')]
    edge_types = [e.get('name') for e in (ontology.get('edge_types') or []) if e.get('name')]
    if not ent_types:
        return None
    types_str = ' | '.join(ent_types)
    edges_str = (', '.join(edge_types)) if edge_types else 'a short lowercase verb phrase'
    # Keep {passage} and the doubled-brace JSON example intact.
    return (
        "Extract the knowledge graph from this passage.\n\n"
        "Rules:\n"
        "- entities: real things only. Use the fullest name the passage gives. "
        f"Assign each a type from this project's schema: {types_str}. "
        "If none fits, use the closest match.\n"
        f"- relations: (subject, predicate, object) where predicate is one of: {edges_str} "
        "(or a short lowercase verb phrase if none fits).\n"
        "- each relation's \"quote\" must be the EXACT contiguous sentence from the passage, verbatim.\n"
        "- \"time\": year/ISO date if stated, else null.\n"
        "- extract only what the passage states; no outside knowledge.\n\n"
        "Respond with ONLY JSON:\n"
        '{{"entities": [{{"name": "...", "type": "..."}}], '
        '"relations": [{{"subj": "...", "pred": "...", "obj": "...", "quote": "...", "time": null}}]}}\n\n'
        "Passage:\n{passage}"
    )


def op_ingest(cmd):
    _setup_env(cmd)
    ontology = cmd.get('ontology') or {}
    # A4: inject custom ontology into the extraction prompt (before importing pipeline).
    import mnemosyne.extract as extract_mod
    prompt = _build_ontology_prompt(ontology)
    if prompt:
        extract_mod.EXTRACT_PROMPT = prompt

    from mnemosyne import config
    from mnemosyne.pipeline import ingest

    data_dir = Path(cmd['data_dir'])
    docs_dir = data_dir / '_src'
    docs_dir.mkdir(parents=True, exist_ok=True)
    # One file holding all chunks (double newline separated) — Mnemosyne re-chunks.
    doc = docs_dir / 'seed.md'
    doc.write_text('\n\n'.join(cmd.get('chunks') or []), encoding='utf-8')

    settings = config.Settings(extractor=os.environ.get('MNEMOSYNE_EXTRACTOR', 'auto'))
    stats = ingest(doc, settings, force_extract=True)
    return {'stats': stats}


def _entity_type(row):
    t = row['type'] if 'type' in row.keys() else None
    return t or 'Entity'


def op_graph_data(cmd):
    _setup_env(cmd)
    from mnemosyne.store import GraphStore
    st = GraphStore()
    ents = st.entities()
    rels = st.all_relations()
    name_by_id = {e['id']: e['name'] for e in ents}

    nodes = [{
        'uuid': e['id'],
        'name': e['name'],
        'labels': [_entity_type(e)],
        'summary': '',
        'attributes': {'mention_count': e['mention_count'] if 'mention_count' in e.keys() else 0},
        'created_at': None,
    } for e in ents]

    edges = []
    for r in rels:
        edges.append({
            'uuid': str(r['id']),
            'source_node_uuid': r['subj'],
            'target_node_uuid': r['obj'],
            'source_name': name_by_id.get(r['subj'], ''),
            'target_name': name_by_id.get(r['obj'], ''),
            'name': r['pred'],
            'fact': r['quote'] if 'quote' in r.keys() else '',
            'valid_at': r['t_start'] if 't_start' in r.keys() else None,
            'invalid_at': r['t_end'] if 't_end' in r.keys() else None,
            'created_at': None,
        })
    return {'nodes': nodes, 'edges': edges}


def op_entities(cmd):
    _setup_env(cmd)
    from mnemosyne.store import GraphStore
    st = GraphStore()
    ents = st.entities()
    rels = st.all_relations()
    name_by_id = {e['id']: e['name'] for e in ents}
    want = set(t.lower() for t in (cmd.get('types') or []))

    # index edges per entity
    out_edges, in_edges = {}, {}
    for r in rels:
        out_edges.setdefault(r['subj'], []).append(r)
        in_edges.setdefault(r['obj'], []).append(r)

    result = []
    for e in ents:
        etype = _entity_type(e)
        if want and etype.lower() not in want:
            # keep anyway if Mnemosyne's coarse types don't overlap Delphi's schema
            # (avoids empty rosters); only filter when there IS overlap.
            pass
        related_edges, related_nodes, summary_parts = [], [], []
        for r in out_edges.get(e['id'], []):
            tgt = name_by_id.get(r['obj'], '')
            related_edges.append({'name': r['pred'], 'direction': 'out', 'target': tgt})
            related_nodes.append({'name': tgt})
            summary_parts.append(f"{e['name']} {r['pred']} {tgt}")
        for r in in_edges.get(e['id'], []):
            src = name_by_id.get(r['subj'], '')
            related_edges.append({'name': r['pred'], 'direction': 'in', 'target': src})
            related_nodes.append({'name': src})
            summary_parts.append(f"{src} {r['pred']} {e['name']}")
        result.append({
            'uuid': e['id'],
            'name': e['name'],
            'labels': [etype],
            'summary': '; '.join(summary_parts[:12]),
            'attributes': {'mention_count': e['mention_count'] if 'mention_count' in e.keys() else 0},
            'related_edges': related_edges,
            'related_nodes': related_nodes,
        })

    # If a type filter was requested AND some entities match it, honor it;
    # otherwise return all (coarse-type fallback).
    if want:
        matching = [r for r in result if r['labels'][0].lower() in want]
        if matching:
            result = matching
    return {'entities': result}


def op_search(cmd):
    _setup_env(cmd)
    from mnemosyne import config
    from mnemosyne.retrieve import GraphRetriever
    settings = config.Settings()
    retr = GraphRetriever(settings=settings)
    res = retr.retrieve(cmd['query'])
    facts = []
    for f in (res.get('facts') or [])[: cmd.get('limit', 10)]:
        facts.append({
            'fact': f.get('text') or f"{f.get('subj')} {f.get('pred')} {f.get('obj')}",
            'quote': f.get('quote', ''),
            'valid_at': f.get('t_start'),
            'invalid_at': f.get('t_end'),
            'source': f.get('doc_title', ''),
            'score': f.get('score', 0),
        })
    return {'facts': facts}


OPS = {
    'ingest': op_ingest,
    'graph_data': op_graph_data,
    'entities': op_entities,
    'search': op_search,
}


def main():
    # Mnemosyne/loomstate print progress to stdout; keep our JSON the ONLY thing
    # on real stdout by routing their prints to stderr during the op.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        cmd = json.loads(sys.stdin.read())
        op = OPS.get(cmd.get('op'))
        if not op:
            raise ValueError(f"unknown op: {cmd.get('op')}")
        result = op(cmd)
        payload = {'ok': True, 'result': result}
    except Exception as e:
        import traceback
        payload = {'ok': False, 'error': str(e), 'traceback': traceback.format_exc()}
    finally:
        sys.stdout = real_stdout
    sys.stdout.write(json.dumps(payload))


if __name__ == '__main__':
    main()
