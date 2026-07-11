"""
Biased perception: a per-agent SocialEnvironment subclass.

Humans do not read their feed neutrally -- attention is selective
(confirmation bias) and negativity-weighted. OASIS builds each agent's
decision prompt from its own `SocialEnvironment` instance (agent.env), so we
swap in a subclass per agent after graph creation. No OASIS fork, and unlike
system-prompt rewrites this does not touch agent memory.

Two responsibilities:

1. get_posts_env(): re-rank (and under strong confirmation bias, drop) the
   refreshed posts before they are serialized into the prompt --
   stance-aligned and negative/charged content floats to the top.
2. to_text_prompt(): prepend the dynamic psych context (current emotion +
   numeric opinions, refreshed each round by the engine) and, in System-1
   mode, truncate the feed and append a gut-reaction instruction.
"""

import json
from typing import Any, Callable, Dict, List, Optional

from oasis.social_agent.agent_environment import SocialEnvironment

from .affect import score_emotional_charge

S1_INSTRUCTION = (
    "You are reacting in the moment: respond on gut instinct, based on your "
    "immediate emotional reaction to what you just saw. Do not weigh pros and "
    "cons, do not verify anything -- act now, the way you actually feel."
)
S1_MAX_POSTS = 3


class PsychSocialEnvironment(SocialEnvironment):
    """Drop-in replacement for an agent's SocialEnvironment."""

    def __init__(
        self,
        action,
        agent_id: int,
        psych,  # PsychProfile
        intensity: float = 1.0,
        biased_perception: bool = True,
        opinion_lookup: Optional[Callable[[int], Optional[float]]] = None,
    ):
        super().__init__(action)
        self.agent_id = agent_id
        self.psych = psych
        self.intensity = float(intensity)
        self.biased_perception = biased_perception
        # Maps author user_id -> main-topic opinion in [-1, 1]; provided by
        # the engine so perception can measure stance alignment.
        self.opinion_lookup = opinion_lookup or (lambda _uid: None)
        # Refreshed by the engine every round (emotion + opinion text).
        self.dynamic_context: str = ""
        # Toggled per activation by the dual-process router.
        self.s1_mode: bool = False

    # ------------------------------------------------------------------
    # Biased feed
    # ------------------------------------------------------------------

    async def get_posts_env(self) -> str:
        posts = await self.action.refresh()
        if not posts.get("success"):
            return "After refreshing, there are no existing posts."

        post_list: List[Dict[str, Any]] = list(posts.get("posts") or [])
        if self.biased_perception and post_list:
            post_list = self._rerank(post_list)
        if self.s1_mode and len(post_list) > S1_MAX_POSTS:
            post_list = post_list[:S1_MAX_POSTS]

        posts_env = json.dumps(post_list, indent=4, ensure_ascii=False)
        return self.posts_env_template.substitute(posts=posts_env)

    def _rerank(self, post_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Attention model: stance-aligned + charged/negative content first.

        Deterministic (no RNG) so a given feed + state always produces the
        same perception -- important for debugging and for tests.
        """
        conf_w = (0.6 if "confirmation_bias" in self.psych.biases
                  or "in_group_favoritism" in self.psych.biases else 0.2)
        conf_w *= self.intensity
        neg_w = self.psych.negativity_bias * self.intensity
        my_opinion = self.opinion_lookup(self.agent_id)

        scored = []
        for order, post in enumerate(post_list):
            content = str(post.get("content", ""))
            charge, valence = score_emotional_charge(
                content,
                num_likes=int(post.get("num_likes", 0) or 0),
                num_dislikes=int(post.get("num_dislikes", 0) or 0),
            )
            # Alignment with the author's stance, when both are known.
            alignment = 0.5
            author_id = post.get("user_id")
            if my_opinion is not None and author_id is not None:
                author_opinion = self.opinion_lookup(int(author_id))
                if author_opinion is not None:
                    distance = abs(my_opinion - author_opinion)  # 0..2
                    alignment = 1.0 - distance / 2.0
            negativity_pull = charge if valence < 0 else charge * 0.4
            score = conf_w * alignment + neg_w * negativity_pull
            scored.append((score, -order, alignment, post))

        # Stable sort: score desc, original order as tiebreak.
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

        # Selective exposure: under strong confirmation bias, sharply
        # stance-opposed posts fall out of attention entirely (keep at
        # least the top 60% of the feed so agents never see nothing).
        result = [item[3] for item in scored]
        if conf_w >= 0.5 and len(result) > 4:
            keep_min = max(4, int(len(result) * 0.6))
            filtered = [item[3] for item in scored if item[2] >= 0.25]
            if len(filtered) >= keep_min:
                result = filtered
        return result

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------

    async def to_text_prompt(
        self,
        include_posts: bool = True,
        include_followers: bool = True,
        include_follows: bool = True,
    ) -> str:
        base = await super().to_text_prompt(
            include_posts=include_posts,
            include_followers=include_followers,
            include_follows=include_follows,
        )
        parts = []
        if self.dynamic_context:
            parts.append(self.dynamic_context)
        parts.append(base)
        if self.s1_mode:
            parts.append(S1_INSTRUCTION)
        return "\n\n".join(parts)
