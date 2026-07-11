"""Verbalized Sampling — inference-time prompting to break mode collapse.

See: Zhang et al., "Verbalized Sampling: How to Mitigate Mode Collapse
and Unlock LLM Diversity", ICLR 2026.
"""

import logging
import random
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VSConfig:
    k: int = 5
    sampling_mode: str = "full"  # "full" | "tails"


VS_RESPONSE_SCHEMA: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "vs_responses",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "responses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "probability": {"type": "number"},
                            "text": {"type": "string"},
                        },
                        "required": ["probability", "text"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["responses"],
            "additionalProperties": False,
        },
    },
}


# Per-function diversity hints (axes of variation the model should explore)
DIVERSITY_HINTS: dict[str, str] = {
    "mission": (
        "Vary across these axes:\n"
        "- Genre (diplomacy, combat, mystery, exploration, sabotage)\n"
        "- Tone (dark, heroic, absurd, tense, melancholic)\n"
        "- Scale (personal drama, ship crisis, galactic threat)\n"
    ),
    "game_title": (
        "Vary across these axes:\n"
        "- Style (metaphorical, technical, ironic, epic)\n"
        "- Length (short punchy, multi-word epic)\n"
    ),
    "turn_story": (
        "Vary across these axes:\n"
        "- Direction (escalation, de-escalation, revelation, character moment)\n"
        "- Pacing (fast action, slow burn, sudden twist)\n"
    ),
    "global_circumstances": (
        "Vary across these axes:\n"
        "- Threat type (external, internal, natural phenomenon, technogenic)\n"
        "- Scene mood (hopeful, tense, mysterious, catastrophic)\n"
        "- Location variety (ship interior, planet surface, space anomaly, station)\n"
    ),
    "combined_outcome": (
        "Vary across these axes:\n"
        "- Outcome (success, partial success, complication, unexpected twist)\n"
        "- Consequences (immediate danger, long-term implication, moral dilemma)\n"
        "- Tone shift (things get worse, silver lining, pyrrhic victory)\n"
    ),
    "player_message": (
        "Vary across these axes:\n"
        "- GM tone (serious, ironic, mysterious, encouraging, ominous)\n"
        "- Response length (terse and punchy, detailed and atmospheric)\n"
        "- Mood must reflect the current scene circumstances.\n"
    ),
    "npc_decision": (
        "Vary across these axes:\n"
        "- Decision style (rational, emotional, risky, cautious, self-serving)\n"
        "- Must reflect the current scene mood and circumstances.\n"
    ),
    "species_description": (
        "Vary across these axes:\n"
        "- Unusualness of appearance (subtle alien, radically non-humanoid)\n"
        "- Textures (crystalline, biological, metallic, energy-based)\n"
        "- Silhouette and body plan (bipedal, floating, amorphous, multi-limbed)\n"
    ),
    "npc_name": (
        "Vary across these axes:\n"
        "- Name style (technical designation, poetic, alien phonetics, functional title)\n"
    ),
    "avatar": (
        "Vary across these axes:\n"
        "- Body form (humanoid, alien, energy being, cybernetic, symbiotic)\n"
        "- Camera angle (portrait, 3/4, full body, dynamic pose)\n"
        "- Environment (ship interior, lab, planet surface, void)\n"
        "- Mood (stoic, intense, serene, alien, unsettling)\n"
        "CRITICAL: For non-human species, at least 3 of 5 options MUST be non-humanoid forms.\n"
    ),
    "npc_avatars": (
        "Vary across these axes:\n"
        "- Body form (humanoid, alien, energy being, cybernetic, symbiotic)\n"
        "- Species-to-species visual diversity — no two NPCs look similar\n"
        "- Camera angle, environment, mood as above\n"
    ),
    "action_prompt": (
        "Vary across these axes:\n"
        "- Composition (wide shot, close-up, Dutch angle, overhead)\n"
        "- Lighting (dramatic shadows, neon glow, harsh sun, bioluminescent)\n"
        "- Camera angle (eye-level, low angle heroic, high angle vulnerable)\n"
        "- Action dynamics (mid-motion freeze, before/after moment)\n"
    ),
    "bridge_image": (
        "Vary across these axes:\n"
        "- Crew arrangement (tight cluster, spread across stations, dramatic tableau)\n"
        "- Bridge lighting (alert red, calm blue, emergency flicker, nebula glow through viewport)\n"
        "- Overall mood (ready for action, tense standoff, routine calm, crisis)\n"
    ),
    "scene_prompt": (
        "Vary across these axes:\n"
        "- Color palette (cold blues, warm ambers, sickly greens, stark monochrome)\n"
        "- Atmosphere (fog, sparks, zero-g float, alien bioluminescence)\n"
        "- Scene scale (intimate close-up, expansive epic wide shot)\n"
    ),
}


def select_response(
    responses: list[dict],
    sampling_mode: str = "full",
) -> dict:
    """Weighted random selection from verbalized responses.

    Args:
        responses: List of {"probability": float, "text": str} dicts.
        sampling_mode: "full" (sample from all) or "tails" (only p < 0.10).

    Returns:
        The selected response dict.

    Raises:
        ValueError: If responses list is empty.
    """
    if not responses:
        raise ValueError("Cannot select from empty responses list")

    if len(responses) == 1:
        return responses[0]

    if sampling_mode == "tails":
        candidates = [r for r in responses if r["probability"] < 0.10]
        if not candidates:
            logger.warning("No tails candidates found (all p >= 0.10), falling back to full sampling")
            candidates = responses
        responses = candidates

    total = sum(r["probability"] for r in responses)
    if total <= 0:
        logger.warning("All probabilities are zero or negative, using uniform selection")
        return random.choice(responses)

    r = random.uniform(0, total)
    cumulative = 0.0
    for resp in responses:
        cumulative += resp["probability"]
        if r <= cumulative:
            return resp

    return responses[-1]
