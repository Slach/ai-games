"""Verbalized Sampling — inference-time prompting to break mode collapse.

See: Zhang et al., "Verbalized Sampling: How to Mitigate Mode Collapse
and Unlock LLM Diversity", ICLR 2026.
"""

import json
import logging
import random as _random
import re as _re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VSConfig:
    k: int = 5
    sampling_mode: str = "full"  # "full" | "tails"


# Per-endpoint k overrides: some endpoints produce very large responses,
# so we use fewer candidates to keep JSON parsable.
VS_K_OVERRIDES: dict[str, int] = {
    "combined_outcome": 3,
}


def repair_json(text: str) -> str:
    """Attempt to repair common LLM JSON errors: trailing commas, unescaped
    newlines in strings, truncated trailing content.

    Returns the repaired text if repairable, otherwise the original text.
    """
    original = text.strip()
    if not original:
        return original

    repaired = original

    # 1. Remove trailing text after the last balanced JSON container.
    repaired = _trim_to_last_json_root(repaired)

    # 2. Fix trailing commas before } or ]
    repaired = _re.sub(r",\s*(\}|\])", r"\1", repaired)

    # 3. Balance braces/brackets: if the JSON is truncated, close unclosed containers.
    open_braces = repaired.count("{") - repaired.count("}")
    open_brackets = repaired.count("[") - repaired.count("]")

    if open_braces > 0 or open_brackets > 0:
        last_char = repaired.rstrip()[-1:] if repaired.rstrip() else ""
        if last_char in (",", ":", "{", "[", '"', "e", "t", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"):
            repaired = repaired.rstrip().rstrip(",")
            for _ in range(open_brackets):
                repaired += "]"
            for _ in range(open_braces):
                repaired += "}"

    # 4. Fix unescaped newlines inside quoted string values.
    repaired = _fix_broken_strings(repaired)

    if repaired != original:
        logger.info("repair_json: applied repairs (%d bytes delta)", len(repaired) - len(original))

    return repaired


def _trim_to_last_json_root(text: str) -> str:
    """Trim trailing text after the last balanced JSON object or array."""
    # Find the last } or ] in the text — this is the root closer.
    root_close = -1
    for i in range(len(text) - 1, -1, -1):
        if text[i] in ("}", "]"):
            root_close = i
            break
    if root_close == -1:
        return text  # No JSON container found

    # Walk backward from root_close to find the matching opener.
    depth = 0
    for i in range(root_close, -1, -1):
        ch = text[i]
        if ch in ("}", "]"):
            depth += 1
        elif ch in ("{", "["):
            depth -= 1
        if depth == 0:
            # i is the root opener position
            return text[i : root_close + 1]
    return text


def _fix_broken_strings(text: str) -> str:
    """Try to fix string values where literal newlines broke the JSON structure."""
    lines = text.split("\n")
    result = []
    in_broken_string = False
    for line in lines:
        stripped = line.strip()
        if in_broken_string:
            if stripped.endswith('",') or stripped.endswith('"') or stripped == '"':
                result.append(line)
                in_broken_string = False
            else:
                escaped = line.replace("\t", "\\t")
                result.append(escaped)
        elif _starts_value_string(stripped) and not _ends_value_string(stripped):
            result.append(line)
            in_broken_string = True
        else:
            result.append(line)
    return "\n".join(result)


def _starts_value_string(line: str) -> bool:
    """Check if a line starts a JSON string value (like '"key": "value...')."""
    return bool(_re.match(r'^\s*"[^"]+"\s*:\s*"', line))


def _ends_value_string(line: str) -> bool:
    """Check if a line ends a JSON string value (like '...value",' or '...value"')."""
    return bool(_re.search(r'",?\s*$', line))


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


def vs_response_schema(inner_schema: dict) -> dict:
    """Wrap an inner response schema into a VS distribution schema.

    The inner schema becomes the type of the 'text' field, so the model
    outputs structured objects directly — no JSON-inside-string needed.
    """
    inner = inner_schema["json_schema"]["schema"]
    return {
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
                                "text": inner,
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
    "mission": ("Vary across these axes:\n- Genre (diplomacy, combat, mystery, exploration, sabotage)\n- Tone (dark, heroic, absurd, tense, melancholic)\n- Scale (personal drama, ship crisis, galactic threat)\n"),
    "game_title": ("Vary across these axes:\n- Style (metaphorical, technical, ironic, epic)\n- Length (short punchy, multi-word epic)\n"),
    "turn_story": ("Vary across these axes:\n- Direction (escalation, de-escalation, revelation, character moment)\n- Pacing (fast action, slow burn, sudden twist)\n"),
    "global_circumstances": (
        "Vary across these axes:\n- Threat type (external, internal, natural phenomenon, technogenic)\n- Scene mood (hopeful, tense, mysterious, catastrophic)\n- Location variety (ship interior, planet surface, space anomaly, station)\n"
    ),
    "combined_outcome": (
        "Vary across these axes:\n- Outcome (success, partial success, complication, unexpected twist)\n- Consequences (immediate danger, long-term implication, moral dilemma)\n- Tone shift (things get worse, silver lining, pyrrhic victory)\n"
    ),
    "player_message": ("Vary across these axes:\n- GM tone (serious, ironic, mysterious, encouraging, ominous)\n- Response length (terse and punchy, detailed and atmospheric)\n- Mood must reflect the current scene circumstances.\n"),
    "npc_decision": ("Vary across these axes:\n- Decision style (rational, emotional, risky, cautious, self-serving)\n- Must reflect the current scene mood and circumstances.\n"),
    "species_description": (
        "Vary across these axes:\n- Unusualness of appearance (subtle alien, radically non-humanoid)\n- Textures (crystalline, biological, metallic, energy-based)\n- Silhouette and body plan (bipedal, floating, amorphous, multi-limbed)\n"
    ),
    "npc_name": ("Vary across these axes:\n- Name style (technical designation, poetic, alien phonetics, functional title)\n"),
    "avatar": (
        "Vary across these axes:\n"
        "- Body form (humanoid, alien, energy being, cybernetic, symbiotic)\n"
        "- Camera angle (portrait, 3/4, full body, dynamic pose)\n"
        "- Environment (ship interior, lab, planet surface, void)\n"
        "- Mood (stoic, intense, serene, alien, unsettling)\n"
        "CRITICAL: For non-human species, at least 3 of 5 options MUST be non-humanoid forms.\n"
    ),
    "npc_avatars": ("Vary across these axes:\n- Body form (humanoid, alien, energy being, cybernetic, symbiotic)\n- Species-to-species visual diversity — no two NPCs look similar\n- Camera angle, environment, mood as above\n"),
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
    "scene_prompt": ("Vary across these axes:\n- Color palette (cold blues, warm ambers, sickly greens, stark monochrome)\n- Atmosphere (fog, sparks, zero-g float, alien bioluminescence)\n- Scene scale (intimate close-up, expansive epic wide shot)\n"),
}


def select_response(
    responses: list[dict],
    sampling_mode: str,
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
        return _random.choice(responses)

    r = _random.uniform(0, total)
    cumulative = 0.0
    for resp in responses:
        cumulative += resp["probability"]
        if r <= cumulative:
            return resp

    return responses[-1]


def verbalize_prompt(
    system_prompt: str,
    user_prompt: str,
    diversity_hint: str,
    k: int,
) -> tuple[str, str]:
    """Wrap instance-level prompt into distribution-level VS prompt.

    Args:
        system_prompt: Original system prompt.
        user_prompt: Original user prompt.
        diversity_hint: Hints for what axes to vary.
        k: Number of candidate responses to request.

    Returns:
        (modified_system_prompt, modified_user_prompt)
    """
    vs_system = (
        f"{system_prompt}\n\n"
        f"You are a creative generator using Verbalized Sampling. "
        f"For each request, output k={k} DIVERSE options with verbalized "
        f"probabilities. Each option must be meaningfully different — explore "
        f"the full distribution including likely, unlikely, and surprising options."
    )

    vs_user = (
        f"{user_prompt}\n\n"
        f"Generate {k} DIVERSE options for the above. Each option must be "
        f"meaningfully different from the others.\n"
        f"{diversity_hint}\n\n"
        f"For each option, assign a numeric probability (0.0-1.0) representing "
        f"how likely or appropriate this option is. Probabilities must sum to 1.0. "
        f"Include both high-probability (conventional) and low-probability "
        f"(creative, surprising) options.\n\n"
        f'Format: output as JSON with a "responses" array. Each entry has '
        f'"probability" (float) and "text" (the response object).'
    )

    return vs_system, vs_user


def vs_parse_json(text: str) -> dict:
    """Parse VS text field as JSON with graceful fallback.

    If the text is valid JSON, return the parsed dict.
    If it's not valid JSON, wrap it in {"raw": text} and log a warning.
    """
    try:
        result = json.loads(text)
        if not isinstance(result, dict):
            logger.warning("VS text parsed as non-dict %s, wrapping", type(result).__name__)
            return {"raw": text}
        return result
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("VS text is not valid JSON: %s, using raw fallback", e)
        return {"raw": text}


def vs_k_for(endpoint: str, default: int) -> int:
    """Return the VS k value for a given endpoint, using overrides if present."""
    return VS_K_OVERRIDES.get(endpoint, default)
