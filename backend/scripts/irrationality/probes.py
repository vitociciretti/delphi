"""
Bias-probe measurement harness.

Runs classic cognitive-bias probes against a sample of agents via OASIS
INTERVIEW actions (which are excluded from agents' autonomous action space
and are NOT written to agent memory -- perform_interview defaults to
interview_record=False -- so probing does not contaminate the simulation).

Probe types:
- anchoring:   agents split into high-anchor / low-anchor groups estimate a
               quantity; the group-mean gap is the anchoring effect.
- conformity:  agreement rating with a claim, with vs without injected
               social-proof ("the vast majority of users here believe...").
- framing:     the same policy choice framed as gains vs losses; the flip in
               option shares is the framing effect.
- rumor_chain: retell a detailed account in two sentences; responses are
               stored raw for LLM-side distortion scoring in the report.

Responses land in the OASIS SQLite `trace` table (action='interview') and are
read back the same way the IPC interview handler does. All waves are
persisted to bias_probe_results.json in the simulation directory.
"""

import json
import os
import random
import re
import sqlite3
from typing import Any, Dict, List, Optional

from oasis import ActionType, ManualAction

RESULTS_FILENAME = "bias_probe_results.json"


def _probe_definitions(subject: str) -> Dict[str, Dict[str, Any]]:
    """Probe library, parameterized by the simulated event subject."""
    subject = subject or "the event being discussed"
    return {
        "anchoring": {
            "variants": {
                "high": (
                    "A researcher privately asks you: do you think more or fewer "
                    "than 85% of people in your community agree with the dominant "
                    "narrative about {subject}? What is your best estimate of the "
                    "true percentage? Answer with a single number between 0 and 100, "
                    "then one short sentence of reasoning."
                ),
                "low": (
                    "A researcher privately asks you: do you think more or fewer "
                    "than 15% of people in your community agree with the dominant "
                    "narrative about {subject}? What is your best estimate of the "
                    "true percentage? Answer with a single number between 0 and 100, "
                    "then one short sentence of reasoning."
                ),
            },
            "parser": "number",
        },
        "conformity": {
            "variants": {
                "social": (
                    "A researcher privately asks you: the vast majority of users on "
                    "this platform believe that the initial reports about {subject} "
                    "are accurate and trustworthy. How much do you personally agree "
                    "that the initial reports are accurate? Answer with a single "
                    "number between 0 (completely disagree) and 100 (completely "
                    "agree), then one short sentence."
                ),
                "control": (
                    "A researcher privately asks you: how much do you personally "
                    "agree that the initial reports about {subject} are accurate "
                    "and trustworthy? Answer with a single number between 0 "
                    "(completely disagree) and 100 (completely agree), then one "
                    "short sentence."
                ),
            },
            "parser": "number",
        },
        "framing": {
            "variants": {
                "gain": (
                    "A researcher privately asks you to choose between two "
                    "responses to {subject}: Option A guarantees that 200 of 600 "
                    "affected people are helped. Option B gives a 1/3 chance that "
                    "all 600 are helped and a 2/3 chance that none are helped. "
                    "Answer 'A' or 'B' first, then one short sentence."
                ),
                "loss": (
                    "A researcher privately asks you to choose between two "
                    "responses to {subject}: Option A guarantees that 400 of 600 "
                    "affected people are not helped. Option B gives a 1/3 chance "
                    "that nobody is left without help and a 2/3 chance that all "
                    "600 are left without help. Answer 'A' or 'B' first, then one "
                    "short sentence."
                ),
            },
            "parser": "choice",
        },
        "rumor_chain": {
            "variants": {
                "retell": (
                    "A researcher privately asks you: based only on what you have "
                    "seen on the platform, retell in exactly two sentences what "
                    "happened regarding {subject}, as you would explain it to a "
                    "friend who has heard nothing."
                ),
            },
            "parser": "raw",
        },
    }


