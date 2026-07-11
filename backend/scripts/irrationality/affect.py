"""
Dynamic affect (emotion) state per agent.

Three coupled scalars per agent, updated every round:

- arousal [0, 1]:  how agitated/activated the agent currently is. Raised by
                   exposure to emotionally charged content, decays toward a
                   trait-dependent baseline. Feeds back into activation
                   probability, System-1 routing, and prompt context.
- valence [-1, 1]: current mood direction. Pulled by the valence of consumed
                   content, anchored to the agent's sentiment_bias baseline.
- fatigue [0, 1]:  boredom / immunity. Grows with cumulative exposure to the
                   same storm of content, recovers slowly. Dampens both
                   arousal gains and activation -- without it, simulated
                   cascades never die, which is as wrong as cascades that
                   never start.

Content scoring is deliberately language-agnostic (Delphi sims run in many
locales): it combines punctuation/emoji/caps intensity with engagement
signals, plus a small EN/ZH outrage lexicon as a bonus term. It is a crude
proxy by design -- crude, deterministic, and testable beats subtle and
unmeasurable here.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

# Small, high-precision outrage/negative lexicons. Bonus signal only --
# the scorer must degrade gracefully to zero lexicon hits.
_NEGATIVE_WORDS_EN = {
    "outrage", "outrageous", "scandal", "disgusting", "shameful", "corrupt",
    "lies", "lie", "liar", "fraud", "betrayal", "furious", "angry", "disaster",
    "catastrophe", "crisis", "cover-up", "coverup", "exposed", "shocking",
    "unacceptable", "horrific", "terrifying", "threat", "dangerous", "victim",
}
_NEGATIVE_WORDS_ZH = {
    "愤怒", "可耻", "丑闻", "腐败", "谎言", "欺骗", "背叛", "灾难", "危机",
    "震惊", "恐怖", "威胁", "受害", "掩盖", "曝光", "无耻", "恶心", "可怕",
}
_POSITIVE_WORDS_EN = {
    "great", "wonderful", "amazing", "hope", "hopeful", "grateful", "proud",
    "love", "support", "beautiful", "inspiring", "celebrate", "victory",
}
_POSITIVE_WORDS_ZH = {
    "希望", "感谢", "感动", "自豪", "支持", "美好", "鼓舞", "庆祝", "胜利", "点赞",
}

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF☀-➿‼⁉️]"
)


def score_emotional_charge(
    text: str,
    num_likes: int = 0,
    num_dislikes: int = 0,
) -> Tuple[float, float]:
    """Score a piece of content.

    Returns (charge, valence):
    - charge in [0, 1]: emotional intensity, direction-free
    - valence in [-1, 1]: direction of the emotion

    Signals: exclamation/question density, ALL-CAPS runs, emoji density,
    lexicon hits, and dislike-vs-like ratio (a disliked post is usually a
    contested/negative one).
    """
    if not text:
        return 0.0, 0.0
    text = str(text)
    length = max(len(text), 40)

    exclaim = text.count("!") + text.count("！") + text.count("?!") * 2
    question = text.count("?") + text.count("？")
    caps_runs = len(re.findall(r"\b[A-Z]{3,}\b", text))
    emoji = len(_EMOJI_RE.findall(text))

    lower = text.lower()
    neg_hits = sum(1 for w in _NEGATIVE_WORDS_EN if w in lower)
    neg_hits += sum(1 for w in _NEGATIVE_WORDS_ZH if w in text)
    pos_hits = sum(1 for w in _POSITIVE_WORDS_EN if w in lower)
    pos_hits += sum(1 for w in _POSITIVE_WORDS_ZH if w in text)

    intensity = (
        1.6 * exclaim + 0.5 * question + 1.2 * caps_runs + 0.8 * emoji
        + 2.0 * neg_hits + 1.5 * pos_hits
    ) / (length / 40.0)
    charge = min(1.0, intensity / 6.0)

    # Engagement: contested content is charged content.
    total_votes = num_likes + num_dislikes
    if total_votes >= 3:
        charge = min(1.0, charge + 0.1 + 0.1 * min(1.0, total_votes / 30.0))

    # Valence: lexicon direction, tilted negative by dislike share.
    direction = 0.0
    if neg_hits or pos_hits:
        direction = (pos_hits - neg_hits) / (pos_hits + neg_hits)
    if total_votes >= 3:
        dislike_share = num_dislikes / total_votes
        direction -= 0.6 * max(0.0, dislike_share - 0.5)
    valence = max(-1.0, min(1.0, direction))

    return charge, valence


@dataclass
class AffectState:
    """Mutable emotion state of one agent."""

    arousal: float = 0.2
    valence: float = 0.0
    fatigue: float = 0.0
    # Trait anchors (set once from the PsychProfile).
    baseline_arousal: float = 0.2
    baseline_valence: float = 0.0

    @classmethod
    def for_profile(cls, sentiment_bias: float, impulsivity: float) -> "AffectState":
        baseline_arousal = 0.15 + 0.2 * impulsivity
        return cls(
            arousal=baseline_arousal,
            valence=sentiment_bias * 0.5,
            fatigue=0.0,
            baseline_arousal=baseline_arousal,
            baseline_valence=sentiment_bias * 0.5,
        )

    # ------------------------------------------------------------------
    # Dynamics
    # ------------------------------------------------------------------

    def decay(self, decay_rate: float = 0.15, fatigue_recovery: float = 0.02) -> None:
        """Per-round relaxation toward baseline; slow fatigue recovery."""
        self.arousal += (self.baseline_arousal - self.arousal) * decay_rate
        self.valence += (self.baseline_valence - self.valence) * decay_rate * 0.7
        self.fatigue = max(0.0, self.fatigue - fatigue_recovery)
        self._clamp()

    def absorb_exposure(
        self,
        charges: Iterable[Tuple[float, float]],
        negativity_bias: float = 0.5,
        boredom_rate: float = 0.05,
    ) -> None:
        """Update state from a round's consumed content.

        `charges` is an iterable of (charge, valence) pairs from
        score_emotional_charge. Fatigue attenuates arousal gains (immunity),
        and each charged exposure adds to fatigue (habituation).
        """
        charges = list(charges)
        if not charges:
            return
        gain = 0.0
        valence_pull = 0.0
        for charge, valence in charges:
            weight = charge
            # Negativity bias: negative content hits harder.
            if valence < 0:
                weight *= 1.0 + negativity_bias
            gain += weight
            valence_pull += valence * charge
        # Saturating arousal gain, attenuated by fatigue.
        effective_gain = (gain / (1.0 + 0.5 * gain)) * (1.0 - self.fatigue)
        self.arousal += 0.25 * effective_gain
        # Mood follows content, weakly.
        n = len(charges)
        self.valence += 0.15 * (valence_pull / n)
        # Habituation: exposure volume builds immunity.
        self.fatigue += boredom_rate * min(1.0, gain / 2.0)
        self._clamp()

    # ------------------------------------------------------------------
    # Couplings
    # ------------------------------------------------------------------

    def activation_multiplier(self, coupling: float = 0.6) -> float:
        """Multiplier on activity_level: agitated agents act more, bored
        agents disengage. Clipped to a sane range."""
        raw = 1.0 + coupling * (self.arousal - self.baseline_arousal) * 2.0
        raw *= 1.0 - 0.5 * self.fatigue
        return max(0.2, min(2.0, raw))

    def render_prompt_section(self) -> str:
        """One-line current-mood context for the env prompt."""
        arousal_phrase = (
            "calm" if self.arousal < 0.35
            else "worked up" if self.arousal < 0.65
            else "extremely agitated -- your reactions right now are fast, emotional, and unfiltered"
        )
        if self.valence <= -0.4:
            mood_phrase = "angry and pessimistic"
        elif self.valence < -0.1:
            mood_phrase = "irritated"
        elif self.valence < 0.25:
            mood_phrase = "neutral"
        else:
            mood_phrase = "upbeat"
        fatigue_phrase = ""
        if self.fatigue > 0.6:
            fatigue_phrase = (
                " You are tired of this whole topic; it takes a lot to make "
                "you engage with it again."
            )
        return (
            f"Current emotional state: you are {arousal_phrase}, and your mood "
            f"is {mood_phrase}.{fatigue_phrase}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "arousal": round(self.arousal, 4),
            "valence": round(self.valence, 4),
            "fatigue": round(self.fatigue, 4),
        }

    def _clamp(self) -> None:
        self.arousal = max(0.0, min(1.0, self.arousal))
        self.valence = max(-1.0, min(1.0, self.valence))
        self.fatigue = max(0.0, min(1.0, self.fatigue))
