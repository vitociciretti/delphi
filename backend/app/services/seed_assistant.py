"""
Seed assistant service.

When a user has no document to seed a simulation with, this service runs a
free-form conversation that helps them articulate the "state of the world"
they want to simulate, and — on request — drafts a structured seed document
they can review, edit and approve.

The assistant is grounded in the selected scenario preset (so a
``financial_market`` world is interviewed differently from a
``creative_narrative`` one) and in the natural-language prediction requirement
the user has already provided. The drafted document is deliberately shaped to
be rich in named entities and relationships so the downstream ontology /
persona / memory pipeline has real material to work with.
"""

from typing import Dict, List, Optional

from ..scenarios import get_registry
from ..utils.llm_client import LLMClient
from ..utils.locale import get_language_instruction


def _scenario_framing(scenario_id: Optional[str]) -> str:
    """Return the prompt framing + label for the selected scenario preset."""
    registry = get_registry()
    try:
        preset = registry.get(scenario_id) if scenario_id else registry.default()
    except Exception:
        preset = registry.default()
    framing = preset.prompt_framing or preset.description or ""
    return f"Scenario: {preset.name} ({preset.domain}).\n{framing}".strip()


def _base_system(scenario_id: Optional[str], requirement: str) -> str:
    framing = _scenario_framing(scenario_id)
    req = requirement.strip() if requirement else ""
    req_block = (
        f"The user's prediction requirement is:\n\"\"\"\n{req}\n\"\"\"\n"
        if req
        else "The user has not yet stated a specific prediction requirement.\n"
    )
    return (
        "You are a simulation seed designer. You help the user describe the "
        "starting 'state of the world' for a multi-agent simulation. "
        f"{framing}\n\n{req_block}"
    )


class SeedAssistant:
    """Conversational helper that interviews the user and drafts a seed doc."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    def chat(
        self,
        messages: List[Dict[str, str]],
        scenario_id: Optional[str] = None,
        requirement: str = "",
    ) -> str:
        """
        Continue the free-form conversation.

        Args:
            messages: prior turns [{role: 'user'|'assistant', content: str}, ...]
            scenario_id: selected scenario preset id (falls back to default)
            requirement: the user's natural-language prediction requirement

        Returns:
            The assistant's next reply.
        """
        system = (
            _base_system(scenario_id, requirement)
            + "\n\nInterview the user to draw out the key ingredients of the "
            "starting world: the main actors and entities (name them), their "
            "relationships and tensions, the initial conditions and relevant "
            "background, and the moment in time the simulation should start "
            "from. Ask focused questions, one small cluster at a time, and "
            "build on their answers. Keep replies concise and conversational. "
            "Do NOT write the full document yet — when the user is satisfied "
            "they will explicitly ask you to draft it.\n\n"
            + get_language_instruction()
        )
        full = [{"role": "system", "content": system}] + list(messages)
        return self.llm_client.chat(full, temperature=0.7, max_tokens=1024)

    def draft(
        self,
        messages: List[Dict[str, str]],
        scenario_id: Optional[str] = None,
        requirement: str = "",
    ) -> str:
        """
        Produce a structured "state of the world" seed document from the
        conversation so far. Returns markdown text the user can review/edit.
        """
        system = (
            _base_system(scenario_id, requirement)
            + "\n\nUsing the conversation so far, write the starting "
            "'state of the world' as a structured document. It must be "
            "self-contained and concrete — the reader has NOT seen the chat. "
            "Include, with real named entities wherever possible:\n"
            "- A short title and one-paragraph situation overview.\n"
            "- Key actors / entities, each with a name and a 1-2 sentence "
            "description of who/what they are and what they want.\n"
            "- The relationships and tensions between them.\n"
            "- Initial conditions and relevant background facts.\n"
            "- The point in time the simulation starts from.\n\n"
            "Write it so that entities and relationships can be extracted from "
            "the prose. Output ONLY the document in Markdown, no preamble.\n\n"
            + get_language_instruction()
        )
        convo = [{"role": "system", "content": system}] + list(messages)
        convo.append(
            {
                "role": "user",
                "content": "Draft the state-of-the-world document now.",
            }
        )
        return self.llm_client.chat(convo, temperature=0.6, max_tokens=4096)
