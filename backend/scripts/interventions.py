"""
Mid-run intervention queue (script side).

The Flask backend queues interventions ("what if X happens at round N?")
as JSON files under ``<sim_dir>/interventions_pending/<platform>/``. Each
platform's round loop drains its own queue at the start of a round and
injects the event as a real post into the environment, so agents react to
it exactly like any other content. An ``event_type: "intervention"``
marker line is appended to the platform's actions.jsonl so timelines and
the opinion tracker can annotate the moment.

One file per (intervention, platform): the backend fans out a single
intervention to every target platform, which keeps this concurrency-safe
without locking — each asyncio platform loop only ever touches its own
directory.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

PENDING_DIR = "interventions_pending"
APPLIED_LOG = "interventions_applied.jsonl"


def drain_pending_interventions(simulation_dir: str, platform: str) -> List[Dict[str, Any]]:
    """
    Pop all pending interventions for a platform (oldest first).

    Files are removed as they are claimed; a file that fails to parse is
    moved aside (suffix ``.bad``) instead of blocking the queue forever.
    """
    pending_dir = os.path.join(simulation_dir, PENDING_DIR, platform)
    if not os.path.isdir(pending_dir):
        return []

    entries = []
    for filename in sorted(os.listdir(pending_dir)):
        if not filename.endswith('.json'):
            continue
        filepath = os.path.join(pending_dir, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            os.remove(filepath)
            entries.append(data)
        except (json.JSONDecodeError, OSError):
            try:
                os.replace(filepath, filepath + '.bad')
            except OSError:
                pass
    return entries


def log_intervention_marker(
    log_path: str,
    round_num: int,
    intervention: Dict[str, Any],
) -> None:
    """Append an intervention marker line to a platform's actions.jsonl."""
    entry = {
        "round": round_num,
        "timestamp": datetime.now().isoformat(),
        "event_type": "intervention",
        "intervention_id": intervention.get("intervention_id"),
        "text": intervention.get("text", ""),
        "agent_id": intervention.get("agent_id"),
    }
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def record_applied(
    simulation_dir: str,
    platform: str,
    round_num: int,
    intervention: Dict[str, Any],
    success: bool,
    error: Optional[str] = None,
) -> None:
    """Append to the applied-interventions journal the backend reads."""
    entry = {
        "intervention_id": intervention.get("intervention_id"),
        "platform": platform,
        "round": round_num,
        "text": intervention.get("text", ""),
        "agent_id": intervention.get("agent_id"),
        "success": success,
        "error": error,
        "applied_at": datetime.now().isoformat(),
    }
    path = os.path.join(simulation_dir, APPLIED_LOG)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


async def apply_interventions(
    env,
    simulation_dir: str,
    platform: str,
    round_num: int,
    agent_names: Dict[int, str],
    action_logger=None,
) -> int:
    """
    Drain and inject this platform's pending interventions.

    Each intervention becomes a ManualAction CREATE_POST by the chosen
    agent (default: agent 0), stepped through the environment immediately
    so it lands before this round's LLM actions.

    Returns the number of interventions applied.
    """
    pending = drain_pending_interventions(simulation_dir, platform)
    if not pending:
        return 0

    from oasis import ActionType, ManualAction  # imported lazily like the scripts do

    applied = 0
    for intervention in pending:
        agent_id = int(intervention.get("agent_id") or 0)
        text = intervention.get("text", "")
        if not text:
            record_applied(simulation_dir, platform, round_num, intervention,
                           success=False, error="empty intervention text")
            continue
        try:
            agent = env.agent_graph.get_agent(agent_id)
            await env.step({agent: ManualAction(
                action_type=ActionType.CREATE_POST,
                action_args={"content": text},
            )})
            if action_logger:
                log_intervention_marker(action_logger.log_path, round_num, intervention)
                action_logger.log_action(
                    round_num=round_num,
                    agent_id=agent_id,
                    agent_name=agent_names.get(agent_id, f"Agent_{agent_id}"),
                    action_type="CREATE_POST",
                    action_args={"content": text, "intervention": True},
                )
            record_applied(simulation_dir, platform, round_num, intervention, success=True)
            applied += 1
        except Exception as exc:  # noqa: BLE001 - must not kill the sim loop
            record_applied(simulation_dir, platform, round_num, intervention,
                           success=False, error=str(exc))
    return applied