class BiasProbeHarness:
    def __init__(
        self,
        simulation_dir: str,
        db_path: str,
        subject: str,
        sample_size: int = 8,
        every_n_rounds: int = 10,
        probe_types: Optional[List[str]] = None,
        rng: Optional[random.Random] = None,
    ):
        self.simulation_dir = simulation_dir
        self.db_path = db_path
        self.subject = subject
        self.sample_size = max(2, int(sample_size))
        self.every_n_rounds = max(1, int(every_n_rounds))
        self.definitions = _probe_definitions(subject)
        self.probe_types = [
            p for p in (probe_types or list(self.definitions.keys()))
            if p in self.definitions
        ]
        self.rng = rng or random.Random()
        self.results: Dict[str, Any] = {"subject": subject, "waves": [], "summary": {}}
        self.results_path = os.path.join(simulation_dir, RESULTS_FILENAME)

    # ------------------------------------------------------------------

    def should_run(self, round_num: int) -> bool:
        return round_num > 0 and round_num % self.every_n_rounds == 0

    async def run_wave(self, env, agent_graph, wave_label: str) -> Dict[str, Any]:
        """Run every enabled probe against an independent agent sample.

        One probe per agent per wave (an agent answering two probes back to
        back would cross-contaminate: e.g. an anchoring number priming the
        conformity rating).
        """
        all_ids = [aid for aid, _ in agent_graph.get_agents()]
        wave: Dict[str, Any] = {"wave": wave_label, "probes": []}

        available = list(all_ids)
        self.rng.shuffle(available)

        for probe_type in self.probe_types:
            definition = self.definitions[probe_type]
            variants = list(definition["variants"].items())
            take = min(self.sample_size, len(available))
            if take < 2:
                break
            sampled, available = available[:take], available[take:]

            actions = {}
            assignments = []
            for idx, agent_id in enumerate(sampled):
                variant_name, template = variants[idx % len(variants)]
                prompt = template.format(subject=self.subject)
                try:
                    agent = agent_graph.get_agent(agent_id)
                except Exception:
                    continue
                actions[agent] = ManualAction(
                    action_type=ActionType.INTERVIEW,
                    action_args={"prompt": prompt},
                )
                assignments.append((agent_id, variant_name, prompt))

            if not actions:
                continue
            try:
                await env.step(actions)
            except Exception as e:
                print(f"[irrationality] probe wave '{probe_type}' failed: {e}")
                continue

            for agent_id, variant_name, prompt in assignments:
                response = self._read_interview_response(agent_id)
                wave["probes"].append({
                    "probe": probe_type,
                    "variant": variant_name,
                    "agent_id": agent_id,
                    "response": response,
                    "parsed": _parse(definition["parser"], response),
                })

        self.results["waves"].append(wave)
        self._save()
        answered = sum(1 for p in wave["probes"] if p["response"])
        print(f"[irrationality] bias-probe wave '{wave_label}': "
              f"{answered}/{len(wave['probes'])} responses")
        return wave

    # ------------------------------------------------------------------

    def finalize(self) -> Dict[str, Any]:
        """Compute effect sizes across all waves and persist."""
        summary: Dict[str, Any] = {}

        anchoring = self._collect("anchoring")
        high = [p["parsed"] for p in anchoring
                if p["variant"] == "high" and p["parsed"] is not None]
        low = [p["parsed"] for p in anchoring
               if p["variant"] == "low" and p["parsed"] is not None]
        if high and low:
            summary["anchoring_effect"] = round(_mean(high) - _mean(low), 2)
            summary["anchoring_n"] = len(high) + len(low)

        conformity = self._collect("conformity")
        social = [p["parsed"] for p in conformity
                  if p["variant"] == "social" and p["parsed"] is not None]
        control = [p["parsed"] for p in conformity
                   if p["variant"] == "control" and p["parsed"] is not None]
        if social and control:
            summary["conformity_shift"] = round(_mean(social) - _mean(control), 2)
            summary["conformity_n"] = len(social) + len(control)

        framing = self._collect("framing")
        gain_b = [p["parsed"] == "B" for p in framing
                  if p["variant"] == "gain" and p["parsed"]]
        loss_b = [p["parsed"] == "B" for p in framing
                  if p["variant"] == "loss" and p["parsed"]]
        if gain_b and loss_b:
            # Classic risky-shift under loss framing: share choosing the
            # gamble (B) in the loss frame minus the gain frame.
            summary["framing_effect"] = round(
                _mean([1.0 if x else 0.0 for x in loss_b])
                - _mean([1.0 if x else 0.0 for x in gain_b]), 3)
            summary["framing_n"] = len(gain_b) + len(loss_b)

        summary["rumor_retellings"] = len(self._collect("rumor_chain"))

        self.results["summary"] = summary
        self._save()
        return summary

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _collect(self, probe_type: str) -> List[Dict[str, Any]]:
        return [
            p for wave in self.results["waves"]
            for p in wave["probes"] if p["probe"] == probe_type
        ]

    def _read_interview_response(self, agent_id: int) -> Optional[str]:
        """Latest interview trace row for this agent (same pattern as the
        IPC interview handler)."""
        if not os.path.exists(self.db_path):
            return None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT info FROM trace
                WHERE action = ? AND user_id = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (ActionType.INTERVIEW.value, agent_id),
            )
            row = cursor.fetchone()
            conn.close()
            if not row or not row[0]:
                return None
            try:
                info = json.loads(row[0])
                response = info.get("response")
                return str(response) if response is not None else None
            except (json.JSONDecodeError, AttributeError):
                return str(row[0])
        except Exception:
            return None

    def _save(self) -> None:
        try:
            with open(self.results_path, "w", encoding="utf-8") as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"[irrationality] failed to save probe results: {e}")


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse(parser: str, response: Optional[str]):
    if not response:
        return None
    if parser == "number":
        match = re.search(r"\b(\d{1,3}(?:\.\d+)?)\b", response)
        if match:
            value = float(match.group(1))
            return value if 0 <= value <= 100 else None
        return None
    if parser == "choice":
        match = re.search(r"\b([AB])\b", response.upper())
        return match.group(1) if match else None
    return response  # raw


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0
